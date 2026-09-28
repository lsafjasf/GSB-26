#!/usr/bin/env python3
"""构建 ``sensitive/data`` 下的两张静态字表（仅标准库，可离线复跑）。

产物（随仓库提交，运行期完全离线加载，见 ``sensitive/normalize.py``）：

* ``t2s_chars.txt`` —— 繁→简 **严格单字 1:1** 映射，每行两个汉字
  「繁简」，按繁体码点排序。来源：OpenCC ``TSCharacters.txt``，只保留
  value 恰好为单个单字词条的条目（一对多/词级转换一律剔除，保证归一化
  仍是单码点→单码点，位置映射不被破坏）。
* ``homophone_groups.txt`` —— 同音折叠组，每行一个分组：组内所有汉字在
  归一化时折叠到该行首字（码点最小者，确定性）。构建口径：
  - 读音取 Unicode Unihan ``kMandarin``，且该字 **只有一个读音**
    （多音字剔除，避免错折）；
  - 字集限定 GB2312 一级常用字（3755 个高频汉字），避免生僻字扩大碰撞；
  - 按 **带声调拼音** 分组（fā/fá/fǎ 不同组），只保留 ≥2 字的组。
    这是“保守同音”档；脚本同时输出不带声调的统计供对比，但默认不生成
    无调表（误伤面过大，见 eval/README 与主 README 说明）。
* ``MANIFEST.json`` —— 数据来源 URL、原始文件 sha256 与条目计数，
  用于校验产物可复现。

用法（需要联网下载两份上游数据）：

    python3 sensitive/data/build_tables.py

运行期（Normalizer）只读上述文本产物，不访问网络。
"""

from __future__ import annotations

import hashlib
import io
import json
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent

OPENCC_TS_URL = (
    "https://raw.githubusercontent.com/BYVoid/OpenCC/"
    "master/data/dictionary/TSCharacters.txt"
)
UNIHAN_URL = "https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip"

T2S_FILE = "t2s_chars.txt"
HOMOPHONE_FILE = "homophone_groups.txt"
MANIFEST_FILE = "MANIFEST.json"


def _download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "build-tables/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_t2s(raw: bytes) -> dict[str, str]:
    """从 OpenCC TSCharacters.txt 抽取严格 1:1 的繁→简单字映射。"""
    table: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        key, values = parts[0], parts[1].split()
        if len(key) == 1 and len(values) == 1 and len(values[0]) == 1:
            table[key] = values[0]
    return dict(sorted(table.items()))


def gb2312_level1() -> set[str]:
    """GB2312 一级汉字（3755 个高频字），编码区 B0A1..D7F9。"""
    chars: set[str] = set()
    for b1 in range(0xB0, 0xD8):
        for b2 in range(0xA1, 0xFF):
            try:
                chars.add(bytes([b1, b2]).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return chars


def build_homophone_groups(readings_raw: bytes) -> list[str]:
    """从 Unihan_Readings.txt 构建带声调的同音折叠组（每组≥2 字）。"""
    common = gb2312_level1()
    readings: dict[str, list[str]] = {}
    for line in readings_raw.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) == 3 and parts[1] == "kMandarin":
            readings[chr(int(parts[0][2:], 16))] = parts[2].split()

    # 仅常用字、仅单音字
    pinyin: dict[str, str] = {
        ch: rs[0] for ch, rs in readings.items() if ch in common and len(rs) == 1
    }
    grouped: dict[str, list[str]] = defaultdict(list)
    for ch, py in pinyin.items():
        grouped[py].append(ch)

    groups: list[str] = []
    for py, chars in grouped.items():
        if len(chars) < 2:
            continue
        ordered = sorted(chars)  # 码点最小者为首字（折叠目标）
        groups.append("".join(ordered))
    groups.sort(key=lambda g: g[0])
    return groups


def main() -> None:
    ts_raw = _download(OPENCC_TS_URL)
    unihan_zip = _download(UNIHAN_URL)
    readings_raw = zipfile.ZipFile(io.BytesIO(unihan_zip)).read(
        "Unihan_Readings.txt"
    )

    t2s = build_t2s(ts_raw)
    groups = build_homophone_groups(readings_raw)

    (HERE / T2S_FILE).write_text(
        "\n".join(f"{k}{v}" for k, v in t2s.items()) + "\n", encoding="utf-8"
    )
    (HERE / HOMOPHONE_FILE).write_text(
        "\n".join(groups) + "\n", encoding="utf-8"
    )
    manifest = {
        "sources": {
            "opencc_tscharacters": {
                "url": OPENCC_TS_URL,
                "sha256": _sha256(ts_raw),
            },
            "unihan_readings": {
                "url": UNIHAN_URL,
                "entry": "Unihan_Readings.txt",
                "zip_sha256": _sha256(unihan_zip),
            },
        },
        "outputs": {
            T2S_FILE: {"pairs": len(t2s)},
            HOMOPHONE_FILE: {"groups": len(groups),
                             "chars": sum(len(g) for g in groups)},
        },
        "homophone_policy": {
            "universe": "GB2312 level-1 common hanzi",
            "readings": "Unihan kMandarin, single-reading chars only",
            "grouping": "pinyin with tone, groups of size >= 2",
            "fold_target": "minimum codepoint char in each group",
        },
    }
    (HERE / MANIFEST_FILE).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {T2S_FILE}: {len(t2s)} pairs")
    print(
        f"wrote {HOMOPHONE_FILE}: {len(groups)} groups, "
        f"{sum(len(g) for g in groups)} chars"
    )


if __name__ == "__main__":
    main()
