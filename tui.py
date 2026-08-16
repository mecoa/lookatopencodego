#!/usr/bin/env python3
"""opencode-go 实时用量监控 - 终端面板 (curses)。

通过 HTTP API 消费后端 (opencode_mon/server.py)。

用法:
    python3 server.py --port 8932   # 先启动后端
    python3 tui.py [--url http://127.0.0.1:8932]
"""

import argparse
import curses
import datetime
import json
import locale
import sys
import urllib.request

sys.path.insert(0, ".")

WINDOW_LABELS = {
    "5h": "5h",
    "week_rolling": "7d",
    "week": "本周",
    "month_rolling": "30d",
    "month": "本月",
}
DEFAULT_WINDOWS = ("5h", "week", "month")

CP_OK = 1
CP_WARN = 2
CP_CRIT = 3
CP_HDR = 4
CP_DIM = 5
CP_HI = 6


def color_for(pct):
    if pct is None:
        return CP_DIM
    if pct >= 90:
        return CP_CRIT
    if pct >= 60:
        return CP_WARN
    return CP_OK


def api_get(base, path, timeout=4):
    with urllib.request.urlopen(base + path, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fmt_value(v):
    if v is None:
        return "    -"
    return "$%6.2f" % v


def fmt_pct(pct):
    if pct is None:
        return "   -"
    return "%4.1f%%" % pct


def fmt_req(used, limit):
    if limit is None:
        return "%5d/  -" % used
    return "%5d/%d" % (used, limit)


def bar(width, pct):
    if pct is None:
        return " " * width
    fill = int(round(width * min(pct, 100) / 100))
    return "█" * fill + "░" * (width - fill)


def ts_to_str(ts_ms):
    return datetime.datetime.fromtimestamp(ts_ms / 1000.0).strftime("%H:%M:%S")


class Tui:
    def __init__(self, base_url, refresh):
        self.base_url = base_url
        self.refresh = max(1, refresh)
        self.live_events = []
        self.last_seq = 0
        self.model_scroll = 0
        self.feed_scroll = 0
        self.active_panel = "models"
        self.last_error = ""

    def fetch(self, path):
        return api_get(self.base_url, path)

    def refresh_live(self):
        try:
            data = self.fetch("/api/live?since=%d" % self.last_seq)
        except Exception:
            return
        if data["seq"] <= self.last_seq:
            return
        for ev in data["events"]:
            self.live_events.append(ev)
        self.live_events = self.live_events[-60:]
        self.last_seq = data["seq"]

    def draw(self, stdscr):
        curses.curs_set(0)
        stdscr.nodelay(1)
        stdscr.timeout(self.refresh * 1000)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(CP_OK, curses.COLOR_GREEN, -1)
        curses.init_pair(CP_WARN, curses.COLOR_YELLOW, -1)
        curses.init_pair(CP_CRIT, curses.COLOR_RED, -1)
        curses.init_pair(CP_HDR, curses.COLOR_CYAN, -1)
        curses.init_pair(CP_DIM, curses.COLOR_WHITE, -1)
        curses.init_pair(CP_HI, curses.COLOR_BLACK, curses.COLOR_WHITE)

        overview = {}
        models = {}
        while True:
            try:
                overview = self.fetch("/api/overview")
                models = self.fetch("/api/models")
                self.refresh_live()
                self.last_error = ""
            except Exception as exc:
                self.last_error = "API 连接失败: %s" % exc

            self.render(stdscr, overview, models)
            key = stdscr.getch()
            if key == ord("q"):
                break
            elif key == ord("j") or key == curses.KEY_DOWN:
                if self.active_panel == "models":
                    self.model_scroll += 1
                else:
                    self.feed_scroll += 1
            elif key == ord("k") or key == curses.KEY_UP:
                if self.active_panel == "models":
                    self.model_scroll = max(0, self.model_scroll - 1)
                else:
                    self.feed_scroll = max(0, self.feed_scroll - 1)
            elif key == ord("n") or key == ord("\t"):
                self.active_panel = "feed" if self.active_panel == "models" else "models"
                self.model_scroll = 0
                self.feed_scroll = 0

    def render(self, stdscr, overview, models):
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        self.draw_header(stdscr, overview, w)
        plan_h = self.draw_plan(stdscr, overview, w)
        self.draw_feed(stdscr, overview, w)
        self.draw_models(stdscr, models, w, plan_h)
        stdscr.refresh()

    def draw_header(self, stdscr, overview, w):
        stdscr.addnstr(0, 0, " OpenCode Go 实时用量监控 ", w, curses.color_pair(CP_HI))
        line = "  刷新:%ss  活跃:%d  今日:$%.2f" % (
            self.refresh,
            len(overview.get("active_sessions", [])),
            (overview.get("today") or {}).get("value", 0),
        )
        stdscr.addnstr(1, 0, line.ljust(w), w, curses.color_pair(CP_DIM))
        if self.last_error:
            stdscr.addnstr(1, 0, (" " + self.last_error).ljust(w)[:w], w,
                           curses.color_pair(CP_CRIT))

    def draw_plan(self, stdscr, overview, w):
        plan = overview.get("plan", {})
        y = 2
        stdscr.addnstr(y, 0, " 计划额度 (美元值, 全模型合计)", w, curses.color_pair(CP_HDR))
        y += 1
        for key in ("5h", "week", "month"):
            item = plan.get(key)
            if not item:
                continue
            pct = item.get("pct")
            bw = max(4, w - 40)
            stdscr.addnstr(y, 0, " %-5s " % key, w)
            stdscr.addnstr(y, 7, bar(bw, pct)[:w - 40], w - 40, curses.color_pair(color_for(pct)))
            text = " %s / %s  %s  请求:%d" % (
                fmt_value(item.get("value")),
                fmt_value(item.get("limit")),
                fmt_pct(pct),
                item.get("requests", 0),
            )
            stdscr.addnstr(y, 7 + bw, text[: w - 7 - bw], w - 7 - bw)
            y += 1
        acct = overview.get("account") or {}
        awins = acct.get("windows") or {}
        if acct.get("configured"):
            parts = []
            for key in ("rolling", "weekly", "monthly"):
                item = awins.get(key) or {}
                pct = item.get("percent")
                if pct is not None:
                    parts.append("%s:%d%%" % ({"rolling": "5h", "weekly": "周", "monthly": "月"}[key], pct))
            src = acct.get("key_source") or ""
            suffix = " (key:%s)" % src if src else ""
            line = " 官方额度(服务器,跨设备)   " + "  ".join(parts) + suffix + "   " + (acct.get("error") or "")
            stdscr.addnstr(y, 0, line[:w], w, curses.color_pair(CP_HI))
        else:
            stdscr.addnstr(y, 0, " 官方额度: 未配置(仅本机,跨设备可能不准)", w, curses.color_pair(CP_DIM))
        y += 1
        return y + 1

    def draw_models(self, stdscr, models, w, plan_h):
        h, _ = stdscr.getmaxyx()
        entries = [m for m in models.values() if m.get("eligible")]
        entries.sort(key=lambda m: (
            -sum((m.get("windows") or {}).get(win, {}).get("value", {}).get("used", 0)
                 for win in DEFAULT_WINDOWS),
            m["model"],
        ))
        feed_rows = max(6, int(h * 0.28))
        table_h = h - plan_h - feed_rows
        table_h = max(3, table_h)
        if self.model_scroll > max(0, len(entries) - table_h + 2):
            self.model_scroll = max(0, len(entries) - table_h + 2)
        stdscr.addnstr(plan_h, 0, " 模型额度   (5h / 本周 / 本月)    [j/k 滚动, Tab 切换, q 退出]",
                       w, curses.color_pair(CP_HDR))
        hdr = (" %-26s %-16s %-16s %-16s" % ("模型", "5h 请求/$", "本周 请求/$", "本月 请求/$"))
        stdscr.addnstr(plan_h + 1, 0, hdr[:w], w, curses.color_pair(CP_DIM))
        y = plan_h + 2
        for entry in entries[self.model_scroll:]:
            if y >= h - feed_rows - 1:
                stdscr.addnstr(y, 0, (" ... 更多 %d 个 (↓)" % (len(entries) - self.model_scroll - (y - plan_h - 2))), w, curses.color_pair(CP_DIM))
                break
            win = entry.get("windows") or {}
            cols = []
            for key in DEFAULT_WINDOWS:
                item = win.get(key) or {}
                reqs = item.get("requests") or {}
                val = item.get("value") or {}
                binding = item.get("binding")
                mark = " *" if binding == "requests" else (" $" if binding == "value" else "  ")
                cols.append("%s%s" % (fmt_req(reqs.get("used"), reqs.get("limit")), mark))
            name = entry["model"]
            style = CP_HI if entry.get("unknown") else CP_OK
            line = " %-26s %-16s %-16s %-16s" % (name, cols[0], cols[1], cols[2])
            stdscr.addnstr(y, 0, line[:w], w, curses.color_pair(style))
            y += 1

    def draw_feed(self, stdscr, overview, w):
        h, _ = stdscr.getmaxyx()
        active = overview.get("active_sessions", [])
        feed_rows = max(6, int(h * 0.28))
        y = h - feed_rows
        stdscr.addnstr(y, 0, " 活跃会话与实时事件", w, curses.color_pair(CP_HDR))
        y += 1
        for a in active[:2]:
            mark = "●流" if a.get("streaming") else "○"
            stdscr.addnstr(y, 0, (" %s %-18s %-8s %s" % (
                mark, a.get("model") or "-", a.get("agent") or "-",
                (a.get("title") or "")[:40])), w, curses.color_pair(CP_HI))
            y += 1
        if not active:
            stdscr.addnstr(y, 0, " (无活跃会话)", w, curses.color_pair(CP_DIM))
            y += 1
        y += 0
        visible = feed_rows - (y - (h - feed_rows)) - 1
        events = list(reversed(self.live_events[-max(0, visible):]))
        if self.feed_scroll:
            events = list(reversed(self.live_events))
        ev_rows = visible
        start_idx = max(0, len(events) - ev_rows)
        for ev in events[start_idx:]:
            if y >= h:
                break
            self.draw_event(stdscr, y, w, ev)
            y += 1

    def draw_event(self, stdscr, y, w, ev):
        ev_type = (ev.get("type") or "").replace(".updated.1", "").replace("message.", "")
        parts = []
        if ev.get("role"):
            parts.append("role=" + str(ev["role"]))
        if ev.get("model"):
            parts.append("model=" + str(ev["model"]))
        if ev.get("part_type"):
            parts.append("part=" + str(ev["part_type"]))
        if ev.get("cost") is not None:
            parts.append("cost=%.5f" % ev["cost"])
        if ev.get("tokens"):
            t = ev["tokens"]
            parts.append("tok=%s/%s" % (t.get("input", 0), t.get("output", 0)))
        body = " %-22s %s" % (ev_type, " ".join(parts))
        stdscr.addnstr(y, 0, body[:w], w, curses.color_pair(CP_DIM))


def main():
    ap = argparse.ArgumentParser(description="OpenCode Go usage monitor - terminal dashboard")
    ap.add_argument("--url", default="http://127.0.0.1:8932", help="API 服务地址")
    ap.add_argument("--refresh", type=int, default=2, help="刷新间隔秒数")
    args = ap.parse_args()

    locale.setlocale(locale.LC_ALL, "")
    tui = Tui(args.url.rstrip("/"), args.refresh)
    try:
        api_get(args.url.rstrip("/"), "/api/overview", timeout=4)
    except Exception:
        print("无法连接 API 服务: %s" % args.url.rstrip("/"))
        print("请先运行: python3 server.py --port 8932")
        sys.exit(1)
    curses.wrapper(tui.draw)


if __name__ == "__main__":
    main()
