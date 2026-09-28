# tplcheck — 模板占位符校验与类型检查库

纯 Python 3 标准库实现（无第三方依赖）。用于在**发布前**发现多语言模板中的
占位符缺失、顺序不一致、类型冲突与作用域错误，而不是等到运行时才暴露。

## 模板语法

| 语法 | 含义 |
| --- | --- |
| `{name}` | 占位符，类型为 `any` |
| `{name:type}` | 带类型标注的占位符 |
| `{name:type?}` / `{name?}` | 可选参数（允许缺省或为 `None`，渲染为空串） |
| `{#if flag} ... {/if}` | 条件段，`flag` 必须是 `bool` |
| `{#each items as item} ... {/each}` | 循环段，`items` 必须是 `list`，`item` 为段内循环变量 |
| `{{` / `}}` | 字面量大括号转义 |

支持的类型：`any, str, int, float, bool, date, datetime, list`。
段可以任意嵌套。

## 校验规则

**单模板校验（`tplcheck.validate`）**，所有错误带行:列位置：

- `TYPE_CONFLICT` — 同一变量出现不兼容的类型标注（如 `{x:int}` 与 `{x:str}`）；
  `any` 与任何具体类型兼容，合并后取具体类型。
- `OUT_OF_SCOPE` — 循环变量只在定义它的 `{#each}` 段内可见（词法作用域，
  内层可遮蔽外层同名变量）；段外（包括其它 `{#if}` 条件中）引用即报错。
- 条件段变量隐含 `bool` 类型、循环段源隐含 `list` 类型，与其它标注冲突时
  报 `TYPE_CONFLICT`。
- `OPTIONAL_INCONSISTENT`（警告）— 同一参数一处标 `?` 一处不标，按必填处理。
- 语法错误（未闭合段、闭合标签不匹配、非法类型名、未转义大括号等）抛出
  `TemplateSyntaxError` 并携带位置。

校验通过后得到**参数签名**：按首次出现顺序排列的参数名、合并后的类型、
可选标记（循环变量是局部变量，不进签名；但其在循环体内的使用会以
「循环路径.变量」的形式记入 `loop_fields`，如 `users[].u`、嵌套时为
`users[].orders[].o`，并参与跨语言比较）。

**跨语言比较（`tplcheck.compare`）**：以第一个语言（或指定 `reference=`）
为基准，逐语言报告：

- `MISSING_PLACEHOLDER` — 基准里有、本语言缺失的占位符；
- `EXTRA_PLACEHOLDER` — 本语言多出的占位符；
- `ORDER_MISMATCH` — 共有占位符的相对顺序与基准不一致（可用
  `check_order=False` 关闭，适用于语序天然不同的语言对）；
- `TYPE_CONFLICT` / `OPTIONAL_CONFLICT` — 同名占位符类型/可选性不一致；
- 循环体字段同样参与比较：同一列表在不同语言里用了不同字段（如
  `{#each users as u}{u:str}{/each}` 对 `{#each users as n}{n:str}{/each}`）
  报 `MISSING_PLACEHOLDER` / `EXTRA_PLACEHOLDER`；同一字段类型不一致
  （`users[].u` 一处 `str` 一处 `int`）报 `TYPE_CONFLICT`；
- 各语言模板自身的校验错误与语法错误也一并带出（标注语言与位置）。

## 与渲染实现对拍

`tplcheck.render` 是严格参考渲染器，校验与渲染满足两条不变量
（`tests/test_differential.py` 中用随机模板 + 故障注入验证）：

1. **校验通过 ⇒ 渲染成功**：按提取的签名生成符合类型的参数，
   渲染必然成功（600 个随机模板，含嵌套段/循环/可选参数）。
2. **校验拒绝 ⇒ 渲染失败**：对合法模板注入故障（类型冲突、段外引用
   循环变量、对非 list 变量循环、未闭合段），校验拒绝的模板用同样参数
   渲染必然抛出 `RenderError` / `TemplateSyntaxError`（每类 100–200 例，
   另有 9 个固定对照用例）。

## 运行命令

```bash
# 全部自测（61 个用例：解析/校验/比较/渲染/对拍）
python3 -m unittest discover -v

# 性能基准（默认 2000 个模板，可指定数量）
python3 bench.py 2000

# 错误报告样例 + 对拍对照表
python3 demo_errors.py
```

## 性能数据

环境：Python 3.12.3，Linux x86-64（容器单核）。2000 个随机模板
（小/中/大混合，共约 1.6 MB，含嵌套段与循环）：

| 指标 | 数值 |
| --- | --- |
| 校验总耗时 | 513.5 ms |
| 单模板均值 / p50 / p95 | 0.256 / 0.117 / 0.717 ms |
| 校验吞吐 | ≈ 3,900 模板/秒 |
| 四语言比较 | 1.38 ms/组（100 组） |
| 渲染（参考） | 0.050 ms/模板 |

结论：发布前对上千个模板做全量校验为亚秒级，可放入 CI。

## 错误报告样例

完整输出见 `examples/error_report.txt`（由 `demo_errors.py` 生成），节选：

```
[zh] MISSING_PLACEHOLDER: placeholder 'since' exists in 'en' (1:59) but is missing here
[zh] 4:4 EXTRA_PLACEHOLDER: placeholder 'level' does not exist in 'en'
[zh] 1:4 TYPE_CONFLICT: placeholder 'count' is 'int' in 'en' but 'str' here
[zh] ORDER_MISMATCH: placeholder order differs from 'en': expected ['name', 'count', 'msgs', 'vip'], got ['count', 'name', 'msgs', 'vip']
[ja] 3:5 OUT_OF_SCOPE: 'm' is a loop variable introduced by an {#each ... as m} section and cannot be referenced here (placeholder)
```

对拍对照（同一参数下校验结论与渲染结果）：

```
template                                             validator  renderer
{x:int} + {y:str}                                    ACCEPT     ok -> '1 + a'
{x:int} {x:str}                                      REJECT     FAIL (RenderError: expected str, got int)
{#each xs as x}{x}{/each}{x}                         REJECT     FAIL (RenderError: missing argument 'x')
{n:int}{#each n as i}{i}{/each}                      REJECT     FAIL (RenderError: expected list, got int)
```

## 覆盖的边界情形

`tests/` 中包含：空模板、纯文本无占位符、同一占位符多次出现（类型合并）、
深层嵌套段（if/each 混合 5 层）、循环变量遮蔽、可选参数缺省/为 `None`、
转义大括号、未闭合/错配闭合标签、`bool` 不被当作 `int` 等。

## 代码结构

```
tplcheck/
  parser.py     # 模板语法 → AST（带位置）
  validator.py  # 单模板校验：作用域、类型合并、签名提取
  compare.py    # 跨语言签名比较
  renderer.py   # 严格参考渲染器（对拍用）
  errors.py     # Diagnostic / TemplateSyntaxError / RenderError
tests/          # 单元测试 + 对拍测试 + 随机模板/参数生成器
bench.py        # 性能基准
demo_errors.py  # 错误报告样例生成
examples/error_report.txt
```
