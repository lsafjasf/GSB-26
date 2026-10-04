"""多环境分层配置：base -> 环境层(可 extends 链) -> local。

合并顺序（后者覆盖前者）:
    1. base           config/base.json，公共项只在这里写一次
    2. env:<name>     config/env/<name>.json，可通过 "extends" 继承其它环境
                      （父环境先于子环境合并，例如 staging -> prod）
    3. local          config/local.json，个人/本机覆盖，不入库也可缺失

每次合并都产生与生效配置同构的溯源数据（provenance），每个叶子值都标注
来源层。结构性冲突（同一路径上一层是映射、另一层是标量/列表）与必需项
缺失一律抛错，绝不静默取默认值。

仅使用 Python 3 标准库。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union


BASE_FILE = "base.json"
LOCAL_FILE = "local.json"

# required 条目：字符串表示“任意层提供即可”；字典 {"path", "layer"=...}
# 表示“必须由指定层提供”。
RequiredEntry = Union[str, Dict[str, str]]


class ConfigError(Exception):
    """配置错误基类，消息中必须指出具体的层与键。"""


class LayerConflictError(ConfigError):
    """层间结构性冲突：同一路径上映射 与 标量/列表 互斥。"""


class MissingRequiredError(ConfigError):
    """必需配置项缺失，或生效值的来源层不符合要求。"""


@dataclass(frozen=True)
class Layer:
    name: str
    data: Dict[str, Any]


@dataclass
class MergedConfig:
    """合并结果。

    data         生效配置
    provenance   与 data 同构的溯源树，叶子值为来源层名
    layers       实际参与合并的层名（按合并/覆盖顺序）
    """

    data: Dict[str, Any]
    provenance: Dict[str, Any]
    layers: List[str]

    def source_of(self, path: str) -> str:
        """返回点分路径上叶子配置的来源层名。

        路径不存在抛 KeyError；路径是嵌套节（来源不唯一）抛 KeyError。
        """
        node = self._locate(path)
        if isinstance(node, dict):
            raise KeyError(
                f"配置项 '{path}' 是嵌套节而非叶子值，请溯源到具体叶子键"
            )
        return node

    def provenance_map(self) -> Dict[str, str]:
        """扁平溯源数据: {'db.host': 'base', 'app.debug': 'env:prod', ...}"""
        out: Dict[str, str] = {}

        def walk(node: Dict[str, Any], prefix: str) -> None:
            for key, value in node.items():
                cur = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    walk(value, cur)
                else:
                    out[cur] = value

        walk(self.provenance, "")
        return out

    def check_required(self, required: Iterable[RequiredEntry]) -> None:
        for entry in required:
            if isinstance(entry, str):
                path, expect_layer = entry, None
            else:
                path, expect_layer = entry["path"], entry.get("layer")
            node = self._locate_or_none(path)
            if node is None:
                if expect_layer:
                    detail = f"，该项要求由层 '{expect_layer}' 提供"
                else:
                    detail = "，以上各层均未提供该项"
                raise MissingRequiredError(
                    f"缺少必需配置项 '{path}'{detail}；已检查的层（低 -> 高优先级）: "
                    + " < ".join(self.layers)
                )
            if expect_layer:
                if isinstance(node, dict):
                    raise MissingRequiredError(
                        f"必需配置项 '{path}' 要求由层 '{expect_layer}' 完整提供，"
                        f"但它是嵌套节，来源不唯一（多个层各写了一部分）"
                    )
                if node != expect_layer:
                    raise MissingRequiredError(
                        f"必需配置项 '{path}' 要求由层 '{expect_layer}' 提供，"
                        f"但生效值实际来自层 '{node}'"
                    )

    def _locate_or_none(self, path: str) -> Optional[Any]:
        node: Any = self.provenance
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def _locate(self, path: str) -> Any:
        node = self._locate_or_none(path)
        if node is None:
            raise KeyError(f"配置项 '{path}' 不存在，无法溯源")
        return node


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


def _stamp(value: Any, layer_name: str) -> Any:
    """给整棵新增子树盖上来源层戳记。"""
    if _is_mapping(value):
        return {key: _stamp(child, layer_name) for key, child in value.items()}
    return layer_name


def _merge_dict(
    old: Dict[str, Any],
    new: Dict[str, Any],
    path: str,
    layer_name: str,
    prov: Dict[str, Any],
) -> Dict[str, Any]:
    """把层 new 深度合并进 old；prov 是 old 的溯源树，就地更新。"""
    merged = dict(old)
    for key, new_value in new.items():
        cur_path = f"{path}.{key}" if path else key
        if key in old:
            old_value = old[key]
            if _is_mapping(old_value) != _is_mapping(new_value):
                raise LayerConflictError(
                    f"配置冲突：键 '{cur_path}' 在层 '{layer_name}' 中是"
                    f"{'映射对象' if _is_mapping(new_value) else '标量/列表'}，"
                    f"而此前合并的层中是"
                    f"{'映射对象' if _is_mapping(old_value) else '标量/列表'}；"
                    f"结构性类型不一致，问题出在层 '{layer_name}'，请修正后再合并"
                )
            if _is_mapping(new_value):
                merged[key] = _merge_dict(
                    old_value, new_value, cur_path, layer_name, prov.setdefault(key, {})
                )
            else:
                # 标量与列表整体替换（不做逐元素合并），溯源指向当前层
                merged[key] = copy.deepcopy(new_value)
                prov[key] = layer_name
        else:
            merged[key] = copy.deepcopy(new_value)
            prov[key] = _stamp(new_value, layer_name)
    return merged


def merge_layers(
    layers: Sequence[Layer], required: Iterable[RequiredEntry] = ()
) -> MergedConfig:
    """按给定层顺序（低 -> 高优先级）深度合并。"""
    if not layers:
        raise ConfigError("至少需要一个配置层，无法合并")
    data: Dict[str, Any] = {}
    provenance: Dict[str, Any] = {}
    for layer in layers:
        if not _is_mapping(layer.data):
            raise ConfigError(f"层 '{layer.name}' 顶层必须是 JSON 对象")
        data = _merge_dict(data, layer.data, "", layer.name, provenance)
    merged = MergedConfig(
        data=data, provenance=provenance, layers=[layer.name for layer in layers]
    )
    merged.check_required(required)
    return merged


def _read_json(path: Path, layer_name: str) -> Dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"层 '{layer_name}' 的配置文件无法读取 {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"层 '{layer_name}' 的配置文件 {path} 不是合法 JSON（{exc.msg}，"
            f"第 {exc.lineno} 行第 {exc.colno} 列），问题出在该层"
        ) from exc
    if not _is_mapping(data):
        raise ConfigError(f"层 '{layer_name}' 的配置文件 {path} 顶层必须是 JSON 对象")
    return data


def _resolve_env_chain(
    env: str, config_dir: Path
) -> List[Tuple[str, Dict[str, Any]]]:
    """沿 "extends" 解析环境继承链，返回 [父环境, ..., 目标环境]。"""
    chain: List[Tuple[str, Dict[str, Any]]] = []
    seen: List[str] = []
    current: Optional[str] = env
    referrer = "命令行参数"
    while current is not None:
        if current in seen:
            cycle = " -> ".join(seen[seen.index(current):] + [current])
            raise ConfigError(f"环境继承存在循环: {cycle}；请检查各环境层的 extends")
        path = config_dir / "env" / f"{current}.json"
        if not path.is_file():
            raise ConfigError(
                f"环境层 '{current}' 的配置文件不存在: {path}"
                f"（由层 '{referrer}' 通过 extends/参数引用），缺失的是环境层"
            )
        data = _read_json(path, f"env:{current}")
        parent = data.pop("extends", None)
        if parent is not None and not isinstance(parent, str):
            raise ConfigError(
                f"层 'env:{current}' 的 extends 必须是环境名字符串，问题出在该层"
            )
        chain.append((current, data))
        seen.append(current)
        referrer = f"env:{current}"
        current = parent
    chain.reverse()
    return chain


def load_environment(
    env: str,
    config_dir: Union[str, Path] = "config",
    required: Iterable[RequiredEntry] = (),
) -> MergedConfig:
    """加载三层配置并合并：base < env 继承链(父->子) < local。"""
    config_dir = Path(config_dir)
    base_path = config_dir / BASE_FILE
    if not base_path.is_file():
        raise ConfigError(f"基础层配置缺失: {base_path}；公共项必须在基础层声明")
    layers: List[Layer] = [Layer("base", _read_json(base_path, "base"))]
    for name, data in _resolve_env_chain(env, config_dir):
        layers.append(Layer(f"env:{name}", data))
    local_path = config_dir / LOCAL_FILE
    if local_path.is_file():
        layers.append(Layer("local", _read_json(local_path, "local")))
    return merge_layers(layers, required=required)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="多环境分层配置合并（base < env 继承链 < local）"
    )
    parser.add_argument("env", help="环境名，对应 config/env/<env>.json")
    parser.add_argument("--config-dir", default="config")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="PATH[@LAYER]",
        help="必需配置项（点分路径），可重复；PATH 任意层提供即可，"
        "PATH@layer 要求必须由指定层提供",
    )
    parser.add_argument(
        "--provenance", action="store_true", help="在 JSON 后输出每个生效值的来源层"
    )
    args = parser.parse_args(argv)

    required: List[RequiredEntry] = []
    for item in args.require:
        if "@" in item:
            path, layer = item.rsplit("@", 1)
            required.append({"path": path, "layer": layer})
        else:
            required.append(item)

    try:
        merged = load_environment(args.env, args.config_dir, required=required)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(merged.data, indent=2, ensure_ascii=False, sort_keys=True))
    if args.provenance:
        print("--- provenance（path <= source layer，合并顺序: "
              + " < ".join(merged.layers) + ") ---")
        for path, source in sorted(merged.provenance_map().items()):
            print(f"{path} <= {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
