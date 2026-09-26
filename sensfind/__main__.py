"""命令行入口：python -m sensfind [文件路径]（缺省读 stdin），输出 JSON。"""
import json
import sys

from .engine import Engine


def main(argv):
    threshold = 0.6
    args = list(argv[1:])
    if "--threshold" in args:
        i = args.index("--threshold")
        threshold = float(args[i + 1])
        del args[i:i + 2]
    if args:
        with open(args[0], encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = sys.stdin.read()
    result = Engine(threshold=threshold).scan(text)
    json.dump(result.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv)
