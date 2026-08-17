# OpenCode Go 实时用量监控

监控 [OpenCode Go](https://opencode.ai/docs/zh-cn/go/) 订阅的实时用量：**美元值额度**与**请求数额度**双维度，按 **滚动5小时 / 每周 / 每月** 窗口统计，覆盖套餐内每个模型。

- **官方额度（推荐）**：自动读取本机 opencode 登录凭证（`~/.local/share/opencode/auth.json`）中的 Go API Key，调用官方 `https://opencode.ai/zen/go/v1/usage` 获取**服务器端权威数据**（跨设备/跨工具统一，含重置时间）。
- **本地明细**：从本地 `~/.local/share/opencode/opencode.db`（SQLite，WAL 只读安全）统计每模型/每会话的请求数与 token 成本，无需 API Key，免费、实时、可离线。
- 架构：纯标准库 Python 后端（REST API）+ 两个前端（终端 TUI / Web），前后端分离，后端包 `opencode_mon/` 可独立复用。

> ⚠️ 重要：官方 5h/周/月 滑窗按**服务器端时钟**（`resetsAt`）计算，与本机"最近 5 小时"估算可能差异很大（实测本地 16% vs 官方 63%）。**判断额度是否够用请以官方百分比为准**；本地数据用于每模型/每会话的细粒度分析。

## 额度口径

| 维度 | 5 小时 | 周 | 月 |
|---|---|---|---|
| 美元值（全模型合计） | ≤ $12 | ≤ $30 | ≤ $60 |
| 美元值（每模型月额度） | — | — | ≤ $15 或 $60 |
| 请求数（每模型） | 官方估算表 | 官方估算表 | 官方估算表 |

> 说明：
> - 美元值 = token 数 × 官方单价（`policy.json` 中 `models` 表），reasoning 按输出计（可配置）。
> - DB 内 `cost` 是 opencode 内部批发价（约为官方价的 1/2），仅作参考展示。
> - 免费模型（providerID 非 `opencode-go`，如 `deepseek-v4-flash-free`）不计入套餐额度。
> - 请求数上限是官方按典型 token 模式给出的**估算值**，实际按真实请求数统计。

## 快速开始

```bash
# 1. 启动后端（默认端口 8932）
python3 server.py --port 8932

# 2a. 终端面板
python3 tui.py            # 依赖后端，按 j/k 滚动、Tab 切换、q 退出

# 2b. 浏览器仪表盘
# 打开 http://127.0.0.1:8932
```

`--config config.json` 可指定自定义配置；`--host`/`--port` 覆盖监听地址。

## 目录结构

```
opencode_mon/          # 可复用后端包（零依赖）
  config.py            # 配置加载：用户设置 + 政策文件(policy.json)
  db.py                # 只读 SQLite 访问（WAL 并发安全）
  metrics.py           # 额度计算：token×单价 → Go 计价值，窗口聚合
  account.py           # 官方服务器端用量（opencode.ai/zen/go/v1/usage）
  policy.py            # 官方文档解析：单价/请求额度/计划限额
  server.py            # stdlib http.server：JSON API + 静态托管
server.py              # 后端入口
tui.py                 # 终端面板（经 HTTP API 消费后端）
web/                   # 静态前端（Chart.js 走 CDN，离线时降级）
scripts/update_policy.py # 从官方文档更新政策到 policy.json（CLI）
docs/                  # api.md（REST API 文档）+ openapi.json（OpenAPI 3.0）
config.json            # 用户设置（入库）
policy.json            # 政策数据：单价/请求额度/限额（不入库，刷新生成）
policy.default.json    # 出厂默认政策（入库，policy.json 缺失时兜底）
```

## 更新价格 / 额度

> 推荐用上面的「政策自动更新」从官方文档同步；以下为手动方式（例如官方文档改版导致解析失效时）。

官方价与请求额度可能随新模型发布变动（见 https://opencode.ai/docs/zh-cn/go/ ）。手动改 **`policy.json`**（不入库）中的 `go_plan.limits` 与 `models` 表即可，无需改代码；修改后重启后端生效：

```jsonc
"models": {
  "my-new-model": {
    "input": 0.5, "output": 1.5,          // 每 1M tokens 单价（USD）
    "cache_read": 0.05, "cache_write": null,
    "allowance_month": 60,                 // 该模型月度美元额度
    "requests": { "5h": 1000, "week": 2500, "month": 5000 }  // 请求数上限（null 表示未公布）
  }
}
```

支持按上下文长度分档计价的模型用 `tiers`（见 `gpt-5.6-luna`、`qwen3.7-plus`）。

## 常见配置项（config.json 顶层）

| 键 | 默认 | 说明 |
|---|---|---|
| `db_path` | `~/.local/share/opencode/opencode.db` | opencode 数据库路径 |
| `port` / `host` | `8932` / `127.0.0.1` | API 监听 |
| `refresh_interval` | `2` | 轮询间隔（秒） |
| `reasoning_as_output` | `true` | 推理 token 是否按输出计价 |
| `count_title_requests` | `true` | 标题生成等小请求是否计入请求数 |
| `go_providers` | `["opencode-go"]` | 计入套餐额度的 provider 列表 |
| `active_window_seconds` | `90` | 判定"活跃会话"的时间窗 |
| `series_days` | `14` | 图表默认天数 |
| `policy_file` | `policy.json` | 政策数据文件（可更新，不入 git） |
| `policy_default_file` | `policy.default.json` | 出厂默认政策（入库兜底） |

## 官方用量（跨设备）配置

官方数据**自动工作**：工具优先从以下来源解析 Go API Key（按优先级）——

1. `config.json` → `account.api_key`
2. `config.json` → `account.api_key_file`（纯文本 key 文件路径）
3. 环境变量 `OPENCODE_GO_API_KEY`
4. `~/.local/share/opencode/auth.json`（opencode 的登录凭证，无需配置）

**没有安装 opencode 也能用**：只要任选其一提供 key（推荐 `account.api_key` 或 `api_key_file`），官方额度照常工作，本地明细部分数据为空（因为本地 DB 是 opencode 写的）。

```jsonc
"account": {
  "enabled": true,
  "api_key": "",                // 手动途径①：直接填 key
  "api_key_file": "/path/to/key", // 手动途径②：key 文件（首行内容）
  "auth_json_path": "~/.local/share/opencode/auth.json",
  "endpoint": "https://opencode.ai/zen/go/v1/usage",
  "refresh_seconds": 60
}
```

工具每 `refresh_seconds` 调用 `GET https://opencode.ai/zen/go/v1/usage` 获取服务器端 5h/周/月 百分比与重置时间。Key 仅存本机、仅用于读取用量，不会出现在任何 API 响应中；`/api/overview` 的 `account.key_source` 会标明当前来源（`config`/`file`/`env`/`auth.json`）。

## 政策（单价/限额）自动更新

官方单价与请求额度随新模型发布而变化。工具可从官方文档**自动解析并更新 `policy.json`**（此文件**不入 git**，刷新不会产生提交噪音；`policy.default.json` 为入库的出厂默认）：

```bash
python3 scripts/update_policy.py             # 预览（解析官方文档，显示 diff）
python3 scripts/update_policy.py --apply     # 写回 policy.json（备份为 policy.json.bak-<ts>）
```

Web 界面上也有「从官方文档更新政策」按钮；或通过 API：`POST /api/policy/refresh`（`?dry_run=1` 只预览）。

> 政策加载优先级：`policy.json`（可更新）→ `policy.default.json`（兜底）。可在 `config.json` 用 `policy_file` / `policy_default_file` 覆盖路径。

> 网络鲁棒性：抓取默认走 `raw.githubusercontent.com`，失败后自动回退 jsDelivr CDN 镜像（`cdn.jsdelivr.net`），并对瞬时错误重试（IPv4 优先，规避 IPv6 路由不通导致的卡死）。可在 `config.json` 的 `policy` 节配置 `docs_url` / `mirrors`（列表）/ `timeout_seconds`（默认 15），或用环境变量 `OPENCODE_MON_DOCS_URL` / `OPENCODE_MON_DOCS_MIRRORS` 覆盖。抓取/解析失败不会覆盖 `policy.json`，只会给出可读的错误提示。

## REST API

所有端点见 **[docs/api.md](docs/api.md)**，机器可读规范见 `GET /api/openapi.json`（`docs/openapi.json`）。简要：

| 端点 | 说明 |
|---|---|
| `GET /api/overview` | 总览：今日/近30天、计划额度、官方用量、活跃会话 |
| `GET /api/models?model=` | 每模型额度；可指定单模型 |
| `GET /api/sessions` | 会话明细（id、模型、agent、请求数、值、成本、token、时长） |
| `GET /api/series` | 按天趋势（供图表） |
| `GET /api/live?since=` | 实时事件增量 |
| `GET /api/account` | 官方服务器端用量（强制刷新） |
| `GET /api/policy` / `POST /api/policy/refresh` | 政策快照 / 从官方文档更新政策 |
| `GET /api/config` / `GET /api/openapi.json` | 配置快照 / OpenAPI 规范 |

响应均带 `Access-Control-Allow-Origin: *`，可直接跨域复用。

### 复用示例（Python 库方式）

```python
from opencode_mon.config import Config
from opencode_mon.db import OpenCodeDB
from opencode_mon.metrics import Metrics
from opencode_mon import account, policy

config = Config("config.json")
metrics = Metrics(config, OpenCodeDB(config.db_path))
print(metrics.models()["kimi-k3"]["windows"]["5h"])
print(metrics.overview()["plan"])

official = account.get_account(config)   # 官方服务器端用量
latest = policy.refresh_policy(config, "config.json", dry_run=True)  # 官方政策解析
```
