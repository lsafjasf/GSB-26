"""从 Python 源码提取函数定义与调用关系（仅用标准库 ast）。

解析规则（静态可判定性分级）：
- `foo()` 且 foo 可解析到本模块定义            -> 确定边
- `self.m()` / `cls.m()` 且 m 是本类方法        -> 确定边
- `obj.m()`，obj 是 `obj = ClassName()` 的别名   -> 确定边
- `obj.m()`，obj 来源未知                      -> 不确定边：指向所有名为 m 的方法（动态分派候选）
- `param()` / `getattr(...)` / 下标 / lambda 等  -> 不确定边：指向合成节点 <unknown:...>
- 调用未定义的名字（外部库/内建）               -> 确定边：指向外部节点 <external:name>
- 模块级赋值别名 `x = foo` 会被解析；指向未知源的别名调用为不确定
所有不确定调用都以不确定边保留在图中，绝不丢弃。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

# 绑定种类
B_FUNC = "func"        # 绑定到本模块函数（fqn）
B_CLASS = "class"      # 绑定到本模块类
B_IMPORT = "import"    # 导入的模块/符号
B_UNKNOWN = "unknown"  # 其他（参数、未知表达式）


@dataclass
class Binding:
    kind: str
    target: str


@dataclass
class CallSite:
    caller: str            # 调用者 fqn；模块级装饰器调用为 "<module>"
    target: str            # 被调方：内部 fqn / <external:...> / <unknown:...>
    certain: bool          # True=静态可确定；False=不确定（动态分派/间接调用等）
    kind: str              # direct | method | external | dynamic-attr | dynamic-other | decorator
    lineno: int


@dataclass
class FunctionDef:
    fqn: str
    name: str
    lineno: int
    is_method: bool
    owner_class: Optional[str] = None
    node: Optional[ast.AST] = None          # 函数 AST 节点
    lexical_prefixes: Tuple[str, ...] = ()  # 外层函数 fqn 前缀链（用于嵌套函数可见名）


@dataclass
class ModuleModel:
    functions: Dict[str, FunctionDef] = field(default_factory=dict)
    module_bindings: Dict[str, Binding] = field(default_factory=dict)
    calls: List[CallSite] = field(default_factory=list)          # 函数体内的调用
    module_calls: List[CallSite] = field(default_factory=list)   # 模块级（装饰器等）调用，仅记录
    nested_names: Dict[str, Set[str]] = field(default_factory=dict)  # 函数 fqn -> 直接嵌套函数名


class _FnVisitor(ast.NodeVisitor):
    """提取单个函数体内的调用。"""

    def __init__(self, model: ModuleModel, fdef: FunctionDef):
        self.model = model
        self.fdef = fdef
        self.owner_class = fdef.owner_class
        self.counter = 0
        node = fdef.node
        args = node.args
        self.params: Set[str] = set(
            [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
            + ([args.vararg.arg] if args.vararg else [])
            + ([args.kwarg.arg] if args.kwarg else [])
        )
        pos = args.posonlyargs + args.args
        self.first_param: Optional[str] = pos[0].arg if pos else None
        self.local_aliases: Dict[str, Binding] = {}
        self._scan_locals(node)

    # ---- 局部绑定预扫描（保守：不进入嵌套作用域）----
    def _scan_locals(self, fn_node: ast.AST) -> None:
        stack = list(ast.iter_child_nodes(fn_node))
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(n, ast.Assign):
                self._bind_targets(n.targets, n.value)
            elif isinstance(n, ast.AnnAssign) and n.value is not None:
                self._bind_targets([n.target], n.value)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    self.local_aliases[(a.asname or a.name).split(".")[0]] = Binding(
                        B_IMPORT, a.name
                    )
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    self.local_aliases[a.asname or a.name] = Binding(B_IMPORT, a.name)
            stack.extend(ast.iter_child_nodes(n))

    def _bind_targets(self, targets: List[ast.expr], value: ast.expr) -> None:
        binding: Optional[Binding] = None
        if isinstance(value, ast.Name):
            binding = self._resolve_name(value.id)
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            b = self._resolve_name(value.func.id)
            if b is not None and b.kind == B_CLASS:
                binding = Binding(B_CLASS, b.target)  # 实例 -> 类
        if binding is None:
            return
        for t in targets:
            if isinstance(t, ast.Name):
                self.local_aliases[t.id] = binding

    # ---- 名字解析 ----
    def _resolve_name(self, name: str) -> Optional[Binding]:
        if name in self.local_aliases:
            return self.local_aliases[name]
        if name in self.params:
            return Binding(B_UNKNOWN, name)
        # 词法作用域：外层函数的直接嵌套定义
        for prefix in self.fdef.lexical_prefixes:
            if name in self.model.nested_names.get(prefix, ()):
                fqn = prefix + "." + name
                if fqn in self.model.functions:
                    return Binding(B_FUNC, fqn)
        if name in self.model.module_bindings:
            return self.model.module_bindings[name]
        if name in self.model.functions:
            return Binding(B_FUNC, name)
        return None

    # ---- 调用记录 ----
    def _emit(self, target: str, certain: bool, kind: str, lineno: int) -> None:
        self.model.calls.append(CallSite(self.fdef.fqn, target, certain, kind, lineno))

    def _emit_unknown(self, kind: str, lineno: int) -> None:
        self.counter += 1
        label = f"{self.fdef.fqn}#{self.counter}"
        self._emit(f"<unknown:{label}>", False, kind, lineno)

    # ---- 访问 ----
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            self._handle_name_call(func.id, node)
        elif isinstance(func, ast.Attribute):
            self._handle_attr_call(func, node)
        else:
            # 下标调用、lambda 立即调用、调用返回值等：无法静态确定
            self._emit_unknown("dynamic-other", node.lineno)
        self.generic_visit(node)

    def _handle_name_call(self, name: str, node: ast.Call) -> None:
        if name == "getattr" and len(node.args) >= 2:
            lit = node.args[1]
            if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                self._emit_dynamic_attr(lit.value, node.lineno)
                return
        b = self._resolve_name(name)
        if b is None:
            self._emit(f"<external:{name}>", True, "external", node.lineno)
        elif b.kind == B_FUNC:
            self._emit(b.target, True, "direct", node.lineno)
        elif b.kind == B_CLASS:
            init = f"{b.target}.__init__"
            if init in self.model.functions:
                self._emit(init, True, "direct", node.lineno)
            else:
                self._emit(f"<external:{b.target}>", True, "external", node.lineno)
        elif b.kind == B_IMPORT:
            self._emit(f"<external:{b.target}>", True, "external", node.lineno)
        else:
            self._emit_unknown("dynamic-other", node.lineno)

    def _handle_attr_call(self, func: ast.Attribute, node: ast.Call) -> None:
        value, attr = func.value, func.attr
        if isinstance(value, ast.Name):
            vid = value.id
            if self.owner_class and vid == self.first_param:
                cand = f"{self.owner_class}.{attr}"
                if cand in self.model.functions:
                    self._emit(cand, True, "method", node.lineno)
                    return
            b = self._resolve_name(vid)
            if b is not None:
                if b.kind == B_CLASS:
                    cand = f"{b.target}.{attr}"
                    if cand in self.model.functions:
                        self._emit(cand, True, "method", node.lineno)
                    else:
                        self._emit(f"<external:{b.target}.{attr}>", True, "external", node.lineno)
                    return
                if b.kind in (B_IMPORT, B_FUNC):
                    self._emit(f"<external:{b.target}.{attr}>", True, "external", node.lineno)
                    return
            # 接收者未知：动态分派，保留候选边（不确定）
            self._emit_dynamic_attr(attr, node.lineno)
            return
        if isinstance(value, ast.Call):
            inner = value.func
            if isinstance(inner, ast.Name) and inner.id == "super":
                if self.owner_class:
                    cand = f"{self.owner_class}.{attr}"
                    if cand in self.model.functions:
                        self._emit(cand, True, "method", node.lineno)
                        return
                self._emit(f"<external:super.{attr}>", True, "external", node.lineno)
                return
            if isinstance(inner, ast.Name):
                b = self._resolve_name(inner.id)
                if b is not None and b.kind == B_CLASS:
                    cand = f"{b.target}.{attr}"
                    if cand in self.model.functions:
                        self._emit(cand, True, "method", node.lineno)
                        return
            self._emit_unknown("dynamic-other", node.lineno)
            return
        # self.x.m() / 属性链 / 其他表达式：不确定
        self._emit_unknown("dynamic-other", node.lineno)

    def _emit_dynamic_attr(self, attr: str, lineno: int) -> None:
        """动态分派：向所有同名方法发不确定候选边，并保留一个未知兜底节点。"""
        for fqn, fd in self.model.functions.items():
            if fd.is_method and fd.name == attr:
                self._emit(fqn, False, "dynamic-attr", lineno)
        self._emit_unknown("dynamic-attr", lineno)

    # 跳过嵌套定义（嵌套函数是独立节点，单独提取）
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _collect(model: ModuleModel, body: List[ast.stmt], prefix: str,
             lexical: Tuple[str, ...], owner_class: Optional[str]) -> None:
    """第一遍：收集定义与模块级绑定。"""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fqn = prefix + node.name
            is_method = owner_class is not None and prefix == owner_class + "."
            fdef = FunctionDef(
                fqn=fqn,
                name=node.name,
                lineno=node.lineno,
                is_method=is_method,
                owner_class=owner_class if is_method else None,
                node=node,
                lexical_prefixes=lexical,
            )
            model.functions[fqn] = fdef
            if not lexical and owner_class is None:
                model.module_bindings.setdefault(node.name, Binding(B_FUNC, fqn))
            if lexical:
                model.nested_names.setdefault(lexical[-1], set()).add(node.name)
            _collect(model, node.body, fqn + ".", lexical + (fqn,), None)
            # 装饰器中的调用记为模块级调用（保守，不参与函数间环）
            for dec in node.decorator_list:
                for c in ast.walk(dec):
                    if isinstance(c, ast.Call):
                        t = ast.unparse(c.func)
                        model.module_calls.append(
                            CallSite("<module>", f"<external:{t}>", True, "decorator", node.lineno)
                        )
        elif isinstance(node, ast.ClassDef):
            cname = prefix + node.name
            if not lexical:
                model.module_bindings.setdefault(node.name, Binding(B_CLASS, cname))
            _collect(model, node.body, cname + ".", lexical, cname)
        elif isinstance(node, ast.Import) and not lexical:
            for a in node.names:
                model.module_bindings.setdefault(
                    (a.asname or a.name).split(".")[0], Binding(B_IMPORT, a.name)
                )
        elif isinstance(node, ast.ImportFrom) and not lexical:
            for a in node.names:
                model.module_bindings.setdefault(a.asname or a.name, Binding(B_IMPORT, a.name))
        elif isinstance(node, ast.Assign) and not lexical:
            _module_alias(model, node.targets, node.value)
        elif isinstance(node, ast.AnnAssign) and not lexical and node.value is not None:
            _module_alias(model, [node.target], node.value)


def _module_alias(model: ModuleModel, targets: List[ast.expr], value: ast.expr) -> None:
    binding: Optional[Binding] = None
    if isinstance(value, ast.Name):
        src = model.module_bindings.get(value.id)
        if src is not None:
            binding = src
        elif value.id in model.functions:
            binding = Binding(B_FUNC, value.id)
    if binding is None:
        return
    for t in targets:
        if isinstance(t, ast.Name):
            model.module_bindings.setdefault(t.id, binding)


def _extract_calls(model: ModuleModel) -> None:
    for fdef in model.functions.values():
        visitor = _FnVisitor(model, fdef)
        for stmt in fdef.node.body:
            visitor.visit(stmt)


def parse_module(source: str, module_name: str = "<module>") -> ModuleModel:
    """解析整个模块源码，返回函数定义与调用列表。"""
    tree = ast.parse(source)
    model = ModuleModel()
    _collect(model, tree.body, "", (), None)
    _extract_calls(model)
    return model


def parse_function_source(source: str, fqn: str, model: ModuleModel) -> List[CallSite]:
    """解析单个函数的新源码（用于增量更新），复用既有模块上下文解析调用。

    fqn 必须已存在于 model.functions（顶层函数或类方法）。
    """
    old = model.functions.get(fqn)
    if old is None:
        raise KeyError(f"未知函数: {fqn}")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = ast.parse("if True:\n" + "\n".join("    " + line for line in source.splitlines()))
    fn_nodes = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == old.name
    ]
    if not fn_nodes:
        raise ValueError(f"源码中未找到函数 {old.name}")
    new_def = FunctionDef(
        fqn=fqn,
        name=old.name,
        lineno=old.lineno,
        is_method=old.is_method,
        owner_class=old.owner_class,
        node=fn_nodes[0],
        lexical_prefixes=old.lexical_prefixes,
    )
    model.functions[fqn] = new_def
    before = len(model.calls)
    visitor = _FnVisitor(model, new_def)
    for stmt in new_def.node.body:
        visitor.visit(stmt)
    return model.calls[before:]
