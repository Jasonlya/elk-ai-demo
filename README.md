# ELK + AI 最小可跑 Demo

> 📖 完整的环境安装与使用说明（含 macOS 踩坑记录、Kibana 实操、AI 层三种接法）见
> **[ELK-AI-环境安装与使用指南.md](./ELK-AI-环境安装与使用指南.md)**。
> 本文是快速上手版，适合已经熟悉 Docker 的人。

一套 30 分钟能跑通、覆盖「采集 → 存储 → 可视化 → AI 根因分析」全链路的练习环境。
数据是造出来的（含两个故意埋的故障），不依赖任何真实业务系统。

```
elk-ai-demo/
├── docker-compose.yml       ES 8.15 + Kibana（Logstash 可选 profile）
├── common.py                环境变量 + 请求封装
├── setup_es.py              索引模板 / ILM / Data View
├── gen_logs.py              造 1 小时日志，埋 2 个异常
├── ai_analyze.py            ES 出证据 + LLM 出 RCA 报告
└── logstash/pipeline/       可选：练采集链路
```

---

## 第 0 步 · 选环境（两条路线，二选一）

### 路线 A：Elastic Cloud 试用（可选，零本地开销）

本地环境已经装好了，这条当备用：如果你想练 8.x 的 ML / ELSER 而本地 VM 内存不够时走这条。
云上试用 14 天，零本地开销。

1. 打开 https://cloud.elastic.co/registration ，14 天试用，建一个 Deployment
2. 拿到 Elasticsearch 地址 + API Key，`cp .env.example .env` 后填：

```bash
ES_URL=https://xxx.es.us-east-1.aws.elastic.cloud:443
KIBANA_URL=https://xxx.kb.us-east-1.aws.elastic.cloud:443
ES_API_KEY=<你的 base64 api key>
KIBANA_API_KEY=<同一个 key>
```

### 路线 B：本地跑（docker compose）— 本机已装好，直接用

本机 Docker 是 **colima + 官方 CLI 二进制**（不是 Docker Desktop），装在 `~/opt`，比 Docker Desktop 省 1GB+ 内存：

| 组件 | 版本 | 位置 |
|---|---|---|
| docker CLI | 29.10.1 | `~/opt/bin/docker` |
| docker compose | 5.8.0 | `~/opt/bin/docker-compose`（已链接到 `~/.docker/cli-plugins/`） |
| colima | 0.10.3 | `~/opt/bin/colima` |
| lima | 2.3.0 | `~/opt/bin/limactl` |
| VM 镜像 | Ubuntu 24.04 arm64 | `~/opt/colima/ubuntu-arm64-docker.raw.gz`（317MB，SHA512 已校验） |

`~/.zshrc` 里已加了 `export PATH="$HOME/opt/bin:$PATH"` 和 `DOCKER_HOST`。

日常启停：

```bash
colima start            # 开机后先起 VM（约 60s）
colima status           # 看状态
docker compose up -d    # 在 elk-ai-demo/ 下起 ES + Kibana
docker compose down     # 停容器，数据卷保留
docker compose down -v  # 连数据一起删
colima stop             # 不用时停 VM，彻底释放 4GB 内存
```

**这台机器的两个特殊之处**（重装时会遇到，记一下）：

1. `brew install` 会失败：`sandbox-exec: sandbox_apply: Operation not permitted`。
   Homebrew 7 检测到自己在沙箱里，但默认前缀下拒绝跳过沙箱。
   → 解法：`brew fetch <包>` 只下 bottle，再 `tar -xzf $(brew --cache <包>) -C ~/opt/brew/Cellar` 手动解压。
2. `github.com` 在这个网络下连不通（`CONNECT tunnel failed, response 502`），
   所以 colima 自动下载镜像必然失败。
   → 解法：用 `https://gh-proxy.com/<github原始URL>` 下载，再 `colima start --disk-image <本地.gz>`。
   注意 `--disk-image` 要传**压缩包 .gz**，传解压后的 .raw 会 SHA512 校验不通过。

> 内存紧的时候：只留 ES 不开 Kibana（`docker compose up -d elasticsearch`），
> 用 `curl` 做查询，Kibana 那步先跳过。ES 堆只给了 1GB，实测 2 万条日志毫无压力。

---

## 第 1 步 · 建索引结构（学：mapping 与模板）

```bash
python3 -m pip install -r requirements.txt
python3 setup_es.py
```

**这一步要记住的**：`service`、`level`、`status` 全部是 `keyword`/数值类型，
不是 text。日志里凡是会被用来**聚合、过滤、画图**的字段必须是 keyword，
只有真正要全文检索的 `message` 才用 text —— 这是 ELK 新手第一个坑。

---

## 第 2 步 · 灌数据（学：文档结构与异常形态）

```bash
python3 gen_logs.py                  # 60 分钟 · 约 9000 条
python3 gen_logs.py --minutes 180 --rate 300   # 想练大数据量时
```

埋在数据里的两个故障，是后面 AI 分析的答案：

| 时间 | 服务 | 表现 |
|---|---|---|
| now-27m ~ now-17m | order-service | 45% 请求 500，堆栈 `HikariPool 连接池耗尽` |
| now-8m ~ now-5m | payment-service | p95 从 180ms → 4000ms+，无 5xx |

---

## 第 3 步 · Kibana 里把「现象」看出来（学：Kibana 四件套）

打开 http://localhost:5601 或云上的 Kibana 地址。

1. **Discover**：选 `app-logs` 数据视图，时间范围 `Last 1 hour`。
   顶部加过滤器 `level: ERROR`，看是不是集中在 order-service。
2. **字段统计**（左侧栏点字段 → Visualize）：`service`、`status`、`latency_ms` 各看一遍分布。
3. **Dashboard**：建 4 个图，这就是一套线上监控面板的最小版：
   - 折线：`@timestamp` 每 5 分钟 × 文档数（日志量趋势）
   - 柱状：`service` × 错误数（哪个服务在冒烟）
   - 折线：`latency_ms` 的 p95（延迟劣化）
   - 饼/表：`status` 分布
4. **告警**： stack 管理 → Rules → Create rule → **Index threshold**，
   条件「`level:ERROR` 在 5 分钟内 > 50 条」→ 触发后写日志动作。
   （Basic 版权限可用；如果被拦住，就用 cron + `ai_analyze.py` 兜底。）

> 练习重点：**先看见现象，再谈 AI**。AI 只是把"看图找规律"这一步自动化，
> 看不懂图的人调不出好 prompt。

---

## 第 4 步 · 自己写查询（学：DSL 与 ES|QL）

Kibana → Dev Tools 里跑，这是 ELK 的核心手感：

```json
GET app-logs-*/_search
{
  "size": 0,
  "query": { "bool": { "filter": [
    { "term":  { "level": "ERROR" } },
    { "range": { "@timestamp": { "gte": "now-30m" } } }
  ]}},
  "aggs": {
    "by_service": {
      "terms": { "field": "service" },
      "aggs": { "p95": { "percentiles": { "field": "latency_ms", "percents": [95] } } }
    }
  }
}
```

ES|QL 版本（8.11+，更接近 SQL，推荐顺手学）：

```
FROM app-logs-*
| WHERE @timestamp > NOW() - 30 minutes AND level == "ERROR"
| STATS errors = COUNT(*) BY service, status
| SORT errors DESC
```

两条都要能写出来 —— 面试和真实排障里，**一次查不准**的人基本做不了 AIOps。

---

## 第 5 步 · 接 AI（三条路线，按难度递进）

### 5.1 外部 LLM 做 RCA（本仓库已实现，先跑这个）

```bash
cp .env.example .env
# 填 LLM_API_KEY（DeepSeek / 通义 / 任意 OpenAI 兼容端点，或本地 Ollama）
python3 ai_analyze.py
```

它做的事，就是 AIOps 的最小闭环：

```
基线对比(now-15m vs 前15m) → 定位异常服务 → 取错误时间线 → 采样堆栈 → LLM 出结论
```

报告落在 `reports/rca-*.md`，会给出根因假设、验证查询、以及**该补哪些测试用例**。
对照一下：LLM 能不能自己指出 `HikariPool 连接池耗尽` 才是真因，而不是骂 order-service？

> 没配 key 也能 `python3 ai_analyze.py --offline` 跑规则版，先验证链路通不通。

**本机实测输出**（2.18 万条日志，`--minutes 90 --rate 150`）：

| 服务 | 错误率变化 | p95 变化 | 可疑分 |
|---|---|---|---|
| payment-service | 0.0% → 0.0% | 187.8ms → 4402.2ms | 448.8 |
| order-service | 0.0% → 17.0% | 184.7ms → 2400.1ms | 273.9 |

另外两个服务（api-gateway / user-service）**没有出现**，因为它们确实没异常。
这个对比值得琢磨：payment-service 可疑分最高，但它一条 ERROR 都没有 ——
**光看错误日志会漏掉它**。这正是纯关键词告警的盲区，也是 AIOps 要解决的问题。

### 5.2 Elastic 原生 AI（学：inference endpoint + 语义检索）

在 Kibana → **Playground** 里：
1. 连接模型（Elastic 托管的 `ELSER` 或接 OpenAI/ Bedrock connector）
2. 上传一份《接口规范 / 故障手册》当知识库
3. 提问「order-service 出现连接池耗尽时该看哪几个指标」→ 它检索文档 + 拼上下文回答

这就是 RAG。跑通后你会理解：**向量检索解决"查不到关键词"的问题，BM25 解决"查得准"的问题**，
生产里通常是 hybrid。

### 5.3 让 Agent 自己查 ES（进阶，和你手上的 Agent 项目最贴）

把 `ai_analyze.py` 的固定查询改成「工具」：给 LLM 两个 function —
`es_search(query_dsl)` 和 `es_query(sql)`，让它自己决定查什么、查几次、什么时候收敛。
这是从「一次性 prompt」到「Agent 排障」的分界线，也是你 C2D / Agent 评测那套经验的用武之地。

---

## 第 6 步 · 加采集链路（可选，学 Filebeat/Logstash）

```bash
docker compose --profile collect up -d logstash
python3 -c "import json;print(json.dumps({'@timestamp':'2026-11-26T10:00:00Z','service':'order-service','level':'ERROR','status':500,'latency_ms':2100,'message':'pool exhausted'}))" > logs/app.log
```

Logstash 会把 `logs/*.log` 读进来，加 `env`、`http_5xx` 标签再写 ES。
观察点：**同一份日志，走 Logstash 和直接 bulk 写，字段有什么差别**（`@metadata`、`tags` 的去留）。

---

## 常见坑

| 现象 | 原因 / 解法 |
|---|---|
| 容器起不来，ES exit code 78 | `vm.max_map_count` 太小；colima 下执行 `colima ssh -- sudo sysctl -w vm.max_map_count=262144` |
| Kibana 一直 `not ready` | 它是等 ES healthy 才启动的，`docker compose logs -f kibana` 看是不是连不上 ES |
| bulk 报 `mapper_parsing_exception` | keyword 字段写了数字，或 `@timestamp` 格式不对（要 ISO8601 带 Z） |
| 报告里"没发现明显异常" | 数据窗口过了 30 分钟就滑走了，重跑 `gen_logs.py` 再造一遍 |
| Mac 风扇狂转 / 系统卡 | ES 堆降到 `-Xms512m`，或直接走 Elastic Cloud 路线 |

---

## 练完之后你应该能回答

1. 为什么日志字段要区分 text 和 keyword？分别用在哪
2. 一次 500 暴增，用 Kibana 怎么在 2 分钟内定位到服务、时间点、错误类型
3. p95 涨了但错误率没涨，是哪种故障形态（提示：慢查询 / 下游抖动）
4. 给 LLM 的证据里，**哪些必须给、哪些给了反而误导**（提示：全量日志 → 噪声淹没真因）
5. AIOps 的边界在哪：什么它能判，什么必须人来看
