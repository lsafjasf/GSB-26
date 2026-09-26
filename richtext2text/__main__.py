"""Command line interface: python -m richtext2text [options] [input]"""

import argparse
import dataclasses
import json
import sys

from .converter import Config, DEFAULT_CONFIG, convert


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="richtext2text",
        description="Convert HTML rich text to plain text.")
    parser.add_argument("input", nargs="?",
                        help="HTML input file (default: stdin)")
    parser.add_argument("-o", "--output",
                        help="output file (default: stdout)")
    parser.add_argument("--config", metavar="JSON_FILE",
                        help="JSON file with config overrides")
    parser.add_argument("--link-mode", choices=["url", "text"],
                        help="keep link URLs or only link text")
    args = parser.parse_args(argv)

    cfg = DEFAULT_CONFIG
    if args.config:
        with open(args.config, "r", encoding="utf-8") as fh:
            cfg = Config.from_dict(json.load(fh))
    if args.link_mode:
        cfg = dataclasses.replace(cfg, link_mode=args.link_mode)

    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            html = fh.read()
    else:
        html = sys.stdin.read()

    text = convert(html, cfg)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
