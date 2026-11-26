"""第 2 步：建索引模板 + Data View + ILM 策略。

跑完这一步，Kibana 里就有 app-logs 数据视图了。
"""
import sys

from common import DATA_VIEW, ES_URL, INDEX_PATTERN, KIBANA_URL, es, kb, wait_ready

MAPPINGS = {
    "index_patterns": [INDEX_PATTERN],
    "template": {
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "service": {"type": "keyword"},
                "env": {"type": "keyword"},
                "level": {"type": "keyword"},
                "logger": {"type": "keyword"},
                "method": {"type": "keyword"},
                "path": {"type": "keyword"},
                "status": {"type": "integer"},
                "latency_ms": {"type": "float"},
                "trace_id": {"type": "keyword"},
                "user_id": {"type": "keyword"},
                "case_id": {"type": "keyword"},
                "message": {"type": "text", "fields": {"raw": {"type": "keyword", "ignore_above": 512}}},
                "error": {"properties": {"type": {"type": "keyword"},
                                          "stack": {"type": "text"}}},
            }
        },
    },
}

ILM = {
    "policy": {
        "phases": {
            "hot": {"actions": {"rollover": {"max_primary_shard_size": "5gb", "max_age": "1d"}}},
            "delete": {"min_age": "7d", "actions": {"delete": {}}},
        }
    }
}


def main():
    print(f"→ 等 Elasticsearch: {ES_URL}")
    wait_ready(ES_URL)
    info = es("GET", "/")
    print(f"  集群 {info['cluster_name']} / 版本 {info['version']['number']}")

    es("PUT", "_ilm/policy/app-logs-policy", ILM)
    print("✓ ILM 策略 app-logs-policy（1 天 rollover，7 天删除）")

    es("PUT", "_index_template/app-logs-template", MAPPINGS)
    print(f"✓ 索引模板 app-logs-template（匹配 {INDEX_PATTERN}）")

    print(f"→ 等 Kibana: {KIBANA_URL}（首次启动要 1~2 分钟）")
    wait_ready(KIBANA_URL)
    try:
        kb("POST", "api/data_views/data_view", {
            "data_view": {"title": INDEX_PATTERN, "name": DATA_VIEW, "timeFieldName": "@timestamp"},
            "override": True,
        })
        print(f"✓ Kibana Data View: {DATA_VIEW}")
    except RuntimeError as e:
        if "exists" in str(e).lower():
            print("· Data View 已存在，跳过")
        else:
            print(f"! Data View 创建失败（不影响造数据）: {e}")

    print("\n下一步：python3 gen_logs.py")


if __name__ == "__main__":
    sys.exit(main())
