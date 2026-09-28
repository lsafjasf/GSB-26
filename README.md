# GSB-26：带错误恢复的迷你语言解析器

纯 Python 3 标准库实现。在原有「遇第一个语法错误即整体失败」的严格模式之外，
新增**容错模式**：跳过或补齐到同步点后继续解析，返回部分语法树与全部错误列表。

## 语言语法

```ebnf
program   := stmt*
stmt      := "let" IDENT "=" expr ";" | "print" expr ";"
           | "if" "(" expr ")" stmt ("else" stmt)?
           | "while" "(" expr ")" stmt | "{" stmt* "}" | expr ";"
expr      := 比较/加减/乘除/一元 (- !) / NUMBER / STRING / IDENT / "(" expr ")"
```

## API

```python
from miniparser import parse, parse_strict, ParseError

result = parse(source)          # 容错模式（默认），返回 ParseResult
result.tree                     # 部分语法树（Node，dataclass）
result.errors                   # 全部错误，按位置排序（ErrorInfo）
result.ok                       # 无任何错误时为 True
result.incomplete               # 存在未处理/被恢复内容时为 True（不假成功）
result.uncertain_regions()      # 所有被标记为恢复产物的子树

tree = parse_strict(source)     # 严格模式：遇第一个错误抛 ParseError（旧行为）
```

每个错误包含：`pos`（offset/line/col）、`expected`（期望内容）、
`actual`（实际内容）、`phase`（`lex`/`parse`）。解析是纯函数式的，
同一输入多次执行结果完全一致（有测试保证）。

## 恢复策略

两级恢复，同步点为 `;`、`}`、EOF：

1. **短语级恢复（补齐）**：语句末尾缺 `;`，且当前 token 是 `}`、EOF 或下一条
   语句的起始 token 时，视为补上一个虚拟分号——只记录错误、不消耗 token，
   语句完整保留，实现「失败后立即恢复」。
2. **恐慌模式恢复（跳过）**：其余语法错误记录后，丢弃 token 直到同步点
   （`;` 消耗掉；`}` 与 EOF 留给外层结构，保证嵌套恢复不吞掉外层定界符），
   产生 `kind="error"` 的占位节点，`props["skipped"]` 记录被跳过的内容。
   若未跳过任何 token 且不在同步点上，强制消耗一个 token，保证必然推进。

**不确定区域标记**：`kind="error"` 的占位节点，以及构建过程中发生过恢复的
语句节点，`recovered` 均为 `True`；调用方用 `uncertain_regions()` 判断可用范围。

**不假成功**：只要存在任何错误（词法或语法），`incomplete=True`、`ok=False`，
与完整解析严格区分。

## 部分结果与错误列表样例

对 `examples/demo.py` 中的源文件（3 处错误：缺表达式、缺分号、表达式残缺）：

```
=== 错误列表（按位置排序） ===
  2:12: [parse] 期望 表达式，实际 ';'
  5:3: [parse] 期望 ';'，实际 'print'
  8:17: [parse] 期望 表达式，实际 ';'

ok=False  incomplete=True
不确定区域数量: 5

=== 部分语法树（节选） ===
program @1:1
  let @1:1 {'name': 'total'}          # 完好的语句原样保留
  error @2:12        <-- 恢复产物（不确定区域）
  if @3:1            <-- 内部发生过恢复，标记不确定
    block @3:17      <-- 不确定
      let @4:3 {'name': 'bonus'}  <-- 补分号恢复，语句本身完整
      print @5:3                  # 恢复点之后的兄弟语句正常解析
  print @7:1
  error @8:17        <-- 恢复产物（不确定区域）
```

## 运行命令

```bash
python3 -m unittest discover -s tests -v   # 全部自测（22 个用例）
python3 examples/demo.py                   # 部分结果 + 错误列表样例
```

## 测试覆盖（tests/test_recovery.py）

- **单处错误**：`let y = ;` 前后语句完好，错误含位置/期望/实际。
- **多处独立错误**：3 处错误全部收集、按位置排序，互不影响。
- **错误嵌套**：`if`/`while` 的块内出错，内层恢复不吞 `}`，外层结构与后续语句完整。
- **失败后立即恢复**：缺 `;` 时补虚拟分号，不跳 token，语句完整保留。
- **不假成功**：未闭合块、孤立 `}`、非法字符均使 `incomplete=True`。
- **分隔符不重复报错**：逗号在词法表中有独立 `COMMA` 规则，同一位置只产出
  一条分类明确的 `parse` 错误，不再先报 lex 非法字符、再报 parse 第二条。
- **确定性**：同一输入重复解析 5 次结果逐字段相等。
- **对拍测试**：10 个手写 + 200 个固定种子随机生成的合法程序，
  容错模式语法树与严格模式 `parse_strict` 完全一致，且无误报错误。
