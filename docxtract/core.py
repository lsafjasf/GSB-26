"""源码注释文档提取核心。

注释归属模型（每条注释有且仅有一个归属）：
  - leading:  紧贴定义头部（含装饰器）上方的连续独立注释块 -> 该定义的文档
  - header:   定义行 / 装饰器行上的行尾注释               -> 该定义的头部注释
  - inner:    定义体内部的注释                           -> 该定义的内部注释
  - unattached: 其余（与下一个定义之间有空行隔开的尾部注释等）-> 模块级游离注释

关键不变式：上一个定义之后的注释，只有与下一个定义头部**连续无空行**时才归
属于下一个定义，否则一律记为游离注释，绝不挂到下一个定义的文档上。
"""

from __future__ import annotations

import ast
import io
import tokenize

from .docparse import parse_doc

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


# ------------------------------------------------------------- 注释收集 --

def _collect_comments(source):
    lines = source.splitlines()
    comments = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type != tokenize.COMMENT:
            continue
        row, col = tok.start
        prefix = lines[row - 1][:col] if row - 1 <= len(lines) else ""
        comments.append({
            "line": row,
            "col": col,
            "text": tok.string[1:].strip(),
            "standalone": not prefix.strip(),
        })
    return comments


# ------------------------------------------------------------- 定义收集 --

def _collect_defs(tree):
    """按源码顺序收集全部 def/class 节点及其父链 [(name, is_class), ...]。"""
    defs = []

    def visit(node, parents):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEF_TYPES):
                defs.append((child, tuple(parents)))
                visit(child, parents + ((child.name, isinstance(child, ast.ClassDef)),))
            else:
                visit(child, parents)

    visit(tree, ())
    return defs


def _header_start(node):
    if node.decorator_list:
        return min(d.lineno for d in node.decorator_list)
    return node.lineno


# ------------------------------------------------------------- 签名构建 --

def _signature(node):
    if isinstance(node, ast.ClassDef):
        parts = [ast.unparse(b) for b in node.bases]
        parts += [ast.unparse(k) for k in node.keywords]
        return "class {}({})".format(node.name, ", ".join(parts))
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    sig = "{} {}({})".format(prefix, node.name, ast.unparse(node.args))
    if node.returns is not None:
        sig += " -> " + ast.unparse(node.returns)
    return sig


def _signature_params(args):
    params = []

    def entry(arg, kind, default):
        return {
            "name": arg.arg,
            "kind": kind,
            "annotation": ast.unparse(arg.annotation) if arg.annotation else None,
            "default": ast.unparse(default) if default is not None else None,
        }

    pos = list(args.posonlyargs) + list(args.args)
    kinds = (["positional_only"] * len(args.posonlyargs)
             + ["positional_or_keyword"] * len(args.args))
    defaults = [None] * (len(pos) - len(args.defaults)) + list(args.defaults)
    for arg, kind, default in zip(pos, kinds, defaults):
        params.append(entry(arg, kind, default))
    if args.vararg:
        params.append(entry(args.vararg, "var_positional", None))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        params.append(entry(arg, "keyword_only", default))
    if args.kwarg:
        params.append(entry(args.kwarg, "var_keyword", None))
    return params


# ------------------------------------------------------------- 归属判定 --

def _attribute(comments, defs):
    """为每条注释计算归属。

    返回 (leading, header, inner, unattached)：
      leading:    id(node) -> [comment, ...]   定义前注释块
      header:     id(node) -> [comment, ...]   定义/装饰器行行尾注释
      inner:      id(node) -> [comment, ...]   定义体内部注释
      unattached: [comment, ...]               游离注释
    """
    standalone_lines = {c["line"] for c in comments if c["standalone"]}
    eol_lines = {c["line"] for c in comments if not c["standalone"]}

    leading = {}
    leading_owner = {}   # line -> node id
    header = {}
    header_owner = {}    # line -> node id

    for node, _parents in defs:
        hs = _header_start(node)
        # 头部上方连续的独立注释块（遇到空行/代码行即停止）
        block = []
        ln = hs - 1
        while ln in standalone_lines:
            block.append(ln)
            ln -= 1
        block.reverse()
        # 装饰器与 def 行之间的独立注释也算头部前注释
        region = [l for l in range(hs, node.lineno) if l in standalone_lines]
        lead_lines = block + region
        leading[id(node)] = lead_lines
        for l in lead_lines:
            leading_owner[l] = id(node)
        # 头部区域（装饰器行..def 行）上的行尾注释
        hdr = [l for l in range(hs, node.lineno + 1) if l in eol_lines]
        header[id(node)] = hdr
        for l in hdr:
            header_owner[l] = id(node)

    # 区间树：def 范围严格嵌套，用栈求最内层包含定义
    inner = {id(node): [] for node, _ in defs}
    unattached = []
    sorted_defs = sorted(defs, key=lambda dp: dp[0].lineno)
    by_id = {id(node): node for node, _ in defs}
    stack = []
    di = 0
    for c in sorted(comments, key=lambda c: c["line"]):
        line = c["line"]
        if line in leading_owner or line in header_owner:
            continue
        while di < len(sorted_defs) and sorted_defs[di][0].lineno < line:
            stack.append(sorted_defs[di][0])
            di += 1
        while stack and stack[-1].end_lineno < line:
            stack.pop()
        if stack:
            inner[id(stack[-1])].append(c)
        else:
            unattached.append(c)

    comment_by_line = {c["line"]: c for c in comments}
    leading = {nid: [comment_by_line[l] for l in lines] for nid, lines in leading.items()}
    header = {nid: [comment_by_line[l] for l in lines] for nid, lines in header.items()}
    return leading, header, inner, unattached, by_id


# ---------------------------------------------------------------- 主入口 --

def extract(source, filename="<memory>"):
    """从 Python 源码中提取定义与文档注释，返回结构化字典。"""
    tree = ast.parse(source)
    comments = _collect_comments(source)
    defs = _collect_defs(tree)
    leading, header, inner, unattached, _ = _attribute(comments, defs)

    entries = []
    for node, parents in defs:
        qualname = ".".join([p[0] for p in parents] + [node.name])
        parent_qual = ".".join(p[0] for p in parents) or None
        is_class = isinstance(node, ast.ClassDef)
        is_async = isinstance(node, ast.AsyncFunctionDef)
        in_class = bool(parents) and parents[-1][1]
        if is_class:
            kind = "class"
        elif is_async:
            kind = "async_method" if in_class else "async_function"
        else:
            kind = "method" if in_class else "function"

        lead = leading.get(id(node), [])
        lead_text = "\n".join(c["text"] for c in lead) or None
        lead_info = None
        if lead:
            lead_info = {
                "text": lead_text,
                "start_line": lead[0]["line"],
                "end_line": lead[-1]["line"],
            }
        hdr = header.get(id(node), [])
        hdr_info = None
        if hdr:
            hdr_info = {
                "text": " ".join(c["text"] for c in hdr),
                "line": hdr[0]["line"],
            }
        inner_info = [
            {"line": c["line"], "text": c["text"],
             "position": "standalone" if c["standalone"] else "end_of_line"}
            for c in inner.get(id(node), [])
        ]

        docstring = ast.get_docstring(node)
        doc_source = "\n\n".join(t for t in (lead_text, docstring) if t)

        entry = {
            "name": node.name,
            "qualified_name": qualname,
            "kind": kind,
            "parent": parent_qual,
            "lineno": node.lineno,
            "end_lineno": node.end_lineno,
            "signature": _signature(node),
            "decorators": [ast.unparse(d) for d in node.decorator_list],
            "docstring": docstring,
            "leading_comment": lead_info,
            "header_comment": hdr_info,
            "inner_comments": inner_info,
            "doc": parse_doc(doc_source),
        }
        if not is_class:
            entry["params"] = _signature_params(node.args)
        entries.append(entry)

    return {
        "file": filename,
        "module_docstring": ast.get_docstring(tree),
        "entries": entries,
        "unattached_comments": [
            {"line": c["line"], "text": c["text"],
             "position": "standalone" if c["standalone"] else "end_of_line"}
            for c in unattached
        ],
        "stats": {
            "lines": len(source.splitlines()),
            "comments": len(comments),
            "entries": len(entries),
        },
    }


def extract_file(path):
    """读取源码文件（尊重 PEP 263 编码声明）并提取。"""
    with tokenize.open(path) as fh:
        source = fh.read()
    return extract(source, filename=path)
