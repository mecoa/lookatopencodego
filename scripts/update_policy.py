#!/usr/bin/env python3
"""从官方文档抓取并更新 OpenCode Go 政策信息到 policy.json（不入库）。

用法:
    python3 scripts/update_policy.py            # 预览（不落盘）
    python3 scripts/update_policy.py --apply    # 写回 policy.json（自动备份）
    python3 scripts/update_policy.py --config config.json --apply
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opencode_mon.config import Config
from opencode_mon import policy


def main():
    ap = argparse.ArgumentParser(description="Update OpenCode Go policy from official docs")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--apply", action="store_true", help="写回 policy.json（默认仅预览）")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args()

    try:
        cfg = Config(args.config)
        result = policy.refresh_policy(cfg, cfg.policy_path, dry_run=not args.apply)
    except policy.PolicyError as exc:
        print("更新失败: %s" % exc, file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print("更新失败: %s" % exc, file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    limits = result["plan_limits"]
    models = result["models"]
    diff = result.get("diff", {})
    print("官方文档来源:", result["docs_url"])
    print("政策文件:", result["policy_file"])
    print("计划限额:", ", ".join("%s=$%s" % (k, v) for k, v in limits.items()))
    print("解析模型数:", len(models))
    print("新增: %s" % diff.get("added", []))
    print("移除: %s" % diff.get("removed", []))
    print("变更: %s" % diff.get("changed", []))

    if result.get("dry_run"):
        print("\n[预览模式] 未写回。加 --apply 以应用（将备份 policy.json）。")
    else:
        write = result.get("write", {})
        print("\n已写回 policy.json（备份: %s）" % write.get("backup"))


if __name__ == "__main__":
    main()
