# ELK + AI 最小可跑 Demo

> 📖 完整的环境安装与使用说明（含 macOS 踩坑记录、Kibana 实操、AI 层三种接法）见
> **[ELK-AI-环境安装与使用指南.md](./ELK-AI-环境安装与使用指南.md)**。
> 本文含**组件说明 + 安装步骤 + 六步实操**，零基础可跟；想深入原理的看上面那份。

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

## 组件地图：五个工具各管什么、怎么装

先建立整体印象，再动手。这套 demo 用到的东西其实只有 5 个，每个职责都很清楚。

### 一张表看完

| 工具 | 一句话概念 | 在本 demo 中的作用 | 端口 | 必须吗 |
|---|---|---|---|---|
| **Docker Engine** | 容器运行时，把应用和依赖打包成镜像隔离运行 | 承载 ES / Kibana / Logstash，一条 compose 全拉起 | — | 必须 |
| **colima + lima** | macOS 上的 Docker 虚拟机（Docker Desktop 的替代品） | 在 Mac 上提供 docker daemon | — | 仅 macOS |
| **Elasticsearch** | 基于 Lucene 的分布式搜索与分析引擎 | 日志的存储层与查询层，所有检索都打给它 | 9200 | 必须 |
| **Kibana** | ES 的 Web 前端，自己不存数据 | 看日志、画图、写查询、配告警、接 LLM | 5601 | 强烈建议 |
| **Logstash** | 采集与转换管道（input → filter → output） | 练「日志怎么从文件流进 ES」这条链路 | 5044 / 5000 | 可选 |
| **Python 3 + requests** | 脚本语言，只依赖一个 HTTP 库 | 建索引、造数据、调 AI 出报告 | — | 必须 |
| **LLM**（DeepSeek / Ollama） | 大模型推理端点 | 把结构化证据翻译成根因分析报告 | — | 可选 |

数据怎么流：

```
gen_logs.py ──bulk 写入──▶  Elasticsearch :9200  ◀──搜索──  ai_analyze.py ──▶ LLM ──▶ reports/*.md
                                ▲      │
               Kibana :5601 ────┘      └──── Logstash :5044（可选，从 logs/*.log 采集）
```

**关键认知**：ES 是唯一存数据的地方，其余都是它的「输入管道」或「输出界面」。所以排查问题时，
只要 ES 里的数据是对的，Kibana 图不对、AI 结论不对，都是上层的事。

---

### 1. Docker Engine —— 容器运行时

**概念**：把应用连同依赖（JDK、配置、系统库）一起打包成镜像，运行时用命名空间隔离，做到「一次构建，处处运行」。

**为什么这套 demo 必须用它**：ELK 三个组件都是 JVM 应用，手工安装要配 JDK、建 `elasticsearch` 系统用户、
调 `vm.max_map_count`、改一堆 yml。用容器这些全都被镜像封装好了，`docker compose up -d` 一行搞定。
**不是必须用 Docker，但不用它你会把 90% 时间花在环境上，而不是学 ELK。**

| 系统 | 安装方式 |
|---|---|
| macOS | 不装 Docker Desktop，用 colima（见下一条，省 1GB+ 内存） |
| Linux | `curl -fsSL https://get.docker.com \| sh` 然后 `sudo usermod -aG docker $USER` |
| Windows | 装 WSL2 + Docker Desktop，项目放 WSL 文件系统里（放 Windows 盘会慢十倍） |

验证：`docker version` 能看到 `Server:` 段（不只是 `Client:`），说明 daemon 通了。

---

### 2. colima + lima —— 仅 macOS 需要

**概念**：macOS 内核跑不了 Linux 容器，必须开一台 Linux 虚拟机。**lima** 负责用 Apple Virtualization.framework
起轻量 VM，**colima** 在它之上把 Docker 运行时、端口转发、socket 挂载都封装好，对外表现得像原生 docker。

**作用**：提供 `docker` 命令背后的 daemon。相比 Docker Desktop，它没有 GUI 和 Kubernetes，
空闲内存少 1GB 以上——对内存长期吃紧的机器很关键。

```bash
brew install colima docker docker-compose
colima start --cpu 2 --memory 4 --disk 30    # 首次会下载 Ubuntu 镜像
docker version                                # 出现 Server 段即成功
colima status                                 # 看 VM 状态
```

日常习惯：**用完 `colima stop`**，这 4GB 会立刻还给系统；下次 `colima start` 约 60 秒。

> 本机安装时踩过两个坑（brew 沙箱报错、GitHub 不通导致镜像下不来），
> 手动二进制方案写在指南 **2.2 节**，重装时可直接照抄。

---

### 3. Elasticsearch —— 存储与查询核心

**概念**：面向文档的搜索引擎。数据以 JSON 文档存入，字段被建倒排索引，所以能做「毫秒级全文检索 + 聚合统计」。
它同时是个分布式系统（分片、副本、选主），但单节点也能跑，本 demo 就是单节点。

**在本 demo 中**：所有日志都写进索引 `app-logs-*`。你后面写的 DSL 查询、ES|QL、Kibana 的每张图、
`ai_analyze.py` 取证据，全部是在向它发请求。

**安装**：不需要单独装，它就是 compose 的第一个服务。配置在 `docker-compose.yml` 里，几个关键项：

| 配置 | 值 | 为什么 |
|---|---|---|
| `discovery.type` | `single-node` | 单节点，跳过集群选主 |
| `xpack.security.enabled` | `false` | demo 免密；**生产绝对不能这样** |
| `ES_JAVA_OPTS` | `-Xms1g -Xmx1g` | 堆固定 1GB，够 10 万条日志；调大很吃内存 |
| `bootstrap.memory_lock` | `true` | 锁内存防止被换出，避免查询抖动 |

```bash
docker compose up -d elasticsearch
curl -s localhost:9200 | head -5    # 返回 version.number = 8.15.0 就算起来了
```

**它实际吃多少内存**：1GB 堆 + 约 700MB 堆外开销 ≈ 1.6GB。这是全 demo 最占资源的部分。

---

### 4. Kibana —— 可视化前端

**概念**：跑在 Node.js 上的 Web 应用，通过 HTTP 调 ES 的 API 取数，**自身不存储任何日志**。
它的价值在于把「写查询」变成「点鼠标」，把「结果」变成「图」。

**在本 demo 中四个用途**（对应第 3、4 步）：

| 功能 | 用途 |
|---|---|
| **Discover** | 逐条看原始日志，加过滤器缩小范围——排障第一步 |
| **Dashboard** | 四张图搭出最小监控面板：日志量趋势 / 错误数 / p95 / status 分布 |
| **Dev Tools** | 交互式写 DSL 和 ES\|QL，比用 curl 试错效率高得多 |
| **Rules** | 配阈值告警（如 5 分钟内 ERROR > 50 条） |

**安装**：同样是 compose 服务，`depends_on: condition: service_healthy` 保证它等 ES 健康后才启动
（去掉这行它会因为连不上 ES 而反复重启）。

```bash
docker compose up -d kibana
open http://localhost:5601        # 首次加载约 30~60s，别急着刷新
```

**它实际吃多少内存**：约 600MB。内存紧张时可以只开 ES，Kibana 那步用 curl 代替。

---

### 5. Logstash —— 采集管道（可选）

**概念**：典型的三段式管道 `input → filter → output`。input 收数据（文件、beats、TCP、Kafka），
filter 做解析改写（grok、date、mutate），output 发出去（ES、Kafka、S3）。

**在本 demo 中**：它是可选件，用来练「日志如何从服务器文件流进 ES」这条真实链路。
生产里常见形态是 `Filebeat（轻量采集）→ Logstash（重加工）→ ES`。

**不用它的代价 / 用它的代价**：不用它也能拿数据（`gen_logs.py` 直接 bulk 写），但你会缺失对采集层的理解；
用它则要多付约 500MB 内存（JVM 应用，堆给了 256m；此数字为按堆推算的估计值，本机未实测）。

```bash
mkdir -p logs                      # 先建目录，compose 挂载它，不存在会报错
echo '{"@timestamp":"2026-11-26T10:00:00Z","service":"order-service","level":"ERROR","status":500,"latency_ms":2100,"message":"pool exhausted"}' > logs/app.log
docker compose --profile collect up -d logstash   # 带了 profile；不加 --profile 它完全不启动
docker compose logs -f logstash                    # 看 pipeline 是否加载成功
```

**观察重点**：同一份日志走 Logstash 和直接 bulk 写，进 ES 后的字段是有差别的——多出 `tags`、`env` 之类的加工字段，
`@metadata` 会被丢掉。这个对比是理解采集层最直观的方式。

> ⚠️ 这一步是**唯一没能本机实测**的环节：本机 VM 的 docker 代理端口失效，Logstash 镜像拉不下来
> （ES / Kibana 镜像是代理还活着时拉的，所以能用）。命令和 `logstash/pipeline/demo.conf` 已核对过语法，
> 但你第一次跑时如果卡在拉镜像，先看文末「关于 colima 里的代理」。

---

### 6. Python 脚本层

**作用**：三个脚本构成 demo 的「操作台」，全部只依赖 `requests` 一个库，不需要装 ES 客户端 SDK。

| 脚本 | 干什么 |
|---|---|
| `setup_es.py` | 建索引模板（定义字段类型）、ILM 策略、Kibana Data View |
| `gen_logs.py` | 造 90 分钟日志灌进 ES，内含 2 个埋好的故障 |
| `ai_analyze.py` | 从 ES 取证据 → 调 LLM → 输出 RCA 报告 |

```bash
python3 -m venv .venv && source .venv/bin/activate   # 建议，避免污染系统环境
python3 -m pip install -r requirements.txt
python3 setup_es.py
```

> 版本要求宽松：Python 3.8+ 即可（本机实测 3.13），代码只用标准库 + `requests`，没用到任何新语法。

---

### 7. LLM —— 让 AI 层跑起来（可选）

**作用**：把「错误率 + p95 + 堆栈采样」这些结构化证据，翻译成有人能读懂、能拿去验证的根因假设。
它**不负责查数据**——查数据是 ES 的事，这个分工别搞混，搞混了就会写出「让模型猜日志」的无效 prompt。

三种接入方式，任选其一：

| 方式 | 成本 | 安装步骤 |
|---|---|---|
| 云端 API（DeepSeek / 通义） | 按量付费，几毛钱 | `.env` 填 `LLM_API_KEY` 即可，无需安装 |
| 本地 Ollama | 免费，数据不出门 | `brew install ollama` → `ollama serve` → `ollama pull qwen2.5:7b` |
| Elastic Playground | 用 Elastic 托管模型 | 在 Kibana 里连 connector，不用装 |

```bash
cp .env.example .env
# 编辑 .env，三行任选一组：
#   DeepSeek: LLM_BASE_URL=https://api.deepseek.com/v1  LLM_MODEL=deepseek-chat  LLM_API_KEY=sk-xxx
#   Ollama:   LLM_BASE_URL=http://localhost:11434/v1    LLM_MODEL=qwen2.5:7b     LLM_API_KEY=ollama
python3 ai_analyze.py
```

**什么 key 都没有？** `python3 ai_analyze.py --offline` 跑规则版，同样能定位异常服务，
只是结论是模板拼的而不是模型生成的。先用它验证整条链路通不通。

---

### 装完自检

```bash
colima status                          # macOS：VM 是否在跑
docker compose ps                      # ES 和 Kibana 是否 healthy
curl -s localhost:9200 | grep number   # ES 版本
curl -s localhost:5601/api/status | head -c 80   # Kibana 状态
python3 -c "import requests; print('py ok')"
```

四条都过，就可以进第 0 步选环境了。

**资源账**（决定你机器扛不扛得住）：

| 组件 | 空闲内存 | 来源 |
|---|---|---|
| ES | ~1.6GB（1GB 堆 + 堆外开销） | `docker stats` 实测 1.616GiB |
| Kibana | ~580MB | `docker stats` 实测 583MiB |
| Logstash | ~500MB（可选，默认不开） | 按 256m 堆推算，未实测 |
| colima VM 上限 | 4GB | 启动参数 `--memory 4` |

不开 Logstash 时合计约 2.2GB（实测 2.20GiB）。**机器紧张就只开 ES**：`docker compose up -d elasticsearch`。

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

```
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
mkdir -p logs                      # 必须先建，compose 挂载 ./logs，不存在会报错
echo '{"@timestamp":"2026-11-26T10:00:00Z","service":"order-service","level":"ERROR","status":500,"latency_ms":2100,"message":"pool exhausted"}' > logs/app.log
docker compose --profile collect up -d logstash
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
| 拉镜像报 `proxyconnect tcp: dial tcp 192.168.x.x:PORT: connection refused` | colima VM 里 `daemon.json` 的 `proxies` 指向了已失效的代理端口（代理软件重启后端口会变）。见下方单独说明 |
| `logs/` 不存在导致第 6 步报错 | 先 `mkdir -p logs` 再起 Logstash，compose 要挂载这个目录 |

### 关于 colima 里的代理（踩过）

VM 里的 docker daemon 会从宿主环境继承 `HTTP_PROXY`，把宿主端口「翻译」成 VM 网段地址后写进
`/etc/docker/daemon.json`：

```json
{ "proxies": { "http-proxy": "http://192.168.5.2:54434", "https-proxy": "..." } }
```

宿主的代理软件一旦重启换了端口，这个写死的值就失效，表现为**任何镜像都拉不下来**。
两种修法：

```bash
# 1) 让 colima 重新继承当前宿主代理（最快）
colima stop && colima start

# 2) 手动改端口，然后重启 daemon
colima ssh -- sudo vi /etc/docker/daemon.json      # 把 54434 改成宿主当前代理端口
colima ssh -- sudo systemctl restart docker
```

改完 `docker pull hello-world` 验证。注意重启 daemon 会让容器全部重启，
`docker compose up -d` 拉回来即可，数据在卷里不会丢。

> 另一种情况：代理只监听宿主的 `127.0.0.1`（某些客户端就是这样），VM 无论如何都访问不到。
> 这时 `--noproxy` 直连也未必通，最省事的办法是**改用 Elastic Cloud 路线**，别跟本地网络死磕。

---

## 练完之后你应该能回答

1. 为什么日志字段要区分 text 和 keyword？分别用在哪
2. 一次 500 暴增，用 Kibana 怎么在 2 分钟内定位到服务、时间点、错误类型
3. p95 涨了但错误率没涨，是哪种故障形态（提示：慢查询 / 下游抖动）
4. 给 LLM 的证据里，**哪些必须给、哪些给了反而误导**（提示：全量日志 → 噪声淹没真因）
5. AIOps 的边界在哪：什么它能判，什么必须人来看
