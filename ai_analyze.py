"""第 5 步：AI 根因分析。ES 出证据，LLM 出结论。

流程（这就是 AIOps 的最小闭环）：
  1. 统计基线：最近 15 分钟 vs 之前 15 分钟，按服务算错误率/延迟变化
  2. 定位异常服务 + 异常起始时间（5 分钟桶直方图）
  3. 采样真实错误日志（含堆栈）
  4. 把「结构化证据 + 原始样本」丢给 LLM，产出 RCA 报告

没配 LLM_API_KEY 也能跑：只出规则版结论，方便你先验证链路。
用法：python3 ai_analyze.py [--offline]
"""
import argparse
import datetime as dt
import json
import os
import pathlib

import requests

from common import ES_URL, es, wait_ready

REPORT_DIR = pathlib.Path("reports")
SERVICES_AGG = {
    "size": 0,
    "aggs": {
        "by_service": {
            "terms": {"field": "service", "size": 20},
            "aggs": {
                "err": {"filter": {"term": {"level": "ERROR"}}},
                "p95": {"percentiles": {"field": "latency_ms", "percents": [50, 95]}},
            },
        }
    },
}


def fetch_evidence():
    # 当前窗口取最近 30 分钟（两个埋点都在里面），基线取再往前 30 分钟
    now_q = {"range": {"@timestamp": {"gte": "now-30m"}}}
    base_q = {"range": {"@timestamp": {"gte": "now-60m", "lt": "now-30m"}}}

    now = es("POST", "app-logs-*/_search",
             {"size": 0, "query": {"bool": {"filter": [now_q]}}, "aggs": SERVICES_AGG["aggs"]})
    base = es("POST", "app-logs-*/_search",
              {"size": 0, "query": {"bool": {"filter": [base_q]}}, "aggs": SERVICES_AGG["aggs"]})

    def flatten(res):
        out = {}
        for b in res["aggregations"]["by_service"]["buckets"]:
            total = b["doc_count"] or 1
            out[b["key"]] = {
                "总日志": b["doc_count"],
                "错误数": b["err"]["doc_count"],
                "错误率%": round(b["err"]["doc_count"] / total * 100, 1),
                "p50_ms": round(b["p95"]["values"]["50.0"] or 0, 1),
                "p95_ms": round(b["p95"]["values"]["95.0"] or 0, 1),
            }
        return out

    cur, ref = flatten(now), flatten(base)

    timeline = es("POST", "app-logs-*/_search", {
        "size": 0,
        "query": {"bool": {"filter": [{"range": {"@timestamp": {"gte": "now-60m"}}},
                                      {"term": {"level": "ERROR"}}]}},
        "aggs": {"per_5m": {"date_histogram": {"field": "@timestamp", "fixed_interval": "5m"},
                           "aggs": {"svc": {"terms": {"field": "service", "size": 5}}}}},
    })
    buckets = []
    for b in timeline["aggregations"]["per_5m"]["buckets"]:
        if b["doc_count"]:
            buckets.append({
                "时间": b["key_as_string"][11:16],
                "错误总数": b["doc_count"],
                "分布": {s["key"]: s["doc_count"] for s in b["svc"]["buckets"]},
            })

    samples = es("POST", "app-logs-*/_search", {
        "size": 12,
        "_source": ["@timestamp", "service", "path", "status", "latency_ms", "message", "error", "trace_id"],
        "query": {"bool": {"filter": [{"term": {"level": "ERROR"}}, {"range": {"@timestamp": {"gte": "now-30m"}}}]}},
        "sort": [{"@timestamp": "desc"}],
    })["hits"]["hits"]

    return cur, ref, buckets, [h["_source"] for h in samples]


def suspicion(cur, ref):
    """规则版可疑度：只给方向，判断交给 LLM。"""
    rows = []
    for svc, c in cur.items():
        b = ref.get(svc, {})
        err_delta = c["错误率%"] - b.get("错误率%", 0)
        p95_ratio = (c["p95_ms"] / b["p95_ms"]) if b.get("p95_ms") else 1
        score = err_delta * 2 + max(0, p95_ratio - 1) * 20
        if score > 5:
            rows.append({"服务": svc, "错误率变化": f"{b.get('错误率%', 0)}% → {c['错误率%']}%",
                         "p95变化": f"{b.get('p95_ms', 0)}ms → {c['p95_ms']}ms",
                         "可疑分": round(score, 1)})
    return sorted(rows, key=lambda r: -r["可疑分"])


PROMPT = """你是资深 SRE + 测试架构师。下面是某个系统最近 60 分钟的可观测性证据，请做根因分析。

【当前 30 分钟 vs 之前 30 分钟 指标对比】
{metrics}

【最近 60 分钟错误时间线（5 分钟桶）】
{timeline}

【真实错误日志样本】
{samples}

请严格按下面结构用中文输出 Markdown，不要客套话：
## 1. 一句话结论
## 2. 现象与时间线（谁、什么时候、什么表现）
## 3. 根因假设（按可能性排序，每条给置信度和支持证据；证据不足要明说）
## 4. 验证步骤（3 条以内，给出可直接执行的 ES 查询或命令）
## 5. 对测试的启示（这类故障该补哪些用例/断言/压测场景）
"""


def call_llm(metrics, timeline, samples):
    base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    key = os.getenv("LLM_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "deepseek-chat")
    if not key:
        return None
    prompt = PROMPT.format(
        metrics=json.dumps(metrics, ensure_ascii=False, indent=1),
        timeline=json.dumps(timeline, ensure_ascii=False, indent=1),
        samples=json.dumps(samples, ensure_ascii=False, indent=1),
    )
    r = requests.post(f"{base}/chat/completions",
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"model": model, "temperature": 0.2,
                            "messages": [{"role": "user", "content": prompt}]},
                      timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"], model


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="只跑规则版，不调 LLM")
    args = ap.parse_args()

    wait_ready(ES_URL)
    cur, ref, buckets, samples = fetch_evidence()
    susp = suspicion(cur, ref)
    if not susp:
        print("没发现明显异常。先跑 python3 gen_logs.py 造数据，或把窗口改大一点。")
        return

    lines = [f"# 日志根因分析报告\n",
             f"- 生成时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}",
             f"- 数据源：{ES_URL} · app-logs-* · 最近 30 分钟\n",
             "## 规则版可疑服务\n",
             "| 服务 | 错误率变化 | p95 变化 | 可疑分 |", "|---|---|---|---|"]
    for s in susp:
        lines.append(f"| {s['服务']} | {s['错误率变化']} | {s['p95变化']} | {s['可疑分']} |")

    ai = None
    if not args.offline:
        try:
            ai = call_llm({"当前": cur, "基线": ref}, buckets, samples)
        except Exception as e:  # noqa: BLE001
            print(f"! LLM 调用失败（继续出规则版报告）：{e}")

    if ai:
        lines += ["\n---\n", f"# AI 分析（模型 {ai[1]}）\n", ai[0]]
    else:
        lines.append("\n> 未启用 AI 分析。在 .env 里配 LLM_API_KEY 后重跑即可。")

    if not REPORT_DIR.exists():
        REPORT_DIR.mkdir(parents=True)
    path = REPORT_DIR / f"rca-{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    path.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines[:14]))
    print(f"\n✓ 完整报告：{path}")


if __name__ == "__main__":
    main()
