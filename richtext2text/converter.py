"""Convert HTML rich-text markup to readable plain text.

Design goals:
- Preserve block structure (paragraphs, headings, lists, quotes, code
  blocks, tables) instead of flattening everything into one blob.
- Keep list nesting/numbering, table row/column layout and verbatim
  code-block content.
- Deterministic: the same input string always yields the same output.
- Conservative: every piece of visible text in the source appears in
  the output (script/style/head content is not visible text).

Only the Python standard library is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterator, List, Tuple, Union

__all__ = ["Config", "DEFAULT_CONFIG", "convert", "convert_file"]

# Tags that never have a closing tag.
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

# Content of these tags is not visible text and is dropped entirely.
SKIP_TAGS = frozenset({
    "script", "style", "head", "title", "noscript", "template",
})

# Tags rendered inline (no block boundaries introduced).
INLINE_TAGS = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "big", "cite", "code", "data",
    "del", "dfn", "em", "font", "i", "ins", "kbd", "mark", "q", "rp",
    "rt", "ruby", "s", "samp", "small", "span", "strike", "strong",
    "sub", "sup", "time", "tt", "u", "var",
})

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_WS_RE = re.compile(r"\s+")

# Sentinel emitted for <br>; NUL never appears in normal HTML text,
# so hard line breaks survive whitespace collapsing.
_BR = "\x00"


@dataclass(frozen=True)
class Config:
    """Rendering configuration. All rules are fixed per instance, so a
    given (input, config) pair always produces identical output."""

    # "url": render links as ``text <url>``; "text": keep only the text.
    link_mode: str = "url"
    # Marker used for unordered list items.
    unordered_marker: str = "-"
    # "atx": prefix headings with '#' * level; "plain": bare text.
    heading_style: str = "atx"
    # Prefix added to every line inside a blockquote.
    quote_prefix: str = "> "
    # Separator placed between table columns.
    table_column_separator: str = " | "
    # Whether to emit a dashed divider row below header (th) rows.
    table_header_divider: bool = True
    # Text emitted for <hr>.
    hr_text: str = "---"

    def __post_init__(self) -> None:
        if self.link_mode not in ("url", "text"):
            raise ValueError(
                f"link_mode must be 'url' or 'text', got {self.link_mode!r}")
        if self.heading_style not in ("atx", "plain"):
            raise ValueError(
                f"heading_style must be 'atx' or 'plain', "
                f"got {self.heading_style!r}")
        if not self.table_column_separator:
            raise ValueError("table_column_separator must not be empty")

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Build a Config from a plain dict (e.g. parsed JSON)."""
        if not isinstance(data, dict):
            raise TypeError("config data must be a dict")
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(
                f"unknown config keys: {sorted(unknown)}")
        return cls(**data)


DEFAULT_CONFIG = Config()


class _Element:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: list) -> None:
        self.tag = tag
        self.attrs = dict(attrs)
        self.children: List[Union[str, "_Element"]] = []


class _TreeBuilder(HTMLParser):
    """Parse HTML into a light element tree. Iterative stack, so deep
    nesting does not hit the recursion limit during parsing."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("#root", [])
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list) -> None:
        el = _Element(tag, attrs)
        self._stack[-1].children.append(el)
        if tag not in VOID_TAGS:
            self._stack.append(el)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self._stack[-1].children.append(_Element(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        # Pop up to and including the nearest matching open element.
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def _collapse(text: str) -> str:
    return _WS_RE.sub(" ", text)


class _Renderer:
    def __init__(self, config: Config) -> None:
        self.cfg = config

    # -- entry point ----------------------------------------------------

    def render(self, root: _Element) -> str:
        lines = self._render_blocks(root.children)
        while lines and lines[-1] == "":
            lines.pop()
        if not lines:
            return ""
        return "\n".join(lines) + "\n"

    # -- block level ----------------------------------------------------

    def _render_blocks(self, children: list) -> List[str]:
        """Render a list of sibling nodes to lines. Blocks are separated
        by exactly one blank line; stray inline content is grouped into
        an implicit paragraph."""
        blocks: List[List[str]] = []
        inline_buf: List[str] = []

        def flush_inline() -> None:
            raw = "".join(inline_buf)
            inline_buf.clear()
            # <br> becomes a hard line break inside the paragraph.
            segs = [s for s in (_collapse(x).strip()
                                for x in raw.split(_BR)) if s]
            if segs:
                blocks.append(segs)

        for child in children:
            if isinstance(child, str):
                inline_buf.append(child)
                continue
            tag = child.tag
            if tag in SKIP_TAGS:
                continue
            if tag == "hr":
                flush_inline()
                blocks.append([self.cfg.hr_text])
                continue
            if tag in VOID_TAGS or tag in INLINE_TAGS:
                inline_buf.append(self._render_inline(child))
                continue
            flush_inline()
            if tag in _HEADING_TAGS:
                blocks.append(self._block_heading(child, int(tag[1])))
            elif tag in ("ul", "ol"):
                blocks.append(self._block_list(child, tag == "ol", 0))
            elif tag == "blockquote":
                blocks.append(self._block_quote(child))
            elif tag == "pre":
                blocks.append(self._block_pre(child))
            elif tag == "table":
                blocks.append(self._block_table(child))
            else:
                # p, div, section, article, li, unknown containers...
                blocks.append(self._render_blocks(child.children))
        flush_inline()

        lines: List[str] = []
        for block in blocks:
            if not block:
                continue
            if lines:
                lines.append("")
            lines.extend(block)
        return lines

    def _block_heading(self, node: _Element, level: int) -> List[str]:
        text = _collapse("".join(
            self._render_inline(c) for c in node.children)).strip()
        if not text:
            return []
        if self.cfg.heading_style == "atx":
            return ["#" * level + " " + text]
        return [text]

    def _block_list(self, node: _Element, ordered: bool,
                    indent: int) -> List[str]:
        lines: List[str] = []
        start = 1
        if ordered:
            try:
                start = int(node.attrs.get("start", "1"))
            except (TypeError, ValueError):
                start = 1
        index = start - 1
        for child in node.children:
            if not (isinstance(child, _Element) and child.tag == "li"):
                continue
            index += 1
            marker = f"{index}." if ordered else self.cfg.unordered_marker
            prefix = " " * indent + marker + " "
            continuation = " " * len(prefix)
            item_lines = self._render_li(child)
            for i, line in enumerate(item_lines):
                if not line:
                    lines.append("")
                elif i == 0:
                    lines.append(prefix + line)
                else:
                    lines.append(continuation + line)
        return lines

    def _render_li(self, li: _Element) -> List[str]:
        """Render one <li>: inline runs plus nested blocks, all at
        relative indent 0. The enclosing list adds its continuation pad
        to every line after the first, which is what builds up the
        visible hierarchy indentation."""
        lines: List[str] = []
        inline_buf: List[str] = []

        def flush() -> None:
            raw = "".join(inline_buf)
            inline_buf.clear()
            segs = [s for s in (_collapse(x).strip()
                                for x in raw.split(_BR)) if s]
            lines.extend(segs)

        for child in li.children:
            if isinstance(child, str):
                inline_buf.append(child)
                continue
            tag = child.tag
            if tag in SKIP_TAGS:
                continue
            if tag in ("ul", "ol"):
                flush()
                lines.extend(self._block_list(child, tag == "ol", 0))
            elif tag in VOID_TAGS or tag in INLINE_TAGS:
                inline_buf.append(self._render_inline(child))
            else:
                flush()
                lines.extend(self._render_blocks([child]))
        flush()
        return lines or [""]

    def _block_quote(self, node: _Element) -> List[str]:
        inner = self._render_blocks(node.children)
        prefix = self.cfg.quote_prefix
        return [prefix + line if line else prefix.rstrip()
                for line in inner]

    def _block_pre(self, node: _Element) -> List[str]:
        # Code blocks: text is kept verbatim (no whitespace collapsing,
        # no re-wrapping, no merging into paragraphs).
        raw = self._raw_text(node).strip("\n")
        if not raw:
            return []
        return raw.split("\n")

    def _raw_text(self, node: _Element) -> str:
        parts: List[str] = []

        def walk(el: _Element) -> None:
            for c in el.children:
                if isinstance(c, str):
                    parts.append(c)
                elif c.tag not in SKIP_TAGS:
                    walk(c)

        walk(node)
        return "".join(parts)

    def _iter_rows(self, node: _Element) -> Iterator[_Element]:
        for child in node.children:
            if not isinstance(child, _Element):
                continue
            if child.tag == "tr":
                yield child
            elif child.tag in ("thead", "tbody", "tfoot"):
                yield from self._iter_rows(child)

    def _block_table(self, node: _Element) -> List[str]:
        sep = self.cfg.table_column_separator
        rows: List[Tuple[List[str], bool]] = []
        for tr in self._iter_rows(node):
            cells: List[str] = []
            is_header = False
            for cell in tr.children:
                if not (isinstance(cell, _Element)
                        and cell.tag in ("td", "th")):
                    continue
                if cell.tag == "th":
                    is_header = True
                text = _collapse("".join(
                    self._render_inline(c) for c in cell.children)).strip()
                cells.append(text)
            if cells:
                rows.append((cells, is_header))

        lines: List[str] = []
        for cells, is_header in rows:
            lines.append(sep.join(cells))
            if is_header and self.cfg.table_header_divider:
                lines.append(sep.join("-" * max(3, len(c)) for c in cells))
        return lines

    # -- inline level ---------------------------------------------------

    def _render_inline(self, node) -> str:
        if isinstance(node, str):
            return node
        tag = node.tag
        if tag in SKIP_TAGS:
            return ""
        if tag == "br":
            return _BR
        if tag == "img":
            alt = node.attrs.get("alt", "").strip()
            return f"[{alt}]" if alt else ""
        inner = "".join(self._render_inline(c) for c in node.children)
        if tag == "a":
            text = _collapse(inner).strip()
            href = node.attrs.get("href", "").strip()
            if self.cfg.link_mode == "text" or not href:
                return text or href
            return f"{text} <{href}>" if text else f"<{href}>"
        return inner


def convert(html: str, config: Config = DEFAULT_CONFIG) -> str:
    """Convert an HTML string to plain text.

    Deterministic: identical (html, config) always yields identical
    output. Empty or whitespace-only input returns an empty string.
    """
    if not isinstance(html, str):
        raise TypeError("html must be a str")
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return _Renderer(config).render(builder.root)


def convert_file(path: str, config: Config = DEFAULT_CONFIG,
                 encoding: str = "utf-8") -> str:
    """Read an HTML file and convert it to plain text."""
    with open(path, "r", encoding=encoding) as fh:
        return convert(fh.read(), config)
