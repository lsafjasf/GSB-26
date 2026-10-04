"""表与分片：类型化 CSV 分片文件的读写。

表 = 一组有序分片；分片 = 一个 CSV 文件（首行表头）。
Schema 为 (列名, 类型) 列表，类型支持 int / float / str / bool。
空字符串解析为 None（NULL）。
"""

from __future__ import annotations

import csv
import os

TYPES = ("int", "float", "str", "bool")


def _parse(tp, s):
    if s == "":
        return None
    if tp == "int":
        return int(s)
    if tp == "float":
        return float(s)
    if tp == "bool":
        return s == "true"
    return s


def _format(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


class Schema:
    def __init__(self, columns):
        self.columns = list(columns)
        for _, tp in self.columns:
            if tp not in TYPES:
                raise ValueError(f"unknown column type: {tp!r}")
        self.names = [n for n, _ in self.columns]

    def parse_row(self, fields):
        return {name: _parse(tp, s) for (name, tp), s in zip(self.columns, fields)}

    def format_row(self, row):
        return [_format(row[name]) for name in self.names]


def read_csv_shard(path, schema):
    """读取一个分片文件，返回行 dict 列表（保持文件内顺序）。"""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return rows
        if list(header) != schema.names:
            raise ValueError(f"{path}: header {header} != schema {schema.names}")
        for fields in reader:
            rows.append(schema.parse_row(fields))
    return rows


def write_csv_shard(path, schema, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(schema.names)
        for row in rows:
            writer.writerow(schema.format_row(row))


def shard_len(source):
    """分片行数（用于计算全局行号偏移）。source 为路径或行列表。"""
    if isinstance(source, (list, tuple)):
        return len(source)
    n = 0
    with open(source, "rb") as f:
        for _ in f:
            n += 1
    return max(0, n - 1)  # 减去表头


def split_evenly(items, n_parts):
    """把序列切成 n_parts 个连续分片（内存表用），保持原顺序。"""
    if n_parts <= 0:
        raise ValueError("n_parts must be >= 1")
    n = len(items)
    q, r = divmod(n, n_parts)
    out, pos = [], 0
    for i in range(n_parts):
        size = q + (1 if i < r else 0)
        out.append(items[pos:pos + size])
        pos += size
    return out


def list_shards(directory, prefix="shard-", suffix=".csv"):
    """列出目录下有序分片文件（按文件名排序即分片顺序）。"""
    names = sorted(n for n in os.listdir(directory)
                   if n.startswith(prefix) and n.endswith(suffix))
    return [os.path.join(directory, n) for n in names]
