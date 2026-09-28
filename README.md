# 区域化排序库（locale-sort）

纯 Python 3 标准库实现的配置驱动区域化排序库：按语言习惯（拼音 / 读音、
变音符号、大小写）排序，而不是按码位排序。

## 文件

- `collator.py` — 排序库（`Collator` 类：`sort_key` / `compare` / `sort` / `uncovered_chars`）
- `rules.json` — 排序规则配置（由 `gen_rules.py` 生成，200 个字符映射）
- `gen_rules.py` — 规则配置生成脚本
- `test_collator.py` — 自测（19 个用例，含全序断言与规则表对拍）
- `repro_superscript.py` — 上标数字复现（数字比较遇 ²/³ 不再中断排序）
- `benchmark.py` — 十万条记录性能基准
- `report_uncovered.py` — 未覆盖字符报告工具
- `sample_corpus.txt` — 报告工具用示例语料
- `uncovered_report.txt` — 生成的未覆盖字符报告

## 排序规则（rules.json）

- `levels`: `["primary", "secondary", "tertiary"]` — 多级比较：
  - 主级：字母 / 读音。拉丁字符按基础字母，汉字按拼音（无声调），数字按数值；
  - 次级：变音符号 / 声调（0=无，1..n 按声明顺序）；
  - 三级：大小写（0=小写，1=大写）。
- `numeric_collation`: `true` — 数字串按数值比较（`a2 < a10`）；关掉则按字符。
- `fallback`: `"end"` — 未覆盖字符按码位排在末尾（顺序确定）；可配 `"start"`。
- `mappings`: 字符 `[主级, 次级, 三级]` 权重表。
- `expected_order_groups`: 声明的期望顺序，测试逐项对拍。

多音字：配置只声明一个默认读音（如「长」取 cháng、「乐」取 lè，见
`gen_rules.py` 注释）；需要其他读音时提供另一份规则配置即可。

## 运行命令

```bash
python3 gen_rules.py           # 重新生成 rules.json
python3 test_collator.py       # 自测（功能 + 全序断言 + 规则表对拍）
python3 benchmark.py           # 十万条记录性能基准
python3 report_uncovered.py sample_corpus.txt   # 生成 uncovered_report.txt
```

## 用法示例

```python
import json
from collator import Collator

collator = Collator(json.load(open("rules.json", encoding="utf-8")))
collator.sort(["张", "李", "安", "王"])        # ['安', '李', '王', '张']
collator.sort(["a10", "a2", "a1"])             # ['a1', 'a2', 'a10']
collator.sort(["à", "A", "á", "a"])            # ['a', 'A', 'á', 'à']
collator.sort(records, key=lambda r: r["name"])  # 稳定排序：同键保持原顺序
```

## 正确性保障

- **稳定性**：基于 Python 稳定排序，相同排序键的项保持原始相对顺序
  （`test_stability` 用同键记录验证）。
- **全序断言**（`TestTotalOrder`）：60 个随机字符串（含未覆盖字符、数字、
  符号、大小写、变音）上验证自反性、反对称性（全部 3600 对）、传递性
  （30 万随机三元组）、可比性，并断言键排序与比较器排序结果一致。
- **规则表对拍**：`expected_order_groups` 中每个分组打乱后排序，结果必须
  与配置声明的顺序完全一致；另对全部 200 个映射字符做两两一致性检查。
- **边界情形**：空集合、单元素、空串、混合语言、数字与符号、超大数字、
  上标/下标数字（如 ²³，`isdigit()` 为真但非 `\d` 十进制数字，统一按
  `str.isdecimal()` 判定，转换失败降级为字符比较）、未知字符回退
  （end/start 两种策略）。

## 未覆盖字符

未覆盖字符按码位排到末尾（可配为开头），顺序确定、可比较。
`python3 report_uncovered.py <语料文件...>` 生成 `uncovered_report.txt`，
列出每个未覆盖字符、码位与出现次数（示例语料检出 19 个，如 鲸/蓝/語/🚀）。

## 性能数据

环境：Python 3.12.3，Linux x86-64（本仓库容器）。10 万条混合记录
（中文名 / 拉丁串 / 字母+数字），三次运行：

| 指标 | 数值 |
|---|---|
| 排序总耗时 | 0.48–0.52 s |
| 吞吐 | 约 19–20 万条/秒 |

排序键生成约 0.27 s（含在总耗时内）；排序后做了全量相邻对有序性校验。
