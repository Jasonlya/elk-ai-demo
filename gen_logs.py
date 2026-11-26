"""第 3 步：造 1 小时模拟日志并灌进 ES，故意埋两个异常。

埋点：
  ① now-27m ~ now-17m  order-service 500 暴增（错误率 ~45%）
  ② now-8m  ~ now-5m    payment-service 延迟飙升（p95 从 180ms → 4000ms）

用法：
  python3 gen_logs.py            # 默认 60 分钟历史
  python3 gen_logs.py --minutes 180 --rate 200
"""
import argparse
import datetime as dt
import json
import random
import uuid

from common import ES_URL, es, es_bulk, wait_ready

SERVICES = {
    "api-gateway": ["/api/v1/order", "/api/v1/user", "/api/v1/pay", "/api/v1/search"],
    "order-service": ["/api/v1/order", "/api/v1/order/confirm"],
    "user-service": ["/api/v1/user", "/api/v1/user/login"],
    "payment-service": ["/api/v1/pay", "/api/v1/pay/refund"],
}
ERR_TYPES = ["TimeoutError", "DBConnectionPoolExhausted", "NullPointerException",
             "UpstreamUnavailable", "RateLimitExceeded"]

ANOMALY_ERR = ("order-service", 27, 17)      # 服务, 开始分钟前, 结束分钟前
ANOMALY_LAT = ("payment-service", 8, 5)


def in_window(minutes_ago, win):
    """win = (service, 开始分钟前, 结束分钟前)"""
    return win[1] >= minutes_ago >= win[2]


def make_log(ts, minutes_ago):
    service = random.choices(list(SERVICES), weights=[40, 30, 20, 10])[0]
    path = random.choice(SERVICES[service])
    level, status = "INFO", 200
    latency = round(random.gauss(90, 35) + random.random() * 60, 1)
    latency = max(8.0, latency)
    error = None

    if service == ANOMALY_ERR[0] and in_window(minutes_ago, ANOMALY_ERR) and random.random() < 0.45:
        level, status = "ERROR", 500
        error = {"type": random.choice(["DBConnectionPoolExhausted", "TimeoutError"]),
                 "stack": "at com.demo.order.OrderRepo.save(OrderRepo.java:88)\n"
                          "Caused by: java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available"}
        latency = round(random.uniform(1200, 3000), 1)

    if service == ANOMALY_LAT[0] and in_window(minutes_ago, ANOMALY_LAT):
        latency = round(random.uniform(3000, 5200), 1)
        if latency > 4000:
            level, status = "WARN", 200

    if random.random() < 0.012:   # 背景噪音
        level, status = "WARN", random.choice([200, 429])

    msg = f"{service} {path} -> {status} in {latency}ms"
    if error:
        msg = f"{service} {path} failed: {error['type']}"

    return {
        "@timestamp": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "service": service,
        "env": "demo",
        "level": level,
        "logger": f"com.demo.{service.replace('-', '.')}.Handler",
        "method": random.choice(["GET", "POST", "POST", "PUT"]),
        "path": path,
        "status": status,
        "latency_ms": latency,
        "trace_id": uuid.uuid4().hex[:16],
        "user_id": f"u{random.randint(1000, 9999)}",
        "case_id": f"TC-{random.randint(1, 300):03d}",
        "message": msg,
        **({"error": error} if error else {}),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--rate", type=int, default=130, help="每分钟日志条数")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--reset", action="store_true", help="先删掉已有 app-logs-* 索引")
    args = ap.parse_args()

    if not args.no_wait:
        wait_ready(ES_URL)

    if args.reset:
        # 8.x 默认禁止按通配符删除（destructive_requires_name），先列出再点名删
        idx = es("GET", "_cat/indices/app-logs-*?h=index&format=json")
        names = [i["index"] for i in idx] if isinstance(idx, list) else []
        if names:
            es("DELETE", ",".join(names))
        print(f"· 已清空 {len(names)} 个旧索引")

    now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
    total, buf = 0, []

    for m in range(args.minutes, -1, -1):
        minute_start = now - dt.timedelta(minutes=m)
        # 高峰/低谷，让图有起伏（按本地时区判断白天黑夜）
        local_hour = minute_start.astimezone().hour
        rate = int(args.rate * (1.6 if 9 <= local_hour <= 22 else 0.5))
        for _ in range(rate):
            ts = minute_start + dt.timedelta(seconds=random.uniform(0, 59))
            doc = make_log(ts, m)
            index = f"app-logs-{ts.strftime('%Y.%m.%d')}"
            buf.append(json.dumps({"index": {"_index": index}}))
            buf.append(json.dumps(doc, ensure_ascii=False))
            total += 1
        if len(buf) >= 4000:
            es_bulk("\n".join(buf) + "\n")
            buf.clear()

    if buf:
        es_bulk("\n".join(buf) + "\n")

    es("POST", "app-logs-*/_refresh")
    print(f"✓ 写入 {total} 条日志，覆盖最近 {args.minutes} 分钟")
    print("  已埋两个异常：order-service 500 暴增 / payment-service 延迟飙升")
    print("\n下一步：python3 ai_analyze.py")


if __name__ == "__main__":
    main()
