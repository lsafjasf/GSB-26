"""从 Python 源码提取函数/类定义及其文档注释。

注释归属规则（核心，决定不把上一个函数的尾部注释挂到下一个函数上）：

1. 行尾注释（代码行末尾的 ``# ...``）归属于该代码行所在的定义；
   位于 def/class 所在行的行尾注释记为该定义的 trailing 注释；
   位于装饰器行的行尾注释记入该定义的 leading。
2. 定义前注释（leading）：紧贴 def/class（或其装饰器）上方、中间无空行、
   且缩进与该定义一致的独占注释行块。
3. 定义内部注释（inner）：位于定义行区间之内、且未归属于嵌套定义的注释。
4. 两个兄弟定义之间的其余注释块，归属于“最内层的前置定义”的
   trailing_block（即缩进小于该注释、且在其之前结束的最近定义），
   绝不挂到后一个定义上。
5. 无法归属到任何定义的注释归入 module 条目的 unattached。
"""

import ast
import io
import tokenize

from .docparse import parse_docstring

__all__ = ["extract_source", "extract_file"]

_DEF_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class _DefInfo:
    __slots__ = ("node", "parent", "def_start", "leading", "trailing",
                 "trailing_block", "inner")

    def __init__(self, node, parent):
        self.node = node
        self.parent = parent
        decos = [d.lineno for d in getattr(node, "decorator_list", [])]
        self.def_start = min(decos + [node.lineno])
        self.leading = []
        self.trailing = None
        self.trailing_block = []
        self.inner = []


def _collect_comments(source):
    """返回 (comment_only, eol)：行号 -> (列, 文本) / 行号 -> 文本。"""
    comment_only = {}
    eol = {}
    lines = source.splitlines()
    reader = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(reader):
        if tok.type != tokenize.COMMENT:
            continue
        lineno, col = tok.start
        if lines[lineno - 1][:col].strip():
            eol[lineno] = tok.string
        else:
            comment_only[lineno] = (col, tok.string)
    return comment_only, eol


def _signature(node):
    if isinstance(node, ast.ClassDef):
        parts = [ast.unparse(b) for b in node.bases]
        parts += [f"{k.arg}={ast.unparse(k.value)}" for k in node.keywords]
        return (f"class {node.name}({', '.join(parts)})" if parts
                else f"class {node.name}")
    sig = f"{node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        sig += f" -> {ast.unparse(node.returns)}"
    if isinstance(node, ast.AsyncFunctionDef):
        sig = "async " + sig
    return sig


def _kind(node, parent):
    if isinstance(node, ast.ClassDef):
        return "class"
    if parent is not None and isinstance(parent.node, ast.ClassDef):
        return "method"
    if isinstance(node, ast.AsyncFunctionDef):
        return "async_function"
    return "function"


def extract_source(source, filename="<unknown>"):
    """提取源码中的定义条目，返回可 JSON 序列化的字典。"""
    tree = ast.parse(source)
    lines = source.splitlines()
    comment_only, eol = _collect_comments(source)

    infos = {}

    def visit(node, parent_info):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEF_NODES):
                info = _DefInfo(child, parent_info)
                infos[child] = info
                visit(child, info)
            else:
                visit(child, parent_info)

    visit(tree, None)

    def children_of(parent_info):
        out = [i for i in infos.values() if i.parent is parent_info]
        out.sort(key=lambda i: i.def_start)
        return out

    attributed = set()  # 已归属的注释行号（comment_only 与 eol 共用）

    def in_scope(info, ancestor):
        """info 是否为 ancestor 自身或其后代（ancestor 为 None 时恒真）。"""
        cur = info
        while cur is not None:
            if cur is ancestor:
                return True
            cur = cur.parent
        return ancestor is None

    def innermost_preceding(lineno, col, scope):
        """最内层的前置定义：缩进小于 col、在 lineno 之前结束、
        且位于 scope 子树内的最近定义。"""
        best = None
        best_key = None
        for info in infos.values():
            node = info.node
            if (node.end_lineno < lineno and node.col_offset < col
                    and in_scope(info, scope)):
                key = (node.col_offset, node.end_lineno)
                if best_key is None or key > best_key:
                    best, best_key = info, key
        return best

    def attribute_segment(seg_start, seg_end, prev_info, next_info, scope):
        """处理两个定义之间（或边界之外）的注释行段。"""
        if next_info is not None:
            block = []
            lineno = next_info.def_start - 1
            while lineno >= seg_start and lineno in comment_only \
                    and lineno not in attributed:
                col, text = comment_only[lineno]
                if col != next_info.node.col_offset:
                    break
                block.append((lineno, text))
                lineno -= 1
            for ln, text in reversed(block):
                next_info.leading.append(text)
                attributed.add(ln)
        for ln in range(seg_start, seg_end + 1):
            if ln in comment_only and ln not in attributed:
                owner = innermost_preceding(ln, comment_only[ln][0], scope)
                if owner is None:
                    owner = prev_info
                if owner is not None:
                    owner.trailing_block.append(comment_only[ln][1])
                    attributed.add(ln)

    def attribute_own_span(info):
        """处理定义自身区间内、不属于任何子定义的注释。"""
        node = info.node
        child_lines = set()
        for child in children_of(info):
            for ln in range(child.def_start, child.node.end_lineno + 1):
                child_lines.add(ln)
        for ln in range(info.def_start, node.end_lineno + 1):
            if ln in child_lines:
                continue
            if ln in comment_only and ln not in attributed:
                info.inner.append(comment_only[ln][1])
                attributed.add(ln)
            if ln in eol and ln not in attributed:
                if ln == node.lineno:
                    info.trailing = eol[ln]
                elif ln < node.lineno:
                    info.leading.append(eol[ln])  # 装饰器行尾注释
                else:
                    info.inner.append(eol[ln])
                attributed.add(ln)

    def process(parent_info):
        siblings = children_of(parent_info)
        if parent_info is None:
            region_start, region_end = 1, len(lines)
        else:
            region_start = parent_info.def_start
            region_end = parent_info.node.end_lineno
        prev = None
        seg_start = region_start
        for s in siblings:
            attribute_segment(seg_start, s.def_start - 1, prev, s,
                              parent_info)
            prev = s
            seg_start = s.node.end_lineno + 1
        attribute_segment(seg_start, region_end, prev, None, parent_info)
        if parent_info is not None:
            attribute_own_span(parent_info)
        for s in siblings:
            process(s)

    process(None)

    entries = []

    def build(info):
        node = info.node
        doc_raw = ast.get_docstring(node)
        entry = {
            "name": node.name,
            "kind": _kind(node, info.parent),
            "signature": _signature(node),
            "line": node.lineno,
            "decorators": [ast.unparse(d)
                           for d in getattr(node, "decorator_list", [])],
            "doc": parse_docstring(doc_raw) if doc_raw else None,
            "comments": {
                "leading": info.leading,
                "trailing": info.trailing,
                "trailing_block": info.trailing_block,
                "inner": info.inner,
            },
        }
        entries.append((info, entry))
        for child in children_of(info):
            build(child)

    for top in children_of(None):
        build(top)

    def qualname(info):
        parts = []
        cur = info
        while cur is not None:
            parts.append(cur.node.name)
            cur = cur.parent
        return ".".join(reversed(parts))

    out_entries = []
    for info, entry in entries:
        entry["qualified_name"] = qualname(info)
        out_entries.append(entry)
    out_entries.sort(key=lambda e: e["line"])

    leftover = [(ln, comment_only[ln][1]) for ln in comment_only
                if ln not in attributed]
    leftover += [(ln, eol[ln]) for ln in eol if ln not in attributed]
    leftover.sort()
    module_doc = ast.get_docstring(tree)
    module = {
        "name": filename,
        "kind": "module",
        "doc": parse_docstring(module_doc) if module_doc else None,
        "comments": {"unattached": [text for _, text in leftover]},
    }
    return {"file": filename, "module": module, "entries": out_entries}


def extract_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return extract_source(fh.read(), filename=str(path))
