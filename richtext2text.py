"""richtext2text: 富文本标记 (HTML) 转纯文本，仅用 Python 标准库。

转换规则（确定性，同一输入必然得到同一输出）：

- 块级元素（段落/标题/列表/引用/代码块/表格）之间以一个空行分隔。
- 标题：``#`` * level + 空格 + 标题文本（h1~h6）。
- 段落：行内文本，空白字符折叠为单个空格；``<br>`` 产生换行。
- 无序列表：每行 ``<缩进>- 内容``；有序列表：``<缩进>N. 内容``，序号从
  ``start`` 属性（缺省 1）开始递增。每层嵌套增加 ``indent`` 个空格缩进，
  列表项的首行缩进被标记（marker）替换，后续行保持该层缩进。
- 引用块：每一非空行前加 ``"> "``，可嵌套。
- 代码块 (``<pre>``)：内容逐字保留（不改写空白、不换行、不折叠），
  仅整体加上当前层缩进；行内 ``<code>`` 按普通文本处理。
- 表格：每行一个表格行，单元格之间用 ``" | "`` 分隔；若首行含 ``<th>``，
  在其后插入一行 ``--- | --- | ...`` 分隔行。不做宽度截断或对齐填充。
- 链接：``link_mode="text"`` 只保留链接文本；``link_mode="url"`` 输出
  ``文本 (URL)``（无文本时输出 URL 本身；文本与 URL 相同则只输出一次）。
- ``<script>``/``<style>``/``<head>`` 内容视为不可见，丢弃。
- ``<hr>`` 输出一行 ``---``。
- 未知/行内标签（b、i、em、strong、span、code 等）直接取其文本。
- 输出以一个换行符结尾；不含有多于一个的连续空行。

不变量：源文档中所有可见文本（script/style/head 之外的文本节点，
经空白折叠后）必须按原顺序出现在输出中。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser

__all__ = ["Config", "convert", "visible_text", "BLOCK_TAGS"]

_WS_RE = re.compile(r"\s+")
_BLANK_RE = re.compile(r"\n{3,}")

DROP_TAGS = frozenset({"script", "style", "head"})

# 开始标签出现时自动闭合的当前开放标签（模拟 HTML 隐含结束标签）
_IMPLIED_CLOSE = {
    "li": {"li"},
    "p": {"p"},
    "dt": {"dt", "dd"},
    "dd": {"dt", "dd"},
    "td": {"td", "th"},
    "th": {"td", "th"},
    "tr": {"tr", "td", "th"},
    "option": {"option"},
}
INLINE_TAGS = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "cite", "code", "data", "dfn", "em",
    "i", "kbd", "mark", "q", "s", "samp", "small", "span", "strong",
    "sub", "sup", "time", "u", "var", "wbr", "font", "big", "tt", "ins",
    "del", "acronym", "label", "output", "ruby",
})
BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "aside", "header", "footer", "main",
    "nav", "figure", "figcaption", "address", "h1", "h2", "h3", "h4", "h5",
    "h6", "ul", "ol", "li", "dl", "dt", "dd", "blockquote", "pre", "table",
    "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "hr", "form",
    "fieldset", "details", "summary", "html", "body",
})
HEADING_TAGS = {f"h{i}": i for i in range(1, 7)}


@dataclass(frozen=True)
class Config:
    """转换配置。"""

    link_mode: str = "url"  # "url": 文本 (URL)；"text": 只保留文本
    indent: int = 2         # 每层列表/嵌套的缩进空格数
    list_marker: str = "-"  # 无序列表标记

    def __post_init__(self):
        if self.link_mode not in ("url", "text"):
            raise ValueError(f"非法 link_mode: {self.link_mode!r}")
        if self.indent < 0:
            raise ValueError("indent 必须 >= 0")

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        known = {f for f in ("link_mode", "indent", "list_marker")}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"未知配置项: {sorted(unknown)}")
        return cls(**data)

    @classmethod
    def from_json_file(cls, path: str) -> "Config":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


# ---------------------------------------------------------------- 解析

@dataclass
class _Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)  # str | _Element


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Element("#root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        closable = _IMPLIED_CLOSE.get(tag)
        if closable:
            while len(self.stack) > 1 and self.stack[-1].tag in closable:
                self.stack.pop()
        el = _Element(tag, dict(attrs))
        self.stack[-1].children.append(el)
        if tag not in ("br", "hr", "img", "input", "meta", "link", "wbr"):
            self.stack.append(el)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Element(tag, dict(attrs)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _parse(html: str) -> _Element:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def _iter_visible_text(node, out):
    """收集可见文本节点（跳过 script/style/head）。"""
    if isinstance(node, str):
        out.append(node)
        return
    if node.tag in DROP_TAGS:
        return
    for child in node.children:
        _iter_visible_text(child, out)


def visible_text(html: str) -> str:
    """提取文档中全部可见文本（空白折叠后），用于守恒断言。"""
    parts: list = []
    _iter_visible_text(_parse(html), parts)
    return _WS_RE.sub(" ", "".join(parts)).strip()


# ---------------------------------------------------------------- 渲染

def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _inline_text(node, cfg: Config) -> str:
    """把节点子树渲染为行内文本（<br> 转为 \n，其余空白折叠）。"""
    out: list = []

    def walk(n):
        if isinstance(n, str):
            out.append(_WS_RE.sub(" ", n))
            return
        tag = n.tag
        if tag in DROP_TAGS:
            return
        if tag == "br":
            out.append("\n")
            return
        if tag == "img":
            alt = n.attrs.get("alt", "")
            if alt:
                out.append(" " + alt + " ")
            return
        if tag == "a":
            inner = _norm("".join(_collect(n)))
            href = n.attrs.get("href", "").strip()
            if cfg.link_mode == "url" and href:
                if not inner:
                    out.append(" " + href + " ")
                elif inner == href:
                    out.append(" " + inner + " ")
                else:
                    out.append(f" {inner} ({href}) ")
            else:
                out.append(" " + inner + " " if inner else "")
            return
        for child in n.children:
            walk(child)

    def _collect(n):
        # 供链接内部使用：返回文本片段迭代
        if isinstance(n, str):
            yield _WS_RE.sub(" ", n)
            return
        if n.tag in DROP_TAGS:
            return
        if n.tag == "br":
            yield " "
            return
        for c in n.children:
            yield from _collect(c)

    walk(node)
    lines = [_WS_RE.sub(" ", ln).strip() for ln in "".join(out).split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _pad(depth: int, cfg: Config) -> str:
    return " " * (cfg.indent * depth)


def _render_blocks(children, cfg: Config, depth: int) -> list:
    """渲染子节点序列为块列表，每块是 (kind, 行列表)。"""
    blocks: list = []
    inline_buf: list = []

    def flush():
        if not inline_buf:
            return
        holder = _Element("#frag", children=list(inline_buf))
        text = _inline_text(holder, cfg)
        inline_buf.clear()
        if text:
            pad = _pad(depth, cfg)
            blocks.append(("text", [pad + ln for ln in text.split("\n")]))

    for node in children:
        if isinstance(node, str) or node.tag in INLINE_TAGS or node.tag == "br":
            inline_buf.append(node)
            continue
        # 其余标签（含未知标签）一律按块级容器处理，保证结构不丢失
        flush()
        blocks.extend(_render_block_element(node, cfg, depth))
    flush()
    return blocks


def _render_block_element(el: _Element, cfg: Config, depth: int) -> list:
    tag = el.tag
    pad = _pad(depth, cfg)

    if tag in DROP_TAGS:
        return []

    if tag in HEADING_TAGS:
        text = _inline_text(el, cfg)
        if not text:
            return []
        level = HEADING_TAGS[tag]
        return [("heading",
                 [pad + "#" * level + " " + ln for ln in text.split("\n")])]

    if tag == "hr":
        return [("hr", [pad + "---"])]

    if tag == "pre":
        raw: list = []
        _iter_visible_text(el, raw)
        lines = "".join(raw).split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return []
        return [("pre", [pad + ln for ln in lines])]

    if tag == "blockquote":
        inner = _render_blocks(el.children, cfg, depth)
        lines = []
        for i, (_kind, block) in enumerate(inner):
            if i:
                lines.append("")
            lines.extend("> " + ln if ln else "" for ln in block)
        return [("quote", lines)] if lines else []

    if tag in ("ul", "ol"):
        return [("list", _render_list(el, cfg, depth))]

    if tag == "table":
        table_lines = _render_table(el, cfg, depth)
        return [("table", table_lines)] if table_lines else []

    if tag in ("thead", "tbody", "tfoot", "tr", "td", "th", "caption"):
        # 表格部件出现在表格外：按普通容器处理
        return _render_blocks(el.children, cfg, depth)

    if tag == "li":
        return _render_blocks(el.children, cfg, depth)

    # p / div / section / ... 通用容器
    return _render_blocks(el.children, cfg, depth)


def _render_list(el: _Element, cfg: Config, depth: int) -> list:
    ordered = el.tag == "ol"
    pad = _pad(depth, cfg)
    try:
        index = int(el.attrs.get("start", "1"))
    except ValueError:
        index = 1
    lines: list = []
    for child in el.children:
        if isinstance(child, str) or child.tag != "li":
            continue
        marker = f"{index}." if ordered else cfg.list_marker
        index += 1
        item_blocks = _render_blocks(child.children, cfg, depth + 1)
        item_lines: list = []
        for i, (kind, block) in enumerate(item_blocks):
            if i and kind != "list":
                item_lines.append("")
            item_lines.extend(block)
        if not item_lines or (item_blocks and item_blocks[0][0] == "list"):
            # 列表项直接以子列表开头：标记独占一行，子列表整体缩进
            lines.append(pad + marker)
            lines.extend(item_lines)
            continue
        child_pad = _pad(depth + 1, cfg)
        first = item_lines[0]
        if first.startswith(child_pad):
            first = first[len(child_pad):]
        lines.append(pad + marker + " " + first)
        lines.extend(item_lines[1:])
    return lines


def _render_table(el: _Element, cfg: Config, depth: int) -> list:
    pad = _pad(depth, cfg)
    rows: list = []          # [(cells, is_header)]
    caption: list = []

    def walk(n):
        if isinstance(n, str):
            return
        if n.tag in DROP_TAGS:
            return
        if n.tag == "caption":
            text = _inline_text(n, cfg)
            if text:
                caption.extend(text.split("\n"))
            return
        if n.tag == "tr":
            cells = []
            is_header = False
            for c in n.children:
                if isinstance(c, str) or c.tag not in ("td", "th"):
                    continue
                if c.tag == "th":
                    is_header = True
                cells.append(_norm(_inline_text(c, cfg).replace("\n", " ")))
            if cells:
                rows.append((cells, is_header))
            return
        for c in n.children:
            walk(c)

    walk(el)
    if not rows and not caption:
        return []

    width = max((len(r[0]) for r in rows), default=0)
    lines = [pad + cap for cap in caption]
    for cells, is_header in rows:
        cells = cells + [""] * (width - len(cells))
        lines.append(pad + " | ".join(cells))
        if is_header:
            lines.append(pad + " | ".join("---" for _ in cells))
    return lines


def convert(html: str, config: Config | None = None) -> str:
    """把 HTML 富文本转换为纯文本。确定性：同输入同输出。"""
    cfg = config or Config()
    root = _parse(html)
    blocks = _render_blocks(root.children, cfg, 0)
    out_lines: list = []
    for i, (_kind, block) in enumerate(blocks):
        if i:
            out_lines.append("")
        out_lines.extend(block)
    text = "\n".join(out_lines)
    text = _BLANK_RE.sub("\n\n", text).strip("\n")
    return text + "\n" if text else ""


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="richtext2text", description="富文本 (HTML) 转纯文本")
    parser.add_argument("input", nargs="?", help="输入 HTML 文件（缺省读 stdin）")
    parser.add_argument("-c", "--config", help="JSON 配置文件")
    parser.add_argument("-o", "--output", help="输出文件（缺省写 stdout）")
    args = parser.parse_args(argv)

    cfg = Config.from_json_file(args.config) if args.config else Config()
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
    raise SystemExit(main())
