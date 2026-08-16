#!/usr/bin/env python3
"""opencode-go 实时用量监控 API 服务入口。

用法:
    python3 server.py [--config config.json] [--host 127.0.0.1] [--port 8932]
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from opencode_mon.config import Config
from opencode_mon.server import create_server


def main():
    ap = argparse.ArgumentParser(description="OpenCode Go usage monitor API server")
    ap.add_argument("--config", default="config.json", help="配置文件路径")
    ap.add_argument("--host", default=None, help="监听地址")
    ap.add_argument("--port", type=int, default=None, help="监听端口")
    args = ap.parse_args()

    cfg = Config(args.config)
    httpd = create_server(args.config, args.host, args.port)
    url = "http://%s:%d" % httpd.server_address[:2]
    print("OpenCode Go usage monitor API on %s" % url)
    print("  Web UI : %s" % url)
    print("  API    : %s/api/overview" % url)
    print("  DB     : %s" % cfg.db_path)
    print("Press Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
