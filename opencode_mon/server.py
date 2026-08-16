"""Zero-dependency JSON REST API + static frontend hosting."""

import json
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import account, policy
from .config import Config, WINDOW_ORDER
from .db import OpenCodeDB
from .metrics import Metrics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")

CURRENT_CONFIG_PATH = ""

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


def _live_event(ev):
    data = ev.get("data") or {}
    compact = {"seq": ev["seq"], "type": ev["type"]}
    info = data.get("info") or {}
    session_id = data.get("sessionID") or info.get("sessionID")
    if session_id:
        compact["session_id"] = session_id
    model = info.get("model") or {}
    if model:
        compact["model"] = model.get("id")
    if info.get("modelID"):
        compact["model"] = info.get("modelID")
    if info.get("cost") is not None:
        compact["cost"] = info["cost"]
    if info.get("tokens"):
        compact["tokens"] = info["tokens"]
    if info.get("role"):
        compact["role"] = info["role"]
    if data.get("part"):
        part = data["part"]
        compact["part_type"] = part.get("type")
        if part.get("type") == "step-finish":
            compact["cost"] = part.get("cost")
            compact["tokens"] = part.get("tokens")
    return compact


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenCodeMon/1.0"

    def log_message(self, format, *args):
        return

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message, status=400):
        self._send_json({"error": message}, status=status)

    def _serve_docs_file(self, rel):
        full = os.path.normpath(os.path.join(os.path.join(ROOT, "docs"), rel))
        base = os.path.normpath(os.path.join(ROOT, "docs"))
        if not full.startswith(base) or not os.path.isfile(full):
            self._send_error_json("not found", 404)
            return
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)):
            self._send_error_json("forbidden", 403)
            return
        if not os.path.isfile(full):
            self._send_error_json("not found", 404)
            return
        ext = os.path.splitext(full)[1]
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path.startswith("/api/"):
            self._handle_api(path[len("/api/"):], query)
        else:
            self._serve_static(path)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/api/policy/refresh":
            self._handle_policy_refresh(query)
        else:
            self._send_error_json("unknown endpoint", 404)

    def _handle_policy_refresh(self, query):
        try:
            config = Config(CURRENT_CONFIG_PATH)
            dry_run = query.get("dry_run", ["0"])[0] not in ("0", "false", "no", "")
            result = policy.refresh_policy(config, CURRENT_CONFIG_PATH, dry_run=dry_run)
            self._send_json(result)
        except Exception as exc:
            self._send_error_json("internal error: %s" % exc, 500)

    @staticmethod
    def _policy_payload(config):
        docs_url = (config.get("policy") or {}).get("docs_url") \
            or os.environ.get("OPENCODE_MON_DOCS_URL") or policy.DEFAULT_DOCS_URL
        return {
            "plan_limits": config.plan_limits(),
            "models": config.models_data,
            "docs_url": docs_url,
            "generated_at": int(time.time() * 1000),
        }

    def _handle_api(self, name, query):
        try:
            config = Config(CURRENT_CONFIG_PATH)
            db = OpenCodeDB(config.db_path)
            if not db.exists():
                self._send_error_json("opencode database not found at %s" % config.db_path, 503)
                return
            metrics = Metrics(config, db)
            if name == "config":
                self._send_json(self._config_payload(config))
            elif name == "overview":
                payload = metrics.overview()
                payload["account"] = account.get_account(config)
                self._send_json(payload)
            elif name == "account":
                self._send_json(account.get_account(config, force=True))
            elif name == "models":
                payload = metrics.models()
                model = query.get("model", [None])[0]
                if model:
                    norm = config.normalize(model)
                    if norm not in payload:
                        self._send_error_json("model not found: %s" % model, 404)
                        return
                    self._send_json(payload[norm])
                else:
                    self._send_json(payload)
            elif name == "policy":
                self._send_json(self._policy_payload(config))
            elif name == "openapi.json":
                self._serve_docs_file("openapi.json")
            elif name == "sessions":
                window = query.get("window", ["month_rolling"])[0]
                model = query.get("model", [None])[0]
                limit = int(query.get("limit", ["200"])[0])
                self._send_json(metrics.sessions(window=window, model=model, limit=limit))
            elif name == "series":
                days = int(query.get("days", [str(config.get("series_days", 14))])[0])
                model = query.get("model", [None])[0]
                self._send_json(metrics.series(days=days, model=model))
            elif name == "live":
                since = int(query.get("since", ["0"])[0])
                max_seq = db.max_event_seq()
                events = db.events_after(since)
                self._send_json({
                    "seq": max_seq,
                    "events": [_live_event(ev) for ev in events],
                })
            else:
                self._send_error_json("unknown endpoint", 404)
        except Exception as exc:
            self._send_error_json("internal error: %s" % exc, 500)

    @staticmethod
    def _config_payload(config):
        acct = config.account_cfg()
        return {
            "refresh_interval": config.get("refresh_interval", 2),
            "db_path": config.db_path,
            "windows": list(WINDOW_ORDER),
            "plan": {
                "limits": config.plan_limits(),
            },
            "account": {
                "enabled": bool(acct.get("enabled", True)),
                "configured": bool(account.load_api_key(config)[0]),
                "key_source": account.load_api_key(config)[1],
                "refresh_seconds": acct.get("refresh_seconds", 60),
            },
            "models": {
                m: {
                    "priced": True,
                    "allowance_month": (config.model_cfg(m) or {}).get("allowance_month"),
                    "requests": (config.model_cfg(m) or {}).get("requests"),
                }
                for m in config.model_ids()
            },
            "generated_at": int(time.time() * 1000),
        }


def create_server(config_path: str, host: str | None = None, port: int | None = None):
    config = Config(config_path)
    h = host if host is not None else str(config.get("host", "127.0.0.1"))
    p = port if port is not None else int(config.get("port", 8932) or 8932)
    httpd = MonitorServer((h, p), Handler)
    global CURRENT_CONFIG_PATH
    CURRENT_CONFIG_PATH = config_path
    return httpd


def run(config_path, host=None, port=None, daemon=False):
    httpd = create_server(config_path, host, port)
    url = "http://%s:%d" % httpd.server_address[:2]
    if daemon:
        httpd.daemon_threads = True
    httpd.serve_forever()
    return url
