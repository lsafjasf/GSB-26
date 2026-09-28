# GSB-26: 一个小型约束式类型检查器（Python 3，仅标准库）

为内部 DSL 在执行前做静态类型检查。从语法树生成类型约束并求解，
支持函数类型、泛型占位（let 多态）、递归与相互递归定义、基本类型推导；
类型错误定位到具体表达式位置，给出期望/实际类型与导致冲突的约束链。

## 目录结构

- `typecheck/types.py` — 类型表示：`TCon`（int/bool/str）、`TFun`、`TVar`（可解变量）、`QVar`（泛型占位）、`Scheme`（多态类型）
- `typecheck/solver.py` — 约束求解器：union-find 合一 + occurs check + 约束日志（用于错误链）+ 步数预算
- `typecheck/checker.py` — 约束生成与推导（Hindley-Milner 风格）、标注解析、内建函数、无法推断检查
- `typecheck/ast_nodes.py` — AST 节点，每个节点携带源码位置 `Pos(line, col)`
- `typecheck/errors.py` — 诊断与渲染（位置、期望/实际类型、约束链）
- `tests/test_typecheck.py` — 22 个自测
- `bench.py` — 大规模程序性能测试
- `examples/type_errors.py` — 类型错误样例集

## 语言

字面量 `int/bool/str`，变量，`\x. body`（`Lam`，可带参数标注），调用（柯里化），
`let`（泛化），`letrec` 互递归绑定组，`if`。程序 = 若干顶层递归定义组 + 可选主表达式。
内建：`add/sub/mul/neg/lt : int -> ...`、`not/concat/show`、`eq : forall a. a -> a -> bool`。
标注语法：`int -> int`、`(a -> b) -> a -> b`（小写名为泛型占位）。

## 设计

1. **约束生成**：`Checker.infer` 遍历 AST，为每个局部类型事实（调用、if 条件、分支一致、
   标注、递归绑定）生成一条 `Constraint(left, right, reason, node)` 并立即交给求解器。
2. **求解**：`Unifier` 用带路径压缩的 union-find 合一；每次绑定记录"由哪条约束绑定"
   （`TVar.bound_by`），形成溯源日志。
3. **泛型/多态**：`let`、`letrec`、顶层定义边界处对不在环境中自由的变量做泛化得到
   `Scheme`；引用处实例化（泛型占位 → 全新变量）。
4. **递归**：互递归组先为每个名字预绑定一个全新类型变量，再检查组内所有右式，
   最后统一泛化。递归引用只是引用该变量，不展开任何定义。
5. **错误链**：冲突时沿两侧类型的 `bound_by` 链接回溯，按约束编号排序，
   加上失败约束本身，渲染为约束链（见下方样例）。

## 终止性说明

求解必然终止，由四层机制保证：

- **occurs check**：绑定 `X := T` 前检查 `X` 不出现 在 `T` 中，无限类型
  （如 `\x. x x`、`letrec f = \x. f f`）在 O(1) 条约束内被拒绝，不会无限展开。
- **递归定义不展开**：递归/互递归通过预绑定变量处理，每个递归引用只产生 O(1) 条约束；
  检查一个大小为 n 的组恰好遍历 AST 一次。
- **泛化有界**：只在 let 边界泛化；实例化复制的大小以 scheme 大小为界，不递归进入定义。
- **防御性步数预算**：`Unifier(max_steps=2_000_000)` 超预算即抛 `FuelExhaustedError`，
  宁可报错也不挂起（正常程序远达不到，见性能数据的 steps 列）。

复杂度：合一近线性（union-find 的反阿克曼因子）；泛化时扫描环境，闭类型
（`Scheme.closed`）跳过扫描，实测整体近线性（见下表）。深层 AST 的 Python 递归
深度由 `check_program` 提升至 100000 兜底。

## 类型错误样例集

`python3 examples/type_errors.py` 的实际输出：

```text
=== add 1 true ===
error[conflict] at 1:7: type mismatch (function application)
  expected: int
  actual:   bool
  constraint chain:
    #1 at 1:7: function application: int -> int ~ bool -> 'a

=== apply (\x. add x 1) true ===
error[conflict] at 3:9: type mismatch (function application)
  expected: int
  actual:   bool
  constraint chain:
    #3 at 3:9: function application: (int -> int) -> int -> int ~ (int -> int) -> int -> int
    #4 at 3:9: function application: int -> int ~ bool -> 'a

=== \x. x x  (occurs check) ===
error[occurs] at 5:8: recursive value would have an infinite type
  expected: 'a
  actual:   'a -> 'b
  constraint chain:
    #0 at 5:8: function application: 'a ~ 'a -> 'b

=== mutual recursion with bad branch ===
error[conflict] at 9:12: type mismatch (if branches must agree)
  expected: int
  actual:   bool
  constraint chain:
    #13 at 0:0: function application: int -> bool ~ int -> bool
    #14 at 9:12: if branches must agree: int ~ bool

=== \x. 1  (uninferred parameter) ===
error[uninferred] at 7:1: cannot infer the type of parameter 'x'; add an annotation

=== \x. eq x x  (polymorphic parameter, hint only) ===
hint[polymorphic] at 9:1: parameter 'x' is polymorphic ('a -> bool); add an annotation to pin it down
```

约束链中的类型以求解完成后的最终形态渲染。错误分四类：
`conflict`（类型冲突）、`occurs`（无限类型）、`unbound`（未绑定变量）、
`uninferred`（无法推断，见下）；另有 `hint` 级别的 `polymorphic` 提示（见下）。

## 未标注与无法推断的类型

不会默认成 `any` 放行：

- 未绑定变量立即报 `unbound` 错误；
- lambda 参数在全部求解结束后仍是自由变量、且未流入结果类型（如 `\x. 1` 的 `x`），
  作为 `uninferred` 诊断显式报告（`CheckResult.diagnostics`），并提示加标注；
- 参数虽被引用、但类型仍是只出现在参数位置的自由变量（如 `\x. eq x x` 得
  `'a -> bool`），属于合法多态：只给 `hint` 级 `polymorphic` 提示，不算错误；
- 真正的多态（如 `\x. x` 得 `'a -> 'a`）不算无法推断，正常放行；
- 结果类型中未解出的变量渲染为 `'a` 等占位符，绝不显示为 `any`。

## 性能数据

`python3 bench.py`（Python 3.12，本机实测）：

| 用例 | AST 节点 | 定义数 | 约束数 | 求解步数 | 耗时 |
|---|---|---|---|---|---|
| 深依赖链 | 20,001 | 2,500 | 10,000 | 25,000 | ~66 ms |
| 多态实例化密集 | 20,005 | 2,501 | 10,002 | 25,004 | ~76 ms |
| 互递归组（1000 组） | 30,003 | 1,000 | 16,001 | 36,003 | ~108 ms |
| 深依赖链 x2 | 40,001 | 5,000 | 20,000 | 50,000 | ~264 ms |

约 15–30 万节点/秒，随规模近线性增长。约束数与步数随节点数线性增长，
佐证终止性分析。

## 运行命令

```bash
python3 -m unittest discover -s tests -v   # 自测（22 个用例）
python3 bench.py                            # 大规模性能测试
python3 examples/type_errors.py             # 类型错误样例集
```

## 自测覆盖

- 空程序、单字面量、单表达式、字符串/布尔运算
- 递归函数（阶乘）、相互递归（even/odd）
- 多态：`id` 在不同类型处实例化、多态内建 `eq`、泛型占位标注 `a -> b -> a`
- 类型冲突：参数不匹配（含位置断言）、if 分支不一致、标注不符、
  冲突穿过多态函数时的约束链（链长 ≥ 2）
- 终止性：`\x. x x` 与 `letrec f = \x. f f` 的 occurs 拒绝；1.5 万+ 节点程序限时完成
- 无法推断：`\x. 1` 报告参数、顶层未标注定义报告；`\x. x` 不误报；
  `\x. eq x x` 只给多态提示；`\x. (\x. x) 1` 外层被遮蔽参数仍报错
