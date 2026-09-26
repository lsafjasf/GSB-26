"""未覆盖字符报告：扫描语料，列出规则表未覆盖的字符及出现次数。"""

import json
import sys

from collator import Collator


def main(paths, out_path="uncovered_report.txt"):
    with open("rules.json", encoding="utf-8") as f:
        collator = Collator(json.load(f))

    texts = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            texts.append(f.read())

    counts = {ch: n for ch, n in collator.uncovered_chars(texts).items()
              if ch.strip() != ''}  # 忽略空白字符
    lines = ["未覆盖字符报告", "规则表: rules.json", "语料: %s" % ", ".join(paths), ""]
    if not counts:
        lines.append("（全部字符均被规则表覆盖）")
    else:
        lines.append("共 %d 个未覆盖字符，按码位排序：" % len(counts))
        lines.append("")
        lines.append("字符  码位       出现次数")
        for ch, n in counts.items():
            name = ch if ch.isprintable() else repr(ch)
            lines.append("%-4s U+%04X    %d" % (name, ord(ch), n))
        lines.append("")
        lines.append("回退策略: %s（未覆盖字符按码位排在%s，顺序确定）"
                     % (collator.fallback,
                        "末尾" if collator.fallback == "end" else "开头"))
    report = "\n".join(lines) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(report, end="")


if __name__ == "__main__":
    main(sys.argv[1:] or ["sample_corpus.txt"])
