# OpenCode Go 用量监控 —— REST API 文档

后端由 `opencode_mon/server.py` 提供，纯标准库 `http.server`，默认监听 `http://127.0.0.1:8932`。所有响应均为 JSON，并带 `Access-Control-Allow-Origin: *`，可直接跨域接入其他工具。

- 机器可读规范：`GET /api/openapi.json`（OpenAPI 3.0，详见 `docs/openapi.json`）
- 时间单位：`*_ts`/`time_created` 等均为 **毫秒**（Unix epoch ms）
- 金额单位：美元（USD），`value`=Go 计价值（token×官方单价），`cost`=DB 内 opencode 批发成本（参考）

## 端点一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/overview` | 总览：今日/近30天、计划额度(本地)、官方用量(服务器)、活跃会话 |
| GET | `/api/models` | 每模型 × 多窗口额度的值/请求双维度；`?model=<id>` 查询单个 |
| GET | `/api/sessions` | 会话明细（本地 DB） |
| GET | `/api/series` | 按天趋势序列（供图表） |
| GET | `/api/live?since=<seq>` | 实时事件增量（轮询） |
| GET | `/api/account` | 官方服务器端用量（强制刷新缓存） |
| GET | `/api/config` | 运行配置快照 |
| GET | `/api/policy` | 当前政策（计划限额 + 每模型单价/请求额度） |
| POST | `/api/policy/refresh` | 从官方文档更新政策；`?dry_run=1` 只预览 |
| GET | `/api/openapi.json` | OpenAPI 3.0 规范 |

## GET /api/overview

```jsonc
{
  "now": 1786798485123,
  "today": { "requests": 312, "value": 1.94, "cost": 1.87, "tokens": { "input": 523443, "output": 123674, "reasoning": 79284, "cache_read": 15512988, "cache_write": 0 } },
  "total_30d": { /* 同上，近30天 */ },
  "plan": {
    "5h":   { "value": 1.94, "cost": 1.87, "limit": 12,  "pct": 16.2, "requests": 312 },
    "week": { "value": 1.94, "cost": 1.87, "limit": 30,  "pct": 6.5,  "requests": 312 },
    "month":{ "value": 2.22, "cost": 2.15, "limit": 60,  "pct": 3.7,  "requests": 666 }
  },
  "account": {
    "configured": true,
    "key_source": "auth.json",
    "windows": {
      "rolling": { "percent": 63, "resets_at": "2026-08-15T14:26:07Z", "used": 7.56, "limit": 12, "remaining": 4.44 },
      "weekly":  { "percent": 26, "resets_at": "2026-08-17T00:00:00Z", "used": 7.8,  "limit": 30, "remaining": 22.2 },
      "monthly": { "percent": 13, "resets_at": "2026-09-12T02:54:23Z", "used": 7.8,  "limit": 60, "remaining": 52.2 }
    }
  },
  "active_sessions": [ { "session_id": "...", "title": "...", "agent": "build", "model": "deepseek-v4-flash", "streaming": false, "last_ts": 1786798480965 } ]
}
```

> `plan` 为本机估算；`account.windows` 为**官方服务器端权威数据**（跨设备）。判断额度以 `account` 为准。

## GET /api/models

参数：`?model=<id>`（可选，省略返回全部）。

每模型每窗口结构（窗口：`5h` / `week_rolling` / `week` / `month_rolling` / `month`）：

```jsonc
{
  "deepseek-v4-flash": {
    "model": "deepseek-v4-flash",
    "configured": true,
    "eligible": true,
    "windows": {
      "5h": {
        "requests": { "used": 254, "limit": 31650, "pct": 0.8, "remaining": 31396 },
        "value":    { "used": 0.14, "cost": 0.07, "limit": 12.0, "pct": 1.16, "remaining": 11.86 },
        "tokens":   { "input": 439621, "output": 89889, "reasoning": 59201, "cache_read": 13467559, "cache_write": 0 },
        "binding": "value"
      }
    }
  }
}
```

- `binding`：该窗口内「请求数 / 值」哪个先耗尽（`requests` | `value` | `null`）
- `eligible=false`：免费模型/未配置 provider，不计入套餐额度

## GET /api/sessions

参数：`window`（`5h`/`week_rolling`/`week`/`month_rolling`/`month`，默认 `month_rolling`）、`model`、`limit`（默认 200）。

返回数组，按最近活动倒序，每项含 `session_id`、`title`、`agent`、`model`、`requests`、`value`、`cost`、`tokens{...}`、`duration_ms`、`first_ts`、`last_ts`。

## GET /api/series

参数：`days`（默认 14）、`model`。返回 `{ days, start, end, series: [{ date, value, cost, requests, tokens{...} }] }`。

## GET /api/live?since=<seq>

`event` 表增量轮询。返回 `{ seq, events: [...] }`，`seq` 为最新事件序号，客户端保存后传回即得实时流。事件含 `type`、`session_id`、`model`、`role`、`cost`、`tokens`、`part_type` 等字段。

## GET /api/account

官方服务器端用量，强制绕过缓存抓取 `https://opencode.ai/zen/go/v1/usage`。结构见 `/api/overview` 的 `account` 字段。

## GET /api/config

```jsonc
{
  "refresh_interval": 2,
  "db_path": "/home/.../opencode.db",
  "windows": ["5h", "week_rolling", "week", "month_rolling", "month"],
  "plan": { "limits": { "5h": 12, "week": 30, "month": 60 } },
  "account": { "enabled": true, "configured": true, "key_source": "auth.json", "refresh_seconds": 60 },
  "models": { "kimi-k3": { "priced": true, "allowance_month": 15, "requests": { "5h": 110, "week": 250, "month": 490 } } }
}
```

## GET /api/policy 与 POST /api/policy/refresh

- `GET /api/policy`：当前生效政策快照 `{ plan_limits, models, docs_url, generated_at }`。
- `POST /api/policy/refresh`：抓取官方文档（默认 GitHub `zh-cn/go.mdx`，可用 `policy.docs_url` 或 `OPENCODE_MON_DOCS_URL` 覆盖）解析计划限额、每模型单价与请求额度。
  - `?dry_run=1`：只返回解析结果 + diff，不写盘。
  - 不带 `dry_run`：写回 `policy.json`（不入 git，先备份为 `policy.json.bak-<ts>`），返回 `{ plan_limits, models, diff, write }`。
  - CLI 等效命令：`python3 scripts/update_policy.py [--apply] [--config path]`

## 跨域复用示例（任意语言）

```python
import urllib.request, json

def get(path):
    with urllib.request.urlopen("http://127.0.0.1:8932" + path, timeout=5) as r:
        return json.load(r)

overview = get("/api/overview")
official_5h = overview["account"]["windows"]["rolling"]   # 官方 5h 百分比
kimi = get("/api/models?model=kimi-k3")["windows"]["month"]["value"]
live = get("/api/live?since=0")["seq"]
```

## 错误格式

非 2xx 均返回 `{ "error": "..." }`。常见：`503`（opencode 数据库不存在）、`404`（端点/模型不存在）、`500`（内部错误）。
