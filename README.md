# 可配置词法分析器（Python 3，仅标准库）

词法规则由 JSON 配置声明，按**最长匹配 + 规则优先级**切分；每个 token 携带
起止行列与原始文本；错误（非法字符、未闭合字符串/注释）只报告不中断，
最终同时返回 token 列表与错误列表。

## 文件

- `lexer.py` — 主实现（正则引擎），库 + CLI
- `reference_lexer.py` — 独立参照实现（手写扫描器，不用 `re`），用于对拍
- `rules.json` — 规则配置样例（类 C 语言）
- `fuzz_diff.py` — 对拍脚本：随机源码两侧 token 序列（类型+文本+起止位置）与错误列表必须一致
- `error_recovery_demo.py` — 错误恢复样例（配合 `examples/error_sample.src`）
- `bench.py` — 吞吐基准 + 边界情形覆盖

## 规则配置（rules.json）

规则按数组顺序排列，**越靠前优先级越高**（等长匹配时生效）。支持的 `kind`：

| kind | 字段 | 说明 |
|---|---|---|
| `whitespace` | `chars`, `skip` | 空白字符集合 |
| `line_comment` | `prefix`, `skip` | 行注释 |
| `block_comment` | `start`, `end`, `nested`, `skip` | 块注释，`nested:true` 支持任意深度嵌套 |
| `string` | `quote`, `escape`, `multiline` | 字符串；`escape` 转义下一字符 |
| `number` | `allow_hex/float/exponent/underscore` | 数字（`1_000`、`0xFF`、`3.14e-2`） |
| `keywords` | `words` | 关键字（字面量，靠优先级胜过 IDENT） |
| `identifier` | `start`, `continue` | 字符类：`alpha`（Unicode 字母）/`digit`/`underscore`，支持非 ASCII 标识符 |
| `operators` | `ops` | 运算符字面量列表 |

## 语义约定

- 行列均为 1 起始；token 结束位置为**开区间**（指向最后一个字符之后）。
- 每个位置取所有规则中的最长匹配；等长时先声明的规则胜出。
- 未闭合字符串/注释：记录错误（位置为起始处），仍产出对应 token，继续分词。
- 非法字符：记录错误（精确到行列），跳过该字符，继续分词，绝不整体失败。

## 运行命令

```bash
python3 lexer.py rules.json examples/error_sample.src   # CLI 分词
python3 error_recovery_demo.py                          # 错误恢复样例
python3 fuzz_diff.py --iterations 3000 --seed 20260926  # 双实现对拍
python3 bench.py                                        # 吞吐 + 边界情形
```

## 对拍结果

```
OK: 13 edge cases + 3000 random sources, token streams and errors identical.
```

两侧对字符分类的口径一致以配置为准：`digit` 仅指 ASCII `0-9`，
非 ASCII 数字（如 `٣`、`４`、`²`）既不构成 NUMBER 的一部分，
也不是标识符的 continue 字符（`²` 这类非十进制数字仍属 `alpha`，
可出现在标识符中），未覆盖的字符按非法字符报错后继续。

## 吞吐数据（bench.py，CPython 3.12，本机实测）

| 负载 | 大小 | tokens | 耗时 | 吞吐 |
|---|---|---|---|---|
| 典型代码 | 1.07 MB | 224,000 | 363 ms | 2.9 MB/s（0.62 Mtok/s） |
| 对抗混合（含大量非法字符/未闭合串） | 2.27 MB | 154,752 | 280 ms | 8.1 MB/s |
| 空输入 | 0 | 0 | <0.1 ms | — |
| 纯空白 | 2.10 MB | 0 | 1.6 ms | 1332 MB/s |
| 超长标识符（单个 2MB） | 2.00 MB | 1 | 3.1 ms | 637 MB/s |
| 深层嵌套注释（10,000 层） | 40 KB | 1 | 1.3 ms | 30 MB/s |
| 非 ASCII 标识符（36 万个） | 1.44 MB | 360,000 | 619 ms | 2.3 MB/s（0.58 Mtok/s） |

边界情形（空输入、只有空白、超长标识符、深层嵌套注释、非 ASCII 标识符）
均通过正确性抽查，且包含在对拍脚本的固定 edge cases 中。
