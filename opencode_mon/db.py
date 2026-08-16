"""Read-only access to the opencode SQLite database (WAL safe)."""

import json
import os
import sqlite3


class OpenCodeDB:
    def __init__(self, path):
        self.path = os.path.expanduser(path)

    def exists(self):
        return os.path.isfile(self.path)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA query_only = ON")
        return conn

    def sessions(self) -> list:
        if not self.exists():
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, project_id, parent_id, slug, directory, title, agent, model, "
                "cost, tokens_input, tokens_output, tokens_reasoning, "
                "tokens_cache_read, tokens_cache_write, time_created, time_updated "
                "FROM session"
            ).fetchall()
        out = []
        for r in rows:
            model = None
            if r["model"]:
                try:
                    model = json.loads(r["model"])
                except (ValueError, TypeError):
                    model = {"id": r["model"]}
            out.append({
                "id": r["id"],
                "project_id": r["project_id"],
                "slug": r["slug"],
                "directory": r["directory"],
                "title": r["title"],
                "agent": r["agent"],
                "model": model,
                "cost": r["cost"] or 0,
                "tokens": {
                    "input": r["tokens_input"] or 0,
                    "output": r["tokens_output"] or 0,
                    "reasoning": r["tokens_reasoning"] or 0,
                    "cache_read": r["tokens_cache_read"] or 0,
                    "cache_write": r["tokens_cache_write"] or 0,
                },
                "time_created": r["time_created"],
                "time_updated": r["time_updated"],
            })
        return out

    def messages(self, min_created=0) -> list:
        if not self.exists():
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, session_id, time_created, time_updated, data "
                "FROM message WHERE time_created >= ? ORDER BY time_created",
                (min_created,),
            ).fetchall()
        out = []
        for r in rows:
            data = None
            if r["data"]:
                try:
                    data = json.loads(r["data"])
                except (ValueError, TypeError):
                    data = None
            if data is None:
                continue
            out.append({
                "id": r["id"],
                "session_id": r["session_id"],
                "time_created": r["time_created"],
                "time_updated": r["time_updated"],
                "data": data,
            })
        return out

    def max_event_seq(self):
        if not self.exists():
            return 0
        with self.connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM event").fetchone()
        return row[0] or 0

    def events_after(self, since_seq, limit=500):
        if not self.exists():
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT seq, type, data FROM event "
                "WHERE seq > ? ORDER BY seq LIMIT ?",
                (since_seq, limit),
            ).fetchall()
        out = []
        for r in rows:
            data = None
            if r["data"]:
                try:
                    data = json.loads(r["data"])
                except (ValueError, TypeError):
                    data = None
            out.append({"seq": r[0], "type": r[1], "data": data})
        return out
