"""多环境配置继承核心逻辑（仅标准库）。

覆盖顺序（后者覆盖前者）：
    base -> 环境继承链（父环境 -> 子环境）-> local

合并规则：
    * 字典与字典递归深合并；
    * 标量 / 列表整体替换（列表不做元素级合并）；
    * 同一键一边是字典、另一边不是字典 => 类型冲突，报错并指出两层来源；
    * base 可通过 "__required__": ["a.b", ...] 声明必需键，
      合并后缺失或为 null 即报错，绝不静默取默认值。
"""

import json
import os
import re

INHERITS_KEY = "__inherits__"
REQUIRED_KEY = "__required__"
RESERVED_KEYS = (INHERITS_KEY, REQUIRED_KEY)

_ENV_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ConfigError(Exception):
    """配置系统所有错误的基类。"""


class LayerError(ConfigError):
    """某一层的文件缺失、非法或内容不合法。"""


class CycleError(ConfigError):
    """环境继承链出现环。"""


class TypeConflictError(ConfigError):
    """同一键在不同层一个是字典、一个不是字典。"""


class MissingRequiredError(ConfigError):
    """必需键在合并结果中缺失或为 null。"""


def _validate_env_name(name):
    if not isinstance(name, str) or not _ENV_NAME_RE.match(name) or name in (".", ".."):
        raise LayerError("非法的环境名: %r（只允许字母数字及 . _ -，且不能以 . 开头）" % (name,))


def _load_layer_file(path, layer_name):
    """读取一层配置；文件缺失或内容非法时抛出带层信息的 LayerError。"""
    if not os.path.isfile(path):
        raise LayerError("层 %s 的配置文件缺失: %s" % (layer_name, path))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise LayerError("层 %s 的配置文件不是合法 JSON: %s（%s）" % (layer_name, path, exc))
    if not isinstance(data, dict):
        raise LayerError("层 %s 的配置文件顶层必须是 JSON 对象: %s" % (layer_name, path))
    return data


def _resolve_env_chain(config_dir, env):
    """解析环境继承链，返回 [(env_name, file_path, data), ...]，父环境在前。"""
    order = []
    seen = []
    current = env
    while current is not None:
        _validate_env_name(current)
        if current in seen:
            raise CycleError(
                "环境继承链出现环: %s" % " -> ".join(seen + [current])
            )
        seen.append(current)
        path = os.path.join(config_dir, "envs", current + ".json")
        data = _load_layer_file(path, "env:%s" % current)
        parent = data.get(INHERITS_KEY)
        if parent is not None:
            _validate_env_name(parent)
        order.append((current, path, data))
        current = parent
    order.reverse()
    return order


def _record(prov, path, value, layer_name, layer_file):
    entry = prov.setdefault(path, {"value": None, "layer": None, "file": None, "history": []})
    entry["value"] = value
    entry["layer"] = layer_name
    entry["file"] = layer_file
    entry["history"].append({"layer": layer_name, "file": layer_file, "value": value})


def _merge_layer(merged, prov, origins, layer_cfg, layer_name, layer_file, path=""):
    """把一层配置深合并进 merged，并记录每个叶子键的溯源信息。"""
    for key, value in layer_cfg.items():
        if key in RESERVED_KEYS:
            continue
        full = "%s.%s" % (path, key) if path else key
        if key in merged:
            old = merged[key]
            if isinstance(old, dict) and isinstance(value, dict):
                _merge_layer(old, prov, origins, value, layer_name, layer_file, full)
            elif isinstance(old, dict) != isinstance(value, dict):
                origin = origins.get(full, {})
                raise TypeConflictError(
                    "键 %r 类型冲突: 层 %s（%s）提供的是 %s，"
                    "但层 %s（%s）已将其定义为 %s"
                    % (
                        full,
                        layer_name,
                        layer_file,
                        "对象" if isinstance(value, dict) else "非对象",
                        origin.get("layer"),
                        origin.get("file"),
                        "对象" if isinstance(old, dict) else "非对象",
                    )
                )
            else:
                merged[key] = value
                origins[full] = {"layer": layer_name, "file": layer_file}
                _record(prov, full, value, layer_name, layer_file)
        else:
            merged[key] = value
            if isinstance(value, dict):
                origins[full] = {"layer": layer_name, "file": layer_file}
                _record_subtree(prov, origins, full, value, layer_name, layer_file)
            else:
                origins[full] = {"layer": layer_name, "file": layer_file}
                _record(prov, full, value, layer_name, layer_file)


def _record_subtree(prov, origins, path, subtree, layer_name, layer_file):
    for key, value in subtree.items():
        full = "%s.%s" % (path, key)
        if isinstance(value, dict):
            origins[full] = {"layer": layer_name, "file": layer_file}
            _record_subtree(prov, origins, full, value, layer_name, layer_file)
        else:
            origins[full] = {"layer": layer_name, "file": layer_file}
            _record(prov, full, value, layer_name, layer_file)


def _lookup(merged, dotted):
    node = merged
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _check_required(required, merged, layer_names):
    problems = []
    for dotted in required:
        found, value = _lookup(merged, dotted)
        if not found:
            problems.append("必需键 %r 在所有层中都未定义" % dotted)
        elif value is None:
            problems.append("必需键 %r 的生效值为 null" % dotted)
    if problems:
        raise MissingRequiredError(
            "%s（已检查的层: %s）" % ("；".join(problems), " -> ".join(layer_names))
        )


def load_config(config_dir, env, local=True):
    """加载并合并指定环境的配置。

    参数:
        config_dir: 配置根目录，需包含 base.json 与 envs/<env>.json。
        env:        目标环境名。
        local:      是否叠加 local.json（该层可选，文件不存在不视为错误）。

    返回:
        (merged, provenance)
        merged      —— 合并后的配置字典。
        provenance  —— 溯源数据：
            {
              "env": ...,
              "layer_order": ["base", "env:...", ..., "local"?],
              "local_layer": {"present": bool, "file": str | None},
              "values": {
                "a.b": {"value": ..., "layer": ..., "file": ...,
                        "history": [{"layer": ..., "file": ..., "value": ...}, ...]}
              }
            }
    """
    _validate_env_name(env)

    layers = []  # (layer_name, file_path, data)
    base_path = os.path.join(config_dir, "base.json")
    layers.append(("base", base_path, _load_layer_file(base_path, "base")))

    for env_name, env_path, env_data in _resolve_env_chain(config_dir, env):
        layers.append(("env:%s" % env_name, env_path, env_data))

    local_path = os.path.join(config_dir, "local.json")
    local_present = local and os.path.isfile(local_path)
    if local_present:
        layers.append(("local", local_path, _load_layer_file(local_path, "local")))

    merged = {}
    values_prov = {}
    origins = {}
    for layer_name, layer_file, layer_data in layers:
        _merge_layer(merged, values_prov, origins, layer_data, layer_name, layer_file)

    required = layers[0][2].get(REQUIRED_KEY, [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise LayerError("base 层的 %s 必须是字符串数组: %s" % (REQUIRED_KEY, base_path))
    _check_required(required, merged, [name for name, _, _ in layers])

    provenance = {
        "env": env,
        "layer_order": [name for name, _, _ in layers],
        "local_layer": {"present": local_present, "file": local_path if local_present else None},
        "values": values_prov,
    }
    return merged, provenance
