"""Official server-side usage via the OpenCode Go account.

The Go subscription tracks usage server-side (rolling 5h / weekly / monthly
windows tied to the server clock). Local DB estimates cannot match those
windows, so when the Go API key is available we prefer the official numbers.
"""

import datetime
import json
import os
import threading
import time
import urllib.request

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_cache = {}
_cache_lock = threading.Lock()


def load_api_key(config):
    """Resolve the Go API key and report its source.

    Priority: config.account.api_key -> config.account.api_key_file ->
    env OPENCODE_GO_API_KEY -> opencode auth.json. Returns (key, source).
    """
    account = config.account_cfg()
    key = (account.get("api_key") or "").strip()
    if key:
        return key, "config"
    key_file = (account.get("api_key_file") or "").strip()
    if key_file:
        path = os.path.expanduser(key_file)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    key = fh.read().strip().splitlines()[0].strip() if fh.read().strip() else ""
            except (OSError, IndexError):
                key = ""
            if key:
                return key, "file"
    key = os.environ.get("OPENCODE_GO_API_KEY", "").strip()
    if key:
        return key, "env"
    auth_path = os.path.expanduser(account.get("auth_json_path") or "")
    if not auth_path or not os.path.isfile(auth_path):
        return "", ""
    try:
        with open(auth_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        provider = data.get("opencode-go") or {}
        key = (provider.get("key") or "").strip()
    except (ValueError, OSError, AttributeError):
        return "", ""
    return (key, "auth.json") if key else ("", "")


def _parse_ts(value):
    if not value:
        return None
    try:
        s = value.replace("Z", "+00:00")
        return int(datetime.datetime.fromisoformat(s).timestamp() * 1000)
    except ValueError:
        return None


def normalize_usage(raw, config):
    """Convert the official payload into window summaries with limits."""
    usage = (raw or {}).get("usage") or {}
    limits = {
        "rolling": config.plan_limit("5h"),
        "weekly": config.plan_limit("week"),
        "monthly": config.plan_limit("month"),
    }
    out = {}
    for key, limit in limits.items():
        item = usage.get(key) or {}
        percent = item.get("percent")
        if percent is None:
            out[key] = {"status": item.get("status"), "percent": None}
            continue
        used = round(limit * percent / 100.0, 4) if limit else None
        out[key] = {
            "status": item.get("status"),
            "percent": percent,
            "resets_at": item.get("resetsAt"),
            "resets_at_ms": _parse_ts(item.get("resetsAt")),
            "used": used,
            "limit": limit,
            "remaining": round(limit - used, 4) if limit is not None else None,
        }
    return out


def _http_get(config, key):
    account = config.account_cfg()
    url = account.get("endpoint") or "https://opencode.ai/zen/go/v1/usage"
    headers = {
        "Authorization": "Bearer " + key,
        "User-Agent": account.get("user_agent") or DEFAULT_UA,
        "Accept": "application/json",
        "Origin": "https://opencode.ai",
        "Referer": "https://opencode.ai/",
    }
    req = urllib.request.Request(url, headers=headers)
    timeout = float(account.get("timeout_seconds", 15))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_account(config, force=False):
    """Return official usage with a short cache. Never raises."""
    account = config.account_cfg()
    if not account.get("enabled", True):
        return {"configured": False, "reason": "disabled", "windows": {}, "error": None}

    key, key_source = load_api_key(config)
    if not key:
        return {
            "configured": False,
            "reason": "no-api-key",
            "key_source": None,
            "windows": {},
            "error": None,
        }

    ttl = float(account.get("refresh_seconds", 60))
    now = time.time()
    with _cache_lock:
        cached = _cache.get(config.path)
    if cached and not force and (now - cached[0]) < ttl:
        return cached[1]

    try:
        raw = _http_get(config, key)
        payload = {
            "configured": True,
            "key_source": key_source,
            "source": "official",
            "fetched_at": int(now * 1000),
            "windows": normalize_usage(raw, config),
            "error": None,
        }
    except Exception as exc:
        payload = {
            "configured": True,
            "key_source": key_source,
            "source": "official",
            "fetched_at": int(now * 1000),
            "windows": {},
            "error": "%s" % exc,
        }
        if cached:
            payload["cached_windows"] = cached[1].get("windows", {})
    with _cache_lock:
        _cache[config.path] = (now, payload)
    return payload
