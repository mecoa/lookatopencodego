"""Fetch and parse the official OpenCode Go docs into a machine-readable policy.

The subscription limits, per-model prices and estimated request quotas change as
OpenCode releases models. This module downloads the docs markdown, extracts the
tables, and can write the result back into config.json (with a backup).
"""

import json
import os
import re
import shutil
import time
import urllib.request

DEFAULT_DOCS_URL = (
    "https://raw.githubusercontent.com/anomalyco/opencode/dev/"
    "packages/web/src/content/docs/zh-cn/go.mdx"
)
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def fetch_docs(config):
    """Return the docs markdown text from the configured URL or a local file."""
    policy_cfg = config.get("policy") or {}
    url = policy_cfg.get("docs_url") or os.environ.get("OPENCODE_MON_DOCS_URL") or DEFAULT_DOCS_URL
    if url.startswith("file://"):
        with open(url[len("file://"):], "r", encoding="utf-8") as fh:
            return fh.read()
    if os.path.isfile(url):
        with open(url, "r", encoding="utf-8") as fh:
            return fh.read()
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA, "Accept": "text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def parse_markdown_tables(text):
    """Parse all markdown pipe tables into (header, rows) pairs."""
    tables = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        block = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            block.append(lines[i])
            i += 1
        rows = []
        for ln in block:
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            rows.append(cells)
        if len(rows) < 2:
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in rows[1]):
            header, body = rows[0], rows[2:]
        else:
            header, body = rows[0], rows[1:]
        tables.append((header, body))
    return tables


def _norm_name(s):
    s = re.sub(r"\(.*?\)", " ", s)
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _to_float(s):
    s = s.strip().replace(",", "").replace("$", "").replace("USD", "").strip()
    if s in ("-", "", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(s):
    s = s.strip().replace(",", "").replace("$", "").strip()
    if s in ("-", "", "—"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _slug_id(name):
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


def _tier_marker(name):
    m = re.search(r"\((≤|>|>=|<|>=|≤)\s*([\d,]+)\s*K\s*tokens\)", name, re.IGNORECASE)
    if not m:
        return None, name
    op, num = m.group(1), int(m.group(2).replace(",", "")) * 1000
    base = name[: m.start()].strip()
    if op in ("≤", "<", "<="):
        return ("le", num), base
    return ("gt", None), base


def parse_policy(text):
    """Parse plan limits, per-model pricing and request quotas from docs."""
    tables = parse_markdown_tables(text)
    requests_map = {}
    pricing_rows = []
    model_ids = {}
    for header, body in tables:
        cols = [c.lower() for c in header]
        joined = " ".join(cols)
        if "请求数" in joined and ("5 小时" in joined or "model" in joined or "模型" in joined):
            for row in body:
                if len(row) < 4:
                    continue
                requests_map[_norm_name(row[0])] = {
                    "5h": _to_int(row[1]),
                    "week": _to_int(row[2]),
                    "month": _to_int(row[3]),
                }
        elif "缓存读取" in joined and "使用额度" in joined:
            for row in body:
                if len(row) < 6:
                    continue
                pricing_rows.append(row)
        elif "模型 id" in joined:
            for row in body:
                if len(row) < 2:
                    continue
                model_ids[_norm_name(row[0])] = row[1].strip()

    plan = {}
    m5 = re.search(r"(\d+)\s*小时限制.*?(\d+)\s*美元", text)
    mw = re.search(r"每周限制.*?(\d+)\s*美元", text)
    mm = re.search(r"每月限制.*?(\d+)\s*美元", text)
    if m5:
        plan["5h"] = float(m5.group(2))
    if mw:
        plan["week"] = float(mw.group(1))
    if mm:
        plan["month"] = float(mm.group(1))

    groups = {}
    for row in pricing_rows:
        name = row[0]
        marker, base = _tier_marker(name)
        nbase = _norm_name(base)
        model_id = model_ids.get(nbase) or _slug_id(base)
        price = {
            "input": _to_float(row[1]),
            "output": _to_float(row[2]),
            "cache_read": _to_float(row[3]),
            "cache_write": _to_float(row[4]),
            "allowance_month": _to_float(row[5]),
        }
        group = groups.setdefault(model_id, {"name": base, "tiers": []})
        if marker:
            group["tiers"].append((marker, price))
        else:
            group["flat"] = price

    models = {}
    for model_id, group in groups.items():
        entry = {"requests": requests_map.get(_norm_name(group["name"])) or {
            "5h": None, "week": None, "month": None}}
        if group.get("tiers"):
            tiers = []
            for marker, price in sorted(group["tiers"], key=lambda t: (t[1]["input"] or 0)):
                is_le = marker[0] == "le"
                tiers.append({
                    "max_total": marker[1] if is_le else None,
                    "input": price["input"],
                    "output": price["output"],
                    "cache_read": price["cache_read"],
                    "cache_write": price["cache_write"],
                })
                entry["allowance_month"] = price["allowance_month"]
            entry["tiers"] = tiers
        elif "flat" in group:
            p = group["flat"]
            entry.update({
                "input": p["input"],
                "output": p["output"],
                "cache_read": p["cache_read"],
                "cache_write": p["cache_write"],
                "allowance_month": p["allowance_month"],
            })
        models[model_id] = entry

    return {
        "plan_limits": plan,
        "models": models,
        "model_ids": {v: k for k, v in model_ids.items()},
    }


def diff_policy(current_models, policy_models):
    """Compare current config models with a fresh policy."""
    added = sorted(set(policy_models) - set(current_models))
    removed = sorted(set(current_models) - set(policy_models))
    changed = []
    for model_id in sorted(set(policy_models) & set(current_models)):
        if policy_models[model_id] != current_models[model_id]:
            changed.append(model_id)
    return {"added": added, "removed": removed, "changed": changed}


def update_config_file(config_path, policy, backup=True):
    """Write the parsed policy into config.json (with a timestamped backup)."""
    with open(config_path, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    original = json.dumps(config, ensure_ascii=False, indent=2)

    go_plan = config.setdefault("go_plan", {})
    limits = go_plan.setdefault("limits", {})
    limits.update({k: v for k, v in policy["plan_limits"].items() if v is not None})

    models = config.setdefault("models", {})
    before = {k: dict(v) for k, v in models.items()}
    for model_id, entry in policy["models"].items():
        existing = models.get(model_id) or {}
        merged = dict(existing)
        merged.update(entry)
        models[model_id] = merged

    bak = None
    if backup:
        bak = "%s.bak-%d" % (config_path, int(time.time()))
        shutil.copyfile(config_path, bak)
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, ensure_ascii=False, indent=2)

    return {
        "backup": bak if backup else None,
        "diff": diff_policy(before, policy["models"]),
        "written": original != json.dumps(config, ensure_ascii=False, indent=2),
    }


def refresh_policy(config, config_path, dry_run=True):
    """Fetch the latest policy; update config.json unless dry_run."""
    text = fetch_docs(config)
    policy = parse_policy(text)
    result = {
        "fetched_at": int(time.time() * 1000),
        "docs_url": (config.get("policy") or {}).get("docs_url")
        or os.environ.get("OPENCODE_MON_DOCS_URL")
        or DEFAULT_DOCS_URL,
        "plan_limits": policy["plan_limits"],
        "models": policy["models"],
    }
    if dry_run:
        result["dry_run"] = True
        result["diff"] = diff_policy(config.models_data, policy["models"])
        return result
    result["dry_run"] = False
    result["write"] = update_config_file(config_path, policy)
    return result
