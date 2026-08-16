"""Usage metrics and quota computation for OpenCode Go."""

import datetime

from .config import WINDOW_ORDER

VALUE_WINDOWS = WINDOW_ORDER

ZERO_TOKENS = {
    "total": 0,
    "input": 0,
    "output": 0,
    "reasoning": 0,
    "cache_read": 0,
    "cache_write": 0,
}


def _add_tokens(acc, tokens):
    for key in ZERO_TOKENS:
        acc[key] = acc.get(key, 0) + (tokens.get(key) or 0)


def window_bounds(now_ms, window):
    start = now_ms
    end = now_ms
    if window == "5h":
        start = now_ms - 5 * 3600 * 1000
    elif window == "week_rolling":
        start = now_ms - 7 * 86400 * 1000
    elif window == "month_rolling":
        start = now_ms - 30 * 86400 * 1000
    elif window == "day":
        dt = datetime.datetime.fromtimestamp(now_ms / 1000.0).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = int(dt.timestamp() * 1000)
    else:
        dt = datetime.datetime.fromtimestamp(now_ms / 1000.0)
        if window == "week":
            dt = (dt - datetime.timedelta(days=dt.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif window == "month":
            dt = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start = int(dt.timestamp() * 1000)
    return start, end


def _msg_tokens(data):
    tokens = data.get("tokens") or {}
    cache = tokens.get("cache") or {}
    return {
        "total": tokens.get("total") or 0,
        "input": tokens.get("input") or 0,
        "output": tokens.get("output") or 0,
        "reasoning": tokens.get("reasoning") or 0,
        "cache_read": cache.get("read") or 0,
        "cache_write": cache.get("write") or 0,
    }


def _msg_cost(rate, tokens, reasoning_as_output):
    input_cost = tokens["input"] * (rate.get("input") or 0) / 1e6
    out_tokens = tokens["output"]
    if reasoning_as_output:
        out_tokens += tokens["reasoning"]
    output_cost = out_tokens * (rate.get("output") or 0) / 1e6
    cache_read_cost = tokens["cache_read"] * (rate.get("cache_read") or 0) / 1e6
    cache_write_cost = tokens["cache_write"] * (rate.get("cache_write") or 0) / 1e6
    total = input_cost + output_cost + cache_read_cost + cache_write_cost
    return {
        "input": input_cost,
        "output": output_cost,
        "cache_read": cache_read_cost,
        "cache_write": cache_write_cost,
        "total": total,
    }


def _is_request(data, config):
    if data.get("role") != "assistant":
        return False
    if data.get("agent") == "title" and not config.get("count_title_requests", True):
        return False
    tokens = _msg_tokens(data)
    if tokens["total"] <= 0 and tokens["input"] <= 0 and tokens["output"] <= 0:
        return False
    return True


class Metrics:
    def __init__(self, config, db, now_ms=None):
        self.config = config
        self.db = db
        self.now = int(now_ms) if now_ms is not None else int(
            datetime.datetime.now().timestamp() * 1000
        )
        cutoff = self.now - 31 * 86400 * 1000
        self.messages = db.messages(cutoff)
        self.session_info = db.sessions()
        self.requests: dict = self._index_requests()

    def _index_requests(self) -> dict:
        indexed = {}
        for m in self.messages:
            data = m["data"]
            if not _is_request(data, self.config.data):
                continue
            model = self.config.normalize(data.get("modelID") or "")
            if not model:
                continue
            tokens = _msg_tokens(data)
            rate = self.config.rate_for(model, tokens["total"])
            cost = _msg_cost(rate, tokens, self.config.get("reasoning_as_output", True)) if rate else None
            time_info = data.get("time") or {}
            indexed.setdefault(model, []).append({
                "ts": m["time_created"],
                "session_id": m["session_id"],
                "model": model,
                "provider": data.get("providerID") or "",
                "go": self.config.is_go(data.get("providerID")),
                "agent": data.get("agent") or "build",
                "mode": data.get("mode") or "build",
                "finish": data.get("finish"),
                "cost": data.get("cost") or 0,
                "tokens": tokens,
                "value": cost["total"] if cost else None,
                "cost_breakdown": cost,
                "created": time_info.get("created"),
                "completed": time_info.get("completed"),
                "cwd": (data.get("path") or {}).get("cwd"),
            })
        return indexed

    def _all_recs(self, go_only=False):
        for recs in self.requests.values():
            for r in recs:
                if go_only and not r["go"]:
                    continue
                yield r

    def _in_window(self, recs, start, end):
        return [r for r in recs if start <= r["ts"] <= end]

    def _sum_window(self, recs, start, end):
        reqs = 0
        value = 0.0
        cost = 0.0
        tokens = dict(ZERO_TOKENS)
        for r in recs:
            if not (start <= r["ts"] <= end):
                continue
            reqs += 1
            value += r["value"] if r["value"] is not None else 0.0
            cost += r["cost"] or 0
            _add_tokens(tokens, r["tokens"])
        return {"requests": reqs, "value": value, "cost": cost, "tokens": tokens}

    def overview(self):
        today_start, _ = window_bounds(self.now, "day")
        today_recs = self._sum_window(list(self._all_recs(go_only=True)), today_start, self.now)
        total_recs = self._sum_window(
            list(self._all_recs(go_only=True)), self.now - 31 * 86400 * 1000, self.now
        )
        plan = {}
        for w in VALUE_WINDOWS:
            start, end = window_bounds(self.now, w)
            used = self._sum_window(list(self._all_recs(go_only=True)), start, end)
            limit = self.config.plan_limit(w)
            plan[w] = {
                "value": used["value"],
                "cost": used["cost"],
                "limit": limit,
                "pct": round(used["value"] / limit * 100, 2) if limit else None,
                "requests": used["requests"],
            }
        session_map = {s["id"]: s for s in self.session_info}
        last_by_session = {}
        streaming_ids = set()
        for recs in self.requests.values():
            for r in recs:
                prev = last_by_session.get(r["session_id"])
                if prev is None or r["ts"] > prev["ts"]:
                    last_by_session[r["session_id"]] = r
                if r["completed"] is None:
                    streaming_ids.add(r["session_id"])
        active = []
        active_window = self.config.get("active_window_seconds", 90) * 1000
        for sid, last in last_by_session.items():
            if last["ts"] >= self.now - active_window:
                s = session_map.get(sid) or {}
                active.append({
                    "session_id": sid,
                    "title": s.get("title"),
                    "agent": last["agent"],
                    "model": last["model"],
                    "cwd": last["cwd"],
                    "streaming": sid in streaming_ids,
                    "last_ts": last["ts"],
                    "cost": last["cost"],
                })
        active.sort(key=lambda x: x["last_ts"], reverse=True)
        return {
            "now": self.now,
            "today": today_recs,
            "total_30d": total_recs,
            "plan": plan,
            "active_sessions": active,
            "models_used": len(self.requests),
        }

    def models(self):
        result = {}
        all_ids = set(self.requests.keys()) | set(self.config.model_ids())
        for model in sorted(all_ids):
            recs = self.requests.get(model, [])
            go_recs = [r for r in recs if r["go"]]
            configured = self.config.priced(model)
            entry = {
                "model": model,
                "configured": configured,
                "eligible": bool(go_recs) or configured,
                "windows": {},
                "unknown": not configured,
            }
            for w in VALUE_WINDOWS:
                start, end = window_bounds(self.now, w)
                used = self._sum_window(go_recs, start, end)
                req_limit = self.config.model_requests_limit(model, w)
                value_limit = self.config.model_value_limit(model, w)
                windows = {
                    "requests": {
                        "used": used["requests"],
                        "limit": req_limit,
                        "pct": round(used["requests"] / req_limit * 100, 2) if req_limit else None,
                        "remaining": (req_limit - used["requests"]) if req_limit is not None else None,
                    },
                    "value": {
                        "used": used["value"],
                        "cost": used["cost"],
                        "limit": value_limit,
                        "pct": round(used["value"] / value_limit * 100, 2) if value_limit else None,
                        "remaining": round(value_limit - used["value"], 4) if value_limit is not None else None,
                    },
                    "tokens": used["tokens"],
                }
                binding = None
                if windows["requests"]["used"] > 0 or windows["value"]["used"] > 0:
                    if windows["requests"]["pct"] is not None and windows["value"]["pct"] is not None:
                        binding = "requests" if windows["requests"]["pct"] >= windows["value"]["pct"] else "value"
                    elif windows["requests"]["pct"] is not None:
                        binding = "requests"
                    elif windows["value"]["pct"] is not None:
                        binding = "value"
                windows["binding"] = binding
                entry["windows"][w] = windows
            result[model] = entry
        return result

    def sessions(self, window="month_rolling", model=None, limit=100):
        start, end = window_bounds(self.now, window)
        session_map = {s["id"]: s for s in self.session_info}
        agg = {}
        for rec_model, rec_list in self.requests.items():
            if model and rec_model != self.config.normalize(model):
                continue
            for r in rec_list:
                if not r["go"]:
                    continue
                if not (start <= r["ts"] <= end):
                    continue
                a = agg.setdefault(r["session_id"], {
                    "session_id": r["session_id"],
                    "model": {},
                    "cost": 0.0,
                    "value": 0.0,
                    "requests": 0,
                    "tokens": dict(ZERO_TOKENS),
                    "first_ts": r["ts"],
                    "last_ts": r["ts"],
                })
                a["cost"] += r["cost"] or 0
                a["value"] += r["value"] if r["value"] is not None else 0.0
                a["requests"] += 1
                _add_tokens(a["tokens"], r["tokens"])
                a["first_ts"] = min(a["first_ts"], r["ts"])
                a["last_ts"] = max(a["last_ts"], r["ts"])
        out = []
        for sid, a in agg.items():
            s = session_map.get(sid) or {}
            m = s.get("model") or {}
            model_id = self.config.normalize(m.get("id") or "")
            if model and model_id != self.config.normalize(model):
                continue
            a["title"] = s.get("title")
            a["agent"] = s.get("agent")
            a["slug"] = s.get("slug")
            a["directory"] = s.get("directory")
            a["model"] = model_id or a["model"]
            a["duration_ms"] = a["last_ts"] - a["first_ts"]
            a["session_cost"] = s.get("cost") or 0
            out.append(a)
        out.sort(key=lambda x: x["last_ts"], reverse=True)
        return out[:limit]

    def series(self, days=None, model=None):
        days = days or self.config.get("series_days", 14)
        start = self.now - days * 86400 * 1000
        buckets = {}
        for i in range(days):
            day_start = start + i * 86400 * 1000
            key = datetime.datetime.fromtimestamp(day_start / 1000.0).strftime("%Y-%m-%d")
            buckets[key] = {
                "date": key,
                "value": 0.0,
                "cost": 0.0,
                "requests": 0,
                "tokens": dict(ZERO_TOKENS),
            }
        for rec_model, rec_list in self.requests.items():
            if model and rec_model != self.config.normalize(model):
                continue
            for r in rec_list:
                if not r["go"]:
                    continue
                key = datetime.datetime.fromtimestamp(r["ts"] / 1000.0).strftime("%Y-%m-%d")
                b = buckets.setdefault(key, {
                    "date": key, "value": 0.0, "cost": 0.0,
                    "requests": 0, "tokens": dict(ZERO_TOKENS),
                })
                b["value"] += r["value"] if r["value"] is not None else 0.0
                b["cost"] += r["cost"] or 0
                b["requests"] += 1
                _add_tokens(b["tokens"], r["tokens"])
        ordered = [buckets[k] for k in sorted(buckets.keys())]
        return {"days": days, "start": start, "end": self.now, "series": ordered}
