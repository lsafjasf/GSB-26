# 可配置词法分析器（Python 3，仅标准库）

## 结构

- `lexer_lib/lexer.py` — 核心库：`Lexer(config)` / `Lexer.from_json(path)`，
  `tokenize(src) -> (tokens, errors)`；`config.mode` 取 `recover`（默认）或
  `strict`。非法输入在 recover 模式下永不抛异常。
- `lexer_lib/reference_lexer.py` — 独立参照实现（位置用行偏移表 + bisect 推导，
  候选用排序选择，解码独立实现），用于对拍。
- `rules.json` — 规则配置样例（类 C 语言：标识符/数字/字符串/运算符/注释/空白），
  `strings.escapes` 显式声明转义表。
- `fuzz_diff.py` — 对拍脚本：随机源码两侧 token（类型 + 原文 + 解码文本 +
  起止行列 + 偏移）与错误（消息 + 起止跨度 + 原文）必须一致。
- `test_lexer.py` — 单元测试（36 个用例）。
- `error_recovery_demo.py` — 功能演示（转义/注释/未闭合/恢复与 strict，真实输出见
  `demo_output.txt`）。
- `bench.py` — 吞吐基准。

## Token 与错误字段

- 每个 token 带 **原文** `text`（源文件切片）与 **解码文本** `decoded` 两个字段；
  STRING 的 `decoded` 为去掉定界符并解码转义后的内容，其余 token `decoded == text`。
- 每个 token 带 1 基起始/结束行列与字符偏移，结束位置为**半开区间**
  （指向末字符之后）。
- 每个错误带**起止两端**位置（同样半开）与错误原文：`LexError.span` 返回
  `((sl,sc),(el,ec))`，另含 `offset/end_offset`。

## 配置与切分语义

- `rules`: `[{name, pattern, skip?, priority?}]`，正则从当前位置锚定匹配；
  `skip: true` 不进入结果（如空白）。
- `keywords` / `keyword_token` / `ident_type`: 文本命中关键字表时把 IDENT 改判。
- 每个位置取**最长匹配**；并列时 `priority` 数值小者优先，再按声明顺序
  （行注释、块注释、字符串、rules 顺序）。

### 字符串与转义（本次迭代）

- `strings.escape` 为转义引导符；`strings.escapes` 为转义映射表，默认覆盖
  `\\ \" \' \n \r \t \0 \b \f \v \a` 以及 `\<真实换行>`（行连接）。
  配置文件可覆盖整张表；未配置 `escapes` 时沿用默认表。
- 引号/反斜杠/换行转义按表解码（如 `"a\nb\\c\"d"` → `a<LF>b\c"d`）。
- **非法转义**：引导符后字符不在表中 → `invalid escape` 错误，跨度为
  `[引导符, 被引导字符之后)`（即两个字符；被引导字符本身是换行/EOF 时退化为一个）；
  recover 模式下该字符按原样保留进 `decoded`，继续扫描。EOF 处孤立反斜杠同样报
  `invalid escape`。
- 反斜杠 + 真实换行作为行连接被消费，字符串跨物理行而不被判未闭合。
- **未闭合字符串**（非 multiline 遇真实换行，或到 EOF）：仍产出 STRING token
  （换行不计入），错误为 `unterminated string`，跨度从开引号到换行/EOF（半开），
  起点为开引号位置。

### 注释（本次迭代）

- 行注释：`comments.line` 前缀列表，到行尾（不含换行）为止，EOF 结尾也合法。
- 块注释：`{start, end, nested}`，`nested: true` 支持嵌套（深度 5 万也线性处理）。
- **未闭合块注释**：COMMENT token 到 EOF，错误 `unterminated comment` 跨度从
  起始标记到 EOF（半开）。

### 错误恢复与 strict 模式

- 默认 `recover`：非法字符 / 非法转义 / 未闭合都记录带跨度的错误，然后继续分词。
  非法字符跳过该字符；非法转义仅报错、不中断字符串；未闭合后从换行后/EOF 恢复。
- `strict`（`config.mode = "strict"`）：首个词法错误抛 `LexicalError`，其
  `.err` 与 recover 模式的 `LexError` 字段完全一致。

## 运行命令

```bash
python3 -m unittest -v                  # 36 个单元测试
python3 fuzz_diff.py 20000 42           # 对拍（迭代次数 随机种子）
python3 error_recovery_demo.py          # 功能演示（输出另存 demo_output.txt）
python3 bench.py 5                      # 吞吐基准（目标 MiB）
```

## 对拍结果

3 个种子 × 20000 轮随机片段（非 ASCII 标识符、合法/非法转义、行连接、
未闭合字符串、嵌套/截断块注释、非法字符），两侧 token 序列与错误列表
（含解码文本与起止跨度）完全一致：

```
OK: 20000 iterations, no divergence (seed=42)
OK: 20000 iterations, no divergence (seed=7)
OK: 20000 iterations, no divergence (seed=20260927)
```

## 位置核对示例（真实输出摘自 error_recovery_demo.py）

```
bad   = "x\q@"          -> STRING raw='"x\\q@"' decoded='xq@'
                           error invalid escape 2:11..2:13 raw='\\q'
x = "abc                -> error unterminated string 1:5..1:9 raw='"abc'
z = /* never closed\nend-> error unterminated comment 3:5..4:4
strict '"x\\q"'          -> LexicalError: invalid escape at 1:3..1:5
strict 'a @ b'           -> LexicalError: illegal character at 1:3..1:4
```

完整逐条输出见 `demo_output.txt`（由演示脚本 tee 生成，可复跑比对）。

## 吞吐数据（CPython 3.12，本机单次测量）

| 输入 | 大小 | tokens | 吞吐 |
|---|---|---|---|
| 混合真实负载（`bench.py 5`） | 5.18 MiB | 926,122 | 1.73 MiB/s ≈ 31 万 token/s |

新增逐字符解码与双文本字段后较上一版（1.89 MiB/s）约有 8% 回退；
无错误路径的行偏移表按需惰性构建，避免对干净输入的额外全扫开销。
