"""Legacy fixed-split mode: the original behaviour, kept for compatibility.

Legacy mode splits every line on a fixed delimiter into positional string
columns ``col1..colN`` and always emits JSON Lines. No config is needed.
"""

import json


def legacy_parse(text, delimiter):
    parts = [part.strip() for part in text.split(delimiter)]
    return {f"col{i}": part for i, part in enumerate(parts, start=1)}


def run_legacy(lines, delimiter, out_stream):
    total = 0
    for raw_line in lines:
        text = raw_line.rstrip("\n").rstrip("\r")
        if text == "":
            continue
        total += 1
        out_stream.write(
            json.dumps(legacy_parse(text, delimiter), ensure_ascii=False) + "\n"
        )
    return total


def legacy_config(field_count, delimiter="|"):
    """Config equivalent to legacy mode: the migration starting point."""
    return {
        "version": 1,
        "parser": {
            "delimiter": delimiter,
            "trim": True,
            "fields": [
                {"name": f"col{i}", "type": "string", "required": True}
                for i in range(1, field_count + 1)
            ],
        },
        "output": {"format": "jsonl"},
    }
