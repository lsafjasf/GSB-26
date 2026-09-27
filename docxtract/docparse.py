"""文档注释正文的结构化解析。

支持三种主流参数说明格式：
  - Google 风格（``Args:`` / ``Returns:`` 段落）
  - NumPy 风格（``Parameters`` + 下划线段落）
  - Sphinx/reST 风格（``:param x:`` / ``:return:`` 字段）

解析不出的行一律保留原文并标记 ``parsed: False``，绝不丢弃。
"""

from __future__ import annotations

import re

_GOOGLE_SECTIONS = {
    "args": "params",
    "arguments": "params",
    "parameters": "params",
    "keyword args": "params",
    "keyword arguments": "params",
    "attributes": "params",
    "params": "params",
    "returns": "returns",
    "return": "returns",
    "yields": "returns",
    "raises": "raises",
    "exceptions": "raises",
}

_NUMPY_SECTIONS = {
    "parameters": "params",
    "other parameters": "params",
    "attributes": "params",
    "returns": "returns",
    "yields": "returns",
    "raises": "raises",
}

# name (type): desc   或   name: desc
_GOOGLE_PARAM_RE = re.compile(
    r"^(?P<name>\*{0,2}[^\W\d][\w.]*)(?:\s*\((?P<type>[^()]*)\))?\s*:\s*(?P<desc>.*)$"
)
# name : type          （NumPy 条目行，描述在后续缩进行）
_NUMPY_PARAM_RE = re.compile(
    r"^(?P<name>\*{0,2}[^\W\d][\w.]*(?:\s*,\s*\*{0,2}[^\W\d][\w.]*)*)\s*:\s*(?P<rest>.*)$"
)

_SPHINX_PARAM = re.compile(r"^:param\s+(?P<name>\*{0,2}\w+)\s*:\s*(?P<desc>.*)$")
_SPHINX_TYPE = re.compile(r"^:type\s+(?P<name>\*{0,2}\w+)\s*:\s*(?P<type>.*)$")
_SPHINX_RETURN = re.compile(r"^:returns?\s*:\s*(?P<desc>.*)$")
_SPHINX_RTYPE = re.compile(r"^:rtype\s*:\s*(?P<type>.*)$")
_SPHINX_RAISES = re.compile(r"^:(?:raises?|except)\s+(?P<name>[\w.]+)\s*:\s*(?P<desc>.*)$")
_SPHINX_DETECT = re.compile(r"^\s*:(param|type|return|returns|rtype|raise|raises|except)\b")
_SPHINX_ANY_FIELD = re.compile(r"^:\w")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _empty_result() -> dict:
    return {"body": "", "params": [], "returns": None, "raises": [], "unparsed": []}


def parse_doc(text):
    """把一段文档注释解析为结构化字典。

    返回 ``{"body", "params", "returns", "raises", "unparsed"}``：
      - body:     去掉参数/返回段落后的正文描述
      - params:   [{"name", "type", "description", "parsed"}]
      - returns:  {"type", "description", "parsed"} 或 None
      - raises:   [{"name", "description", "parsed"}]
      - unparsed: [{"raw", "parsed": False}] 无法匹配格式的原文行
    """
    result = _empty_result()
    if not text or not text.strip():
        return result
    lines = text.expandtabs().splitlines()
    if any(_SPHINX_DETECT.match(l) for l in lines):
        return _parse_sphinx(lines)
    sections = _find_sections(lines)
    if sections:
        return _parse_sections(lines, sections)
    result["body"] = text.strip()
    return result


# ---------------------------------------------------------------- Sphinx --

def _continuation(lines, i, first):
    """收集字段首行之后的缩进续行，返回 (文本, 下一行下标)。"""
    parts = [first.strip()]
    j = i + 1
    while j < len(lines):
        nxt = lines[j]
        if nxt.strip() and nxt[0] in " \t" and not _SPHINX_ANY_FIELD.match(nxt.strip()):
            parts.append(nxt.strip())
            j += 1
        else:
            break
    return " ".join(p for p in parts if p), j


def _parse_sphinx(lines):
    result = _empty_result()
    params = {}
    returns_desc, returns_type = None, None
    body_lines = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        m = _SPHINX_PARAM.match(stripped)
        if m:
            name = m.group("name").lstrip("*")
            desc, i = _continuation(lines, i, m.group("desc"))
            entry = params.setdefault(
                name, {"name": name, "type": None, "description": "", "parsed": True})
            entry["description"] = desc
            continue
        m = _SPHINX_TYPE.match(stripped)
        if m:
            name = m.group("name").lstrip("*")
            entry = params.setdefault(
                name, {"name": name, "type": None, "description": "", "parsed": True})
            entry["type"] = m.group("type").strip() or None
            i += 1
            continue
        m = _SPHINX_RETURN.match(stripped)
        if m:
            returns_desc, i = _continuation(lines, i, m.group("desc"))
            continue
        m = _SPHINX_RTYPE.match(stripped)
        if m:
            returns_type = m.group("type").strip() or None
            i += 1
            continue
        m = _SPHINX_RAISES.match(stripped)
        if m:
            desc, i = _continuation(lines, i, m.group("desc"))
            result["raises"].append(
                {"name": m.group("name"), "description": desc, "parsed": True})
            continue
        if stripped.startswith(":") and _SPHINX_ANY_FIELD.match(stripped):
            # 不认识的字段：保留原文，标记未解析
            result["unparsed"].append({"raw": lines[i].rstrip(), "parsed": False})
            i += 1
            continue
        body_lines.append(lines[i])
        i += 1
    result["params"] = list(params.values())
    if returns_desc is not None or returns_type is not None:
        result["returns"] = {
            "type": returns_type,
            "description": returns_desc or "",
            "parsed": True,
        }
    result["body"] = "\n".join(body_lines).strip()
    return result


# ------------------------------------------------------- Google / NumPy --

def _find_sections(lines):
    """定位所有段落标题，返回 [(kind, header_idx, content_start, style, indent)]。"""
    headers = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        low = stripped.lower()
        indent = _indent(line)
        if low.endswith(":") and low[:-1] in _GOOGLE_SECTIONS:
            headers.append((_GOOGLE_SECTIONS[low[:-1]], i, i + 1, "google", indent))
        elif (
            low in _NUMPY_SECTIONS
            and i + 1 < len(lines)
            and lines[i + 1].strip()
            and set(lines[i + 1].strip()) == {"-"}
        ):
            headers.append((_NUMPY_SECTIONS[low], i, i + 2, "numpy", indent))
    return headers


def _section_end(lines, start, indent, style, stop_at):
    """计算段落内容结束行（开区间）。"""
    j = start
    while j < len(lines) and j < stop_at:
        line = lines[j]
        if not line.strip():
            # 空行：看下一个非空行是否仍属于本段
            k = j
            while k < len(lines) and not lines[k].strip():
                k += 1
            if k >= len(lines) or k >= stop_at:
                break
            nxt = lines[k]
            if _indent(nxt) > indent:
                j = k
                continue
            if style == "numpy" and _NUMPY_PARAM_RE.match(nxt.strip()):
                j = k
                continue
            break
        if style == "google" and _indent(line) <= indent:
            break
        if style == "numpy" and _indent(line) < indent:
            break
        j += 1
    return min(j, stop_at)


def _parse_sections(lines, headers):
    result = _empty_result()
    consumed = [False] * len(lines)
    for idx, (kind, h, start, style, indent) in enumerate(headers):
        stop_at = headers[idx + 1][1] if idx + 1 < len(headers) else len(lines)
        end = _section_end(lines, start, indent, style, stop_at)
        for n in range(h, end):
            consumed[n] = True
        content = lines[start:end]
        if kind == "params":
            if style == "google":
                params, unparsed = _parse_google_params(content)
            else:
                params, unparsed = _parse_numpy_params(content)
            result["params"].extend(params)
            result["unparsed"].extend(unparsed)
        elif kind == "returns":
            result["returns"] = _parse_returns(content, style)
        elif kind == "raises":
            result["raises"].extend(_parse_raises(content, style))
    result["body"] = "\n".join(
        lines[i] for i in range(len(lines)) if not consumed[i]
    ).strip()
    return result


def _base_indent(content):
    indents = [_indent(l) for l in content if l.strip()]
    return min(indents) if indents else 0


def _parse_google_params(content):
    params, unparsed = [], []
    current = None
    base = _base_indent(content)
    for line in content:
        if not line.strip():
            continue
        indent = _indent(line)
        m = _GOOGLE_PARAM_RE.match(line.strip()) if indent <= base else None
        if m:
            current = {
                "name": m.group("name").lstrip("*"),
                "type": (m.group("type") or "").strip() or None,
                "description": m.group("desc").strip(),
                "parsed": True,
            }
            params.append(current)
        elif current is not None and indent > base:
            current["description"] = (current["description"] + " " + line.strip()).strip()
        else:
            unparsed.append({"raw": line.rstrip(), "parsed": False})
            current = None
    return params, unparsed


def _parse_numpy_params(content):
    params, unparsed = [], []
    current = None
    base = _base_indent(content)
    for line in content:
        if not line.strip():
            continue
        indent = _indent(line)
        m = _NUMPY_PARAM_RE.match(line.strip()) if indent <= base else None
        if m:
            names = [n.strip().lstrip("*") for n in m.group("name").split(",")]
            group = []
            for name in names:
                entry = {
                    "name": name,
                    "type": m.group("rest").strip() or None,
                    "description": "",
                    "parsed": True,
                }
                params.append(entry)
                group.append(entry)
            current = group
        elif current is not None and indent > base:
            for entry in current:
                entry["description"] = (entry["description"] + " " + line.strip()).strip()
        else:
            unparsed.append({"raw": line.rstrip(), "parsed": False})
            current = None
    return params, unparsed


def _parse_returns(content, style):
    text_lines = [l for l in content if l.strip()]
    if not text_lines:
        return None
    if style == "google":
        first = text_lines[0].strip()
        m = re.match(r"^(?P<type>[\w.\[\], |]+?)\s*:\s*(?P<desc>.*)$", first)
        if m:
            desc = " ".join(
                [m.group("desc").strip()] + [l.strip() for l in text_lines[1:]]
            ).strip()
            return {"type": m.group("type").strip() or None,
                    "description": desc, "parsed": True}
        # 不匹配 type: desc 形式：保留原文，标记未解析
        return {"type": None,
                "description": " ".join(l.strip() for l in text_lines),
                "parsed": False}
    # numpy：首个非空行为类型，其余为描述
    rtype = text_lines[0].strip()
    desc = " ".join(l.strip() for l in text_lines[1:])
    return {"type": rtype or None, "description": desc, "parsed": True}


def _parse_raises(content, style):
    raises = []
    if style == "google":
        params, unparsed = _parse_google_params(content)
        for p in params:
            raises.append({"name": p["name"],
                           "description": p["description"], "parsed": True})
        for u in unparsed:
            raises.append({"name": None, "description": u["raw"], "parsed": False})
    else:
        params, _ = _parse_numpy_params(content)
        for p in params:
            raises.append({"name": p["name"],
                           "description": p["description"], "parsed": True})
    return raises
