# ELK + AI 可观测性 Demo：环境安装与使用指南

> 版本：v1.0　最后更新：2026-11-26　适用环境：macOS (Apple Silicon) / Linux
> 读者：想动手理解「日志采集 → 存储检索 → 可视化 → AI 根因分析」这条链路的研发与测试同学

---

## 目录

- [0. 这份文档解决什么问题](#0-这份文档解决什么问题)
- [1. 先搞清楚这套架构在做什么](#1-先搞清楚这套架构在做什么)
- [2. 环境准备](#2-环境准备)
- [3. 跑起来：六步走](#3-跑起来六步走)
- [4. Kibana 实操指南](#4-kibana-实操指南)
- [5. AI 层的三种接法](#5-ai-层的三种接法)
- [6. 本次实测结果与解读](#6-本次实测结果与解读)
- [7. 日常命令速查](#7-日常命令速查)
- [8. 常见问题排查](#8-常见问题排查)
- [9. 学完自测](#9-学完自测)
- [附录 A：文件清单](#附录-a文件清单)
- [附录 B：环境版本与校验值](#附录-b环境版本与校验值)

---

## 0. 这份文档解决什么问题

一句话：**给你一套 30 分钟能跑通、且带真实故障数据的 ELK 环境，用来练「从日志里定位问题」这件事。**

它不依赖任何真实业务系统。数据是脚本造的，里面故意埋了两个故障，所以你看到的每一条告警、每一次 AI 分析，都有标准答案可对。

读完并动手做完，你应该能：

1. 独立搭起 ES + Kibana，知道每个组件的资源开销和取舍
2. 在 Kibana 里 2 分钟内定位「哪个服务、什么时候、什么错误」
3. 手写 DSL 和 ES|QL 两种查询
4. 把 LLM 接到日志上做根因分析，并且知道**该给它什么、不该给它什么**

---

## 1. 先搞清楚这套架构在做什么

### 1.1 四层职责

```
 数据源                采集层                 存储检索                消费层
┌──────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│ 应用日志  │      │              │      │              │      │ Kibana       │
│ 网关日志  ├─────►│ Filebeat /   ├─────►│ Elasticsearch├──┬──►│ 看板·告警    │
│ 测试报告  │      │ Logstash     │      │ 索引·聚合·查询│  │   └──────────────┘
└──────────┘      └──────────────┘      └──────┬───────┘  │   ┌──────────────┐
                                               │          └──►│ AI 层        │
                                               └─────────────►│ LLM 根因分析 │
                                                  (AI 反查取上下文)  └──────────────┘
```

| 层 | 组件 | 干什么 | 关键概念 |
|---|---|---|---|
| 数据源 | 应用 / 网关 / 测试脚本 | 产出结构化（或半结构化）日志 | 一行一个 JSON、字段命名要统一 |
| 采集层 | Filebeat（轻）/ Logstash（重） | 收集、解析、打标、路由 | `codec`、`filter`、`tags` |
| 存储检索 | Elasticsearch | 倒排索引、聚合、ES\|QL | `mapping`、`keyword` vs `text`、ILM |
| 消费层 | Kibana / LLM | 可视化、告警、根因分析 | Data View、Dashboard、Rule |

### 1.2 一句话记住分工

- **ELK 负责「看见」**：把分散的日志变成可以聚合、可以画图、可以告警的数据
- **AI 负责「解释」**：把「一堆指标异常」翻译成「可能是什么原因、下一步查什么」

这个分工很重要。**ELK 没做好的时候，AI 只会给你一段听起来很专业的废话。**
所以本 demo 的顺序是：先把数据和图表做对，再接 AI。

---

## 2. 环境准备

### 2.1 三条路线怎么选

| 路线 | 本地内存开销 | 首次耗时 | 适合 |
|---|---|---|---|
| **A. Elastic Cloud 试用** | 0 | 5 分钟 | 机器内存紧张；想练 8.x 的 ML / ELSER 等 AI 能力 |
| **B. colima（本机已采用）** | 约 4GB（VM 定额） | 25 分钟 | macOS 上想省内存、不装 Docker Desktop |
| **C. Docker Desktop** | 约 2~3GB 常驻 + VM | 15 分钟 | 团队统一环境、需要 GUI 管理容器 |

> 我们的机器长期内存占用偏高，最终选的是 B。C 是最省事的标准路线，同事按 2.3 节操作即可。

### 2.2 路线 B：colima 安装（macOS，本机实际方案）

**为什么不用 Docker Desktop**：Desktop 自带一套常驻 GUI 和守护进程，空载就吃 2GB+。
colima 是一个纯 CLI 的轻量 VM 管理器，底层用 lima，只有跑容器时占内存，`colima stop` 能全部释放。

安装顺序：docker CLI + compose 插件 + colima + lima + VM 镜像。**这台机器上有 4 个坑，按顺序处理。**

#### 坑 1：`brew install` 直接失败

```
sandbox-exec: sandbox_apply: Operation not permitted
```
`/opt/homebrew/Library/Homebrew/sandbox_operation.rb extract exited with 71`

**原因**：Homebrew 7 会先探测自己是否已在沙箱内，但即使设置 `HOMEBREW_AVOID_NESTED_SANDBOXING=1`，
默认前缀（`/opt/homebrew`）下也会被拒绝：

```
Error: Refusing to skip the sandbox: `$HOMEBREW_AVOID_NESTED_SANDBOXING` is set
inside another sandbox but Homebrew is using its default prefix (/opt/homebrew);
this is only supported in a custom prefix.
```

**解法**：绕开 brew 的「解压」环节，只用它下载 bottle，自己解压到用户目录。

```bash
# 1) 只下载，不解压（fetch 不涉及沙箱）
brew fetch --force colima lima docker docker-compose

# 2) 手动解到 ~/opt/brew/Cellar，保持 Homebrew 的目录结构
mkdir -p ~/opt/brew/Cellar ~/opt/bin
for f in colima lima docker docker-compose; do
  tar -xzf "$(brew --cache $f)" -C ~/opt/brew/Cellar
done

# 3) 建软链
for f in colima lima docker docker-compose; do
  for b in ~/opt/brew/Cellar/$f/*/bin/*; do ln -sfn "$b" ~/opt/bin/$(basename "$b"); done
done
mkdir -p ~/.docker/cli-plugins
ln -sfn ~/opt/brew/Cellar/docker-compose/*/bin/docker-compose ~/.docker/cli-plugins/docker-compose

# 4) PATH 持久化（写进 ~/.zshrc）
export PATH="$HOME/opt/bin:$PATH"
export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"
```

验证：

```bash
docker --version          # Docker version 29.10.1
docker compose version    # Docker Compose version 5.8.0
colima version            # colima version 0.10.3
limactl --version         # limactl version 2.3.0
```

#### 坑 2：`github.com` 连不通，colima 下不到镜像

```
level=fatal msg="error starting vm: ... error resolving download URL
'https://github.com/abiosoft/colima-core/releases/download/v0.10.4/ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz':
resolve redirect failed ... Bad Gateway"
```

**原因**：内网代理只放通了部分域名。实测可达性：

| 域名 | 状态 | 用途 |
|---|---|---|
| `api.github.com` | 通 | 查版本 |
| `ghcr.io` | 通 | Homebrew bottle |
| `download.docker.com` | 通 | docker CLI |
| `docker.elastic.co` | 通 | ES / Kibana 镜像 |
| **`github.com`** | **不通（CONNECT 502）** | **colima 的 VM 镜像 ← 卡在这** |
| `gh-proxy.com` | 通 | GitHub 内容的代理 |

**解法**：用 `gh-proxy.com` 前缀代理下载，校验后挂进去。

```bash
mkdir -p ~/opt/colima && cd ~/opt/colima
curl -L -o ubuntu-arm64-docker.raw.gz \
  "https://gh-proxy.com/https://github.com/abiosoft/colima-core/releases/download/v0.10.4/ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz"

# 必须校验：官方 SHA512 见附录 B
shasum -a 512 ubuntu-arm64-docker.raw.gz
```

#### 坑 3：`--disk-image` 必须传 `.gz`，不能传解压后的 `.raw`

传 `.raw` 会报：

```
hash failure: SHA512 checksum mismatch for 'ubuntu-arm64-docker.raw':
  expected: 32242674...
  actual:   bc196077...
```

**原因**：colima 拿**压缩包**的 SHA512 去比对，你传解压后的文件，哈希当然对不上。
所以不要手动 `gunzip`，直接把 `.gz` 交给它，colima 自己解。

#### 启动 VM

```bash
colima start --disk-image "$HOME/opt/colima/ubuntu-arm64-docker.raw.gz" \
             --cpu 2 --memory 4 --disk 30
```

约 60~80 秒，看到 `msg=done` 即成功。验证：

```bash
docker info --format 'OS: {{.OperatingSystem}} | CPU: {{.NCPU}} | MEM: {{.MemTotal}}'
# OS: Ubuntu 24.04.4 LTS | CPU: 2 | MEM: 4095574016 | Driver: overlayfs
```

> **注意**：`--disk-image` 只在首次创建时生效。以后直接 `colima start` 即可，不用再传。

### 2.3 路线 C：标准 Docker 环境（同事用这个）

装了 Docker Desktop 的机器，跳过上面全部，直接：

```bash
docker compose up -d
```

### 2.4 目录结构与文件职责

```
elk-ai-demo/
├── docker-compose.yml      ES 8.15 单节点(1GB 堆) + Kibana + 可选 Logstash
├── .env.example            环境变量模板（ES/Kibana 地址、LLM 配置）
├── common.py               .env 解析 + ES/Kibana 请求封装 + 探活
├── setup_es.py             索引模板(字段类型) / ILM 生命周期 / Kibana Data View
├── gen_logs.py             造 90 分钟日志，埋 2 个故障
├── ai_analyze.py           基线对比 → 定位异常 → 采样 → LLM 出根因报告
├── logstash/pipeline/      可选：练采集链路的配置
└── reports/                生成的 RCA 报告（Markdown）
```

---

## 3. 跑起来：六步走

> 每步都标了「在学什么」。只想看效果的话，按顺序复制命令即可。

### 第 1 步 · 起容器

```bash
cd elk-ai-demo
docker compose up -d
```

预期：

```
Container demo-es      Healthy
Container demo-kibana  Started
```

**在学什么**：编排的启动顺序。compose 里 Kibana 用 `depends_on: {condition: service_healthy}`，
必须先等 ES 通过健康检查才启动。去掉这一行，Kibana 会因为连不上 ES 而反复重启。
首次要拉约 1.5GB 镜像，之后秒起。

### 第 2 步 · 建索引结构

```bash
python3 -m pip install -r requirements.txt   # 只依赖 requests
python3 setup_es.py
```

预期：

```
→ 等 Elasticsearch: http://localhost:9200
  集群 docker-cluster / 版本 8.15.0
✓ ILM 策略 app-logs-policy（1 天 rollover，7 天删除）
✓ 索引模板 app-logs-template（匹配 app-logs-*）
→ 等 Kibana: http://localhost:5601
✓ Kibana Data View: app-logs
```

**在学什么（本步最重要的一条）**：`keyword` 和 `text` 的区别。

```json
"service":  { "type": "keyword" },   // 要聚合、过滤、画图 → keyword
"message":  { "type": "text" }       // 要全文检索 → text
```

把 `service` 写成 `text` 的后果：`terms` 聚合会把 `order-service` 拆成三个词，
你的服务维度图表全是错的，而且**报错信息没有任何提示**。这是新手第一坑。

### 第 3 步 · 灌数据

```bash
python3 gen_logs.py --reset --minutes 90 --rate 150
```

预期：

```
· 已清空 1 个旧索引
✓ 写入 21840 条日志，覆盖最近 90 分钟
  已埋两个异常：order-service 500 暴增 / payment-service 延迟飙升
```

参数说明：

| 参数 | 含义 | 建议 |
|---|---|---|
| `--minutes` | 生成多少分钟的历史 | 60 够看图，90 够做基线对比 |
| `--rate` | 每分钟日志条数（白天 ×1.6，夜间 ×0.5） | 150 → 约 2 万条 |
| `--reset` | 先删掉已有 `app-logs-*` 索引 | 反复跑必须加，否则数据翻倍 |

**埋的两个故障**（后面对答案用）：

| 时间 | 服务 | 表现 |
|---|---|---|
| now-27m ~ now-17m | order-service | 45% 请求 500，堆栈 `HikariPool 连接池耗尽` |
| now-8m ~ now-5m | payment-service | p95 从 180ms → 4000ms+，**一条 ERROR 都没有** |

### 第 4 步 · 在 Kibana 里看见现象

浏览器打开 <http://localhost:5601>，按第 4 节操作。

**在学什么**：先建立「看图的直觉」。看不懂图的人，调不出好的 AI prompt。

### 第 5 步 · 手写查询

见第 4.3 节，DSL 和 ES|QL 各写一遍。

### 第 6 步 · 接 AI

```bash
cp .env.example .env
# 编辑 .env，填 LLM_API_KEY（DeepSeek / 通义 / Ollama 均可）
python3 ai_analyze.py
```

没配 key 也能跑：

```bash
python3 ai_analyze.py --offline   # 只出规则版结论，验证链路是否通
```

**在学什么**：AI 分析的最小闭环长什么样 —— 见第 5.1 节。

---

## 4. Kibana 实操指南

### 4.1 Discover：先看原始日志

1. 左侧菜单 → **Discover**
2. 左上数据视图选 `app-logs`
3. 时间范围选 **Last 90 minutes**
4. 顶部搜索栏输入 `level: ERROR`，回车

你会看到错误集中在 `order-service`。左侧字段栏点 `service`、`status`、`latency_ms` 可以看 Top 值分布。

> 技巧：点字段名右侧的「+」是**加过滤**，「-」是**排除**。
> 排障时先排除掉无关服务，比一直加条件更快收敛。

### 4.2 Dashboard：搭一个最小监控面板

新建 Dashboard，加 4 个图。这不是走形式 —— 这 4 张图就是线上监控的最小可用集：

| 图 | 类型 | 配置 | 它能回答什么 |
|---|---|---|---|
| 日志量趋势 | 折线 | X: `@timestamp` 每 5 分钟，Y: 文档数 | 流量有没有突变、日志是不是断了 |
| 服务错误数 | 柱状 | X: `service`，Y: 计数，过滤 `level:ERROR` | 哪个服务在冒烟 |
| 延迟 p95 | 折线 | Y: `latency_ms` 的 95 分位，按 `service` 拆分 | 有没有「只慢不报错」的劣化 |
| 状态码分布 | 表格 | 行 `status`，值 计数 + 百分比 | 是 5xx 还是 4xx，客户侧还是服务侧 |

**为什么必须有「延迟 p95」这张图**：本 demo 的第二个故障，`payment-service` 一条 ERROR 都没有。
只按错误日志做告警的团队，这类故障会一直漏到用户投诉。

### 4.3 两种查询写法

#### DSL（原生查询，能力最全）

Kibana → Dev Tools：

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

#### ES|QL（8.11+，更像 SQL，推荐日常用）

```
FROM app-logs-*
| WHERE @timestamp > NOW() - 30 minutes AND level == "ERROR"
| STATS errors = COUNT(*) BY service, status
| SORT errors DESC
```

实测输出：

```
    errors     |    service    |    status
---------------+---------------+---------------
157            |order-service  |500
```

两个都要会。理由很实在：**DSL 写得慢但什么都能查，ES|QL 写得快但受限于语法覆盖**。
排障时先 ES|QL 快速收敛范围，需要复杂聚合再落回 DSL。

### 4.4 告警

Stack Management → **Rules and Connectors** → Create rule → **Index threshold**：

- 索引：`app-logs-*`
- 条件：`level:ERROR`，窗口 5 分钟，阈值 > 50 条
- 动作：写日志 / Webhook / 邮件

> 阈值型告警只覆盖「量」的异常。要覆盖「延迟劣化」这类形态，
> 得再加一条基于 `latency_ms` p95 的规则，或者干脆交给第 5 节的 AI 分析。

---

## 5. AI 层的三种接法

按难度递进，建议依次做。

### 5.1 外部 LLM 做根因分析（本仓库已实现）

```bash
python3 ai_analyze.py
```

它做的事，就是 AIOps 的最小闭环：

```
① 基线对比     最近 30 分钟 vs 前 30 分钟，按服务算错误率/延迟变化
② 定位异常     规则打分，先筛出可疑服务（这一步不交给 LLM）
③ 取证据       5 分钟桶的错误时间线 + 12 条真实错误样本（含堆栈）
④ LLM 出结论   根因假设（带置信度）+ 验证步骤 + 对测试的启示
⑤ 落盘         reports/rca-YYYYMMDD-HHMMSS.md
```

**为什么第 ② 步不交给 LLM**：让 LLM 对着 2 万条原始日志找规律，等于大海捞针。
先用聚合把范围压到「2 个服务、3 个时间点」，再喂给它 —— **这是 prompt 工程里最有效的动作，
比调 temperature 有用得多。**

`.env` 配置（任选其一）：

```bash
# DeepSeek
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=sk-xxx

# 本地 Ollama（零成本、数据不出门）
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b
LLM_API_KEY=ollama
```

### 5.2 Elastic 原生 AI（Playground + 语义检索）

Kibana → **Playground**：

1. 连接模型（Elastic 托管的 ELSER，或外部 OpenAI / Bedrock connector）
2. 上传一份《接口规范 / 故障手册》作为知识库
3. 提问「order-service 出现连接池耗尽时该看哪几个指标」

这就是 RAG。跑通后你会理解一件事：**向量检索解决「查不到关键词」，
BM25 解决「查得准」**，生产环境通常是 hybrid（两者相加后重排）。

### 5.3 让 Agent 自己查 ES（进阶）

把 `ai_analyze.py` 里的固定查询改成「工具」，交给 LLM 决策：

```python
tools = [
    {"name": "es_search",   "description": "执行 DSL 查询，返回命中数与聚合"},
    {"name": "es_query",    "description": "执行 ES|QL，返回表格"},
]
```

模型自己决定查什么、查几次、什么时候收敛。这是「一次性 prompt」和「Agent 排障」的分界线。

> 这条路线的难点不在调用，而在**工具描述**：`es_search` 的 description 写得含糊，
> Agent 就会乱查。写清楚「什么场景该用我、参数含义、返回结构」—— 这部分经验可以直接复用。

---

## 6. 本次实测结果与解读

环境：ES/Kibana 8.15.0，21840 条日志，`--minutes 90 --rate 150`。

`python3 ai_analyze.py --offline` 输出：

| 服务 | 错误率变化 | p95 变化 | 可疑分 |
|---|---|---|---|
| payment-service | 0.0% → 0.0% | 187.8ms → **4402.2ms** | 448.8 |
| order-service | 0.0% → **17.0%** | 184.7ms → 2400.1ms | 273.9 |

`api-gateway` 和 `user-service` **没有出现在结果里**，因为它们确实没异常 —— 这是对的。

### 三个值得讨论的点

**1. 得分最高的服务，一条错误日志都没有。**
`payment-service` 只是变慢，日志全是 INFO/WARN。如果你们的告警只盯 `level:ERROR`，
这个故障会一路漏到用户投诉。**延迟必须有独立的告警维度。**

**2. order-service 的可疑分反而更低。**
因为它虽然打了 500，但延迟只到 2400ms；payment 到了 4400ms。
说明「可疑分」这类规则打分只能用来**排序**，不能用来**定级** —— 定级还得看业务影响。

**3. 基线对比是这套方法的基础。**
所有判断都建立在「前 30 分钟正常」这个前提上。如果数据本身没有平稳期，
（比如刚发版、流量在爬坡）基线就会失真，这时候需要换更长的窗口或同期对比（上周同一时段）。

---

## 7. 日常命令速查

### 7.1 环境启停

```bash
colima start                  # 开机后先起 VM（约 60s）
colima status                 # 查看 VM 状态
colima stop                   # 停 VM，释放 4GB 内存

cd elk-ai-demo
docker compose up -d          # 起 ES + Kibana
docker compose ps             # 看容器状态
docker compose logs -f kibana # 跟日志
docker compose down           # 停容器，数据保留
docker compose down -v        # 停容器并删除数据卷（重来一遍用这个）
```

### 7.2 数据操作

```bash
python3 setup_es.py                                  # 建模板/ILM/Data View
python3 gen_logs.py --reset --minutes 90 --rate 150  # 重灌数据
python3 gen_logs.py --minutes 180 --rate 300         # 大数据量压测
curl -s "localhost:9200/app-logs-*/_count"           # 看总条数
```

### 7.3 分析

```bash
python3 ai_analyze.py            # 带 AI 的完整报告
python3 ai_analyze.py --offline  # 只出规则版
ls reports/                      # 历史报告
```

### 7.4 服务地址

| 服务 | 地址 |
|---|---|
| Kibana | <http://localhost:5601> |
| Elasticsearch | <http://localhost:9200> |
| ES 集群健康 | <http://localhost:9200/_cluster/health?pretty> |

---

## 8. 常见问题排查

| 现象 | 原因 | 解法 |
|---|---|---|
| `docker: command not found` | `~/opt/bin` 不在 PATH | `source ~/.zshrc` 或新开终端 |
| `Cannot connect to the Docker daemon` | VM 没起 | `colima start` |
| `colima start` 卡在 downloading | github.com 不通 | 按坑 2 用 gh-proxy 预下载 + `--disk-image` |
| ES 容器反复重启 | VM 内存不足 | `colima stop && colima start --memory 6` |
| Kibana 一直 not ready | 在等 ES healthy | `docker compose logs -f kibana` 看是否连不上 ES |
| `mapper_parsing_exception` | 字段类型冲突（keyword 写了数字 / 时间格式错） | 检查 `@timestamp` 是否为 ISO8601 带 Z |
| 图表里服务名被拆成多个词 | 字段 mapping 用了 `text` | 改成 `keyword`，重建索引后重灌 |
| 数据条数翻倍 | 重复执行 `gen_logs.py` 没加 `--reset` | `--reset` 重来 |
| 报告提示「没发现明显异常」 | 数据滑出 30 分钟窗口了 | 重跑 `gen_logs.py` |
| Mac 风扇狂转、系统卡 | ES 堆 + Kibana 常驻吃内存 | 只起 ES：`docker compose up -d elasticsearch` |

---

## 9. 学完自测

能流畅回答下面 5 题，这套东西就算过了一遍：

1. 为什么 `service` 要用 `keyword` 而不是 `text`？用错了会有什么现象？
2. 一次 500 暴增，你怎么在 2 分钟内定位到「哪个服务、哪个时间点、什么错误类型」？
3. p95 涨了但错误率没涨，说明是哪类故障？（提示：慢查询、下游抖动、资源争抢）
4. 给 LLM 的证据里，哪些必须给、哪些给了反而会误导它？（提示：全量日志 → 噪声淹没真因）
5. AIOps 的边界在哪：什么它能判，什么必须人来看？

### 延伸练习

- 加第三个故障形态：**错误率不变、延迟不变，但日志量暴涨**（重复打日志）→ 该怎么查？
- 把 `gen_logs.py` 的数据源换成**你们自己的测试报告 JSON**，字段映射怎么做？
- 给 `ai_analyze.py` 加一个 `--since` 参数，支持分析指定时间段的历史故障。
- 用 Logstash（`--profile collect`）走一遍真实采集链路，对比直连 bulk 写入的字段差异。

---

## 附录 A：文件清单

| 文件 | 作用 | 关键接口 |
|---|---|---|
| `docker-compose.yml` | ES 8.15 单节点 + Kibana + 可选 Logstash | 端口 9200 / 5601 / 5044 |
| `.env.example` | 环境变量模板 | `ES_URL` `KIBANA_URL` `LLM_*` |
| `common.py` | 请求封装 | `es()` `es_bulk()` `kb()` `wait_ready()` |
| `setup_es.py` | 建索引模板 / ILM / Data View | 幂等，可重复跑 |
| `gen_logs.py` | 造数据 + 埋点 | `--reset` `--minutes` `--rate` |
| `ai_analyze.py` | 根因分析 | `--offline` |
| `logstash/pipeline/demo.conf` | 采集链路示例 | `--profile collect` 启用 |

## 附录 B：环境版本与校验值

| 组件 | 版本 |
|---|---|
| Elasticsearch / Kibana | 8.15.0 |
| docker CLI | 29.10.1 |
| docker compose | 5.8.0 |
| colima | 0.10.3 |
| lima | 2.3.0 |
| VM 系统 | Ubuntu 24.04.4 LTS (arm64) |

VM 规格：2 CPU / 4GB 内存 / 30GB 磁盘，`vz` 驱动。ES 堆 1GB。

colima 镜像校验值（SHA512）：

```
文件：ubuntu-24.04-minimal-cloudimg-arm64-docker.raw.gz
SHA512：32242674b046b5057e60c4aba334b51e3665f05412cda89ed081cc2de153ae5c
        41f6b105b5c442cbe48d78e2cc21e9ba1950e406b6fb4fc2fd1dd2259240abbd
大小：332,354,401 字节（约 317MB）
```

> 下载后务必校验。走代理拿到的文件如果被篡改或截断，colima 会拒绝启动 ——
> 这个校验是唯一能提前发现问题的环节。
