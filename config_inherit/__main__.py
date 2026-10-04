"""命令行入口：

    python3 -m config_inherit --config-dir examples --env prod.cn
    python3 -m config_inherit --config-dir examples --env prod.cn --provenance
    python3 -m config_inherit --config-dir examples --env prod.cn --write examples/output
"""

import argparse
import json
import sys

from .loader import ConfigError, load_config


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="config_inherit",
        description="多环境配置继承：base -> 环境继承链 -> local",
    )
    parser.add_argument("--config-dir", required=True, help="配置根目录（含 base.json 与 envs/）")
    parser.add_argument("--env", required=True, help="目标环境名，如 prod.cn")
    parser.add_argument("--no-local", action="store_true", help="不叠加 local.json 层")
    parser.add_argument("--provenance", action="store_true", help="输出溯源数据而非合并结果")
    parser.add_argument("--write", metavar="DIR", help="把 merged.json 与 provenance.json 写入指定目录")
    args = parser.parse_args(argv)

    try:
        merged, provenance = load_config(args.config_dir, args.env, local=not args.no_local)
    except ConfigError as exc:
        print("配置错误: %s" % exc, file=sys.stderr)
        return 2

    if args.write:
        import os

        os.makedirs(args.write, exist_ok=True)
        for name, payload in (("merged.json", merged), ("provenance.json", provenance)):
            path = os.path.join(args.write, name)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
        print("已写入 %s/{merged.json,provenance.json}" % args.write)

    payload = provenance if args.provenance else merged
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
