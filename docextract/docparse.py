"""结构化解析 docstring 中的参数/返回说明。

支持三种主流格式：
  - reST/Sphinx: ``:param name: desc`` / ``:type name:`` / ``:returns:`` / ``:rtype:``
  - Google:      ``Args:`` / ``Returns:`` 段落
  - NumPy:       ``Parameters`` + 下划线段落

格式不匹配的行不丢弃：保留原文并标记 parsed=False，放入 unparsed。
"""

import re

__all__ = ["parse_docstring"]

_GOOGLE_SECTION = re.compile(
    r"^(Args|Arguments|Parameters|Returns|Yields|Raises)\s*:\s*$")
_GOOGLE_PARAM = re.compile(
    r"^\s*(?P<name>\*{0,2}[^\W\d]\w*)"
    r"(?:\s*\((?P<type>[^)]*)\))?\s*:\s*(?P<desc>.*)$")
_NUMPY_HEADER = re.compile(r"^(Parameters|Returns|Yields|Raises)\s*$")
_NUMPY_UNDERLINE = re.compile(r"^-{3,}\s*$")
_NUMPY_PARAM = re.compile(
    r"^(?P<name>\*{0,2}[^\W\d]\w*)\s*:\s*(?P<type>.+?)\s*$")
_REST_FIELD = re.compile(
    r"^:(param|type|returns?|rtype|raises?|yields?)"
    r"(?:\s+(?P<extra>[^:]*?))?\s*:\s*(?P<desc>.*)$")


def _empty():
    return {"body": "", "params": [], "returns": None,
            "unparsed": [], "style": None}


def parse_docstring(doc):
    """把 docstring 解析为结构化字典；无法识别结构时整体作为 body。"""
    result = _empty()
    if not doc or not doc.strip():
        return result
    lines = doc.expandtabs().split("\n")
    style = _detect_style(lines)
    result["style"] = style
    if style == "rest":
        _parse_rest(lines, result)
    elif style == "google":
        _parse_google(lines, result)
    elif style == "numpy":
        _parse_numpy(lines, result)
    else:
        result["body"] = doc.strip()
    return result


def _detect_style(lines):
    for i, line in enumerate(lines):
        s = line.strip()
        if _REST_FIELD.match(s):
            return "rest"
        if _GOOGLE_SECTION.match(s):
            return "google"
        if (_NUMPY_HEADER.match(s) and i + 1 < len(lines)
                and _NUMPY_UNDERLINE.match(lines[i + 1].strip())):
            return "numpy"
    return None


# ---------------------------------------------------------------- reST

def _parse_rest(lines, result):
    params = {}
    order = []
    body_lines = []
    returns = None
    for line in lines:
        s = line.strip()
        m = _REST_FIELD.match(s)
        if not m:
            body_lines.append(line)
            continue
        field = m.group(1)
        extra = (m.group("extra") or "").strip()
        desc = m.group("desc").strip()
        if field == "param":
            # 支持 ":param type name:" 与 ":param name:" 两种写法
            parts = extra.rsplit(" ", 1)
            if len(parts) == 2:
                ptype, name = parts
            else:
                ptype, name = "", parts[0]
            if not name:
                result["unparsed"].append(
                    {"raw": line, "section": "params", "parsed": False})
                continue
            params[name] = {"name": name, "type": ptype or None,
                            "desc": desc, "parsed": True}
            order.append(name)
        elif field == "type":
            name = extra
            if name in params:
                params[name]["type"] = desc or None
            else:
                params[name] = {"name": name, "type": desc or None,
                                "desc": "", "parsed": True}
                order.append(name)
        elif field in ("returns", "return"):
            returns = returns or {"type": None, "desc": "", "parsed": True}
            returns["desc"] = (returns["desc"] + " " + desc).strip()
        elif field == "rtype":
            returns = returns or {"type": None, "desc": "", "parsed": True}
            returns["type"] = desc or None
        else:  # raises / yields 等非目标字段：保留原文，不丢弃
            result["unparsed"].append(
                {"raw": line, "section": field, "parsed": False})
    result["params"] = [params[n] for n in order]
    result["returns"] = returns
    result["body"] = "\n".join(body_lines).strip()


# ---------------------------------------------------------------- Google

def _parse_google(lines, result):
    body_lines = []
    i = 0
    n = len(lines)
    while i < n:
        m = _GOOGLE_SECTION.match(lines[i].strip())
        if not m:
            body_lines.append(lines[i])
            i += 1
            continue
        section = m.group(1)
        i += 1
        block = []
        while i < n and (lines[i].startswith((" ", "\t"))
                         or not lines[i].strip()):
            block.append(lines[i])
            i += 1
        # 去掉段落尾部空行（空行可能属于 body 间距）
        while block and not block[-1].strip():
            block.pop()
        if section in ("Args", "Arguments", "Parameters"):
            _google_params(block, result)
        elif section == "Returns":
            _google_returns(block, result)
        else:  # Raises / Yields：保留原文
            for raw in block:
                if raw.strip():
                    result["unparsed"].append(
                        {"raw": raw.strip(), "section": section.lower(),
                         "parsed": False})
    result["body"] = "\n".join(body_lines).strip()


def _google_params(block, result):
    current = None
    for raw in block:
        if not raw.strip():
            continue
        m = _GOOGLE_PARAM.match(raw)
        if m:
            current = {"name": m.group("name"),
                       "type": (m.group("type") or None),
                       "desc": m.group("desc").strip(), "parsed": True}
            result["params"].append(current)
        elif current is not None and raw.startswith((" ", "\t")) \
                and len(raw) - len(raw.lstrip()) > _indent_of(block[0]):
            # 缩进更深的续行：并入上一个参数的说明
            current["desc"] = (current["desc"] + " "
                               + raw.strip()).strip()
        else:
            # 不匹配参数格式：保留原文并标记未解析
            result["unparsed"].append(
                {"raw": raw.strip(), "section": "params", "parsed": False})
            current = None


def _google_returns(block, result):
    text = " ".join(r.strip() for r in block if r.strip())
    if not text:
        return
    m = re.match(r"^(?P<type>[\w\.\[\], ]+?)\s*:\s*(?P<desc>.*)$", text)
    if m:
        result["returns"] = {"type": m.group("type"),
                             "desc": m.group("desc").strip(),
                             "parsed": True}
    else:
        result["returns"] = {"type": None, "desc": text, "parsed": True}


def _indent_of(line):
    return len(line) - len(line.lstrip())


# ---------------------------------------------------------------- NumPy

def _parse_numpy(lines, result):
    body_lines = []
    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        m = _NUMPY_HEADER.match(s)
        if not (m and i + 1 < n
                and _NUMPY_UNDERLINE.match(lines[i + 1].strip())):
            body_lines.append(lines[i])
            i += 1
            continue
        section = m.group(1)
        i += 2
        block = []
        while i < n and not (
                _NUMPY_HEADER.match(lines[i].strip())
                and i + 1 < n
                and _NUMPY_UNDERLINE.match(lines[i + 1].strip())):
            block.append(lines[i])
            i += 1
        if section == "Parameters":
            _numpy_params(block, result)
        elif section == "Returns":
            _numpy_returns(block, result)
        else:
            for raw in block:
                if raw.strip():
                    result["unparsed"].append(
                        {"raw": raw.strip(), "section": section.lower(),
                         "parsed": False})
    result["body"] = "\n".join(body_lines).strip()


def _numpy_params(block, result):
    current = None
    for raw in block:
        if not raw.strip():
            continue
        indented = raw.startswith((" ", "\t"))
        m = _NUMPY_PARAM.match(raw.strip()) if not indented else None
        if m:
            current = {"name": m.group("name"), "type": m.group("type"),
                       "desc": "", "parsed": True}
            result["params"].append(current)
        elif indented and current is not None:
            current["desc"] = (current["desc"] + " "
                               + raw.strip()).strip()
        else:
            result["unparsed"].append(
                {"raw": raw.strip(), "section": "params", "parsed": False})
            current = None


def _numpy_returns(block, result):
    meaningful = [r for r in block if r.strip()]
    if not meaningful:
        return
    rtype = meaningful[0].strip()
    desc = " ".join(r.strip() for r in meaningful[1:])
    result["returns"] = {"type": rtype or None, "desc": desc,
                         "parsed": True}
