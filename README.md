# numfmt —— 数值本地化格式化库（Python 3，仅标准库）

按语言习惯显示数值（千分位、小数分隔符、负数记号、货币位置），并在显示环节
精确完成舍入：全部计算基于 `fractions.Fraction` 精确有理数，半值判定不使用浮点。

## 交付文件

| 文件 | 说明 |
| --- | --- |
| `numfmt.py` | 库源码：精确舍入、本地化排版、科学计数法自动切换 |
| `locales.json` | 语言/格式配置样例：en_US、de_DE、fr_FR、zh_CN、hi_IN、会计风格等 |
| `selftest.py` | 单元自测（28 例）：零/负零、极值、超长小数、缺项配置、切换边界 |
| `diff_test.py` | 对拍脚本：numfmt(Fraction) vs 独立参照(Decimal 解析+舍入)，随机用例 |
| `coverage_stats.py` | 覆盖统计：对拍实际覆盖的精度/舍入/分组宽度/语言配置数字 |
| `bench.py` | 性能测试：格式化十万次耗时 |

## 快速开始

```python
from numfmt import format_number, FormatSpec, load_locales

format_number("1234567.895")                    # '1,234,567.90'（四舍五入，2 位小数）
format_number("0.125", FormatSpec(precision=2, rounding="half_even"))  # '0.12'（半值取偶）
format_number(2.675, FormatSpec(precision=2))   # '2.67'（float 二进制精确值）
format_number("2.675", FormatSpec(precision=2)) # '2.68'（十进制字符串精确值）

spec = FormatSpec(precision_mode="significant", precision=6, sci_high=6, sci_low=-4)
locales = load_locales("locales.json")
format_number("123456.78", spec, {"decimal_sep": ",", "group_sep": "."})  # '123.457'
format_number("-1234.5", locale=locales["en_US"])       # '-$1,234.50'
format_number("1234.5", locale=locales["fr_FR"])        # '1 234,50 €'（U+202F 窄空格）
format_number("12345678.9", {"precision": 1}, locales["hi_IN"])  # '₹1,23,45,678.9'
```

`FormatSpec` / `LocaleConfig` 均可只传部分字段（缺项走默认值），也可直接传 `dict`。

## 配置项

**LocaleConfig（语言习惯）**

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `decimal_sep` | `.` | 小数分隔符 |
| `group_sep` | `,` | 分组分隔符（空串表示不分组） |
| `group_width` | `(3,)` | 从右往左各组宽度，末项循环；`(3,2)` 为印度式 |
| `negative_sign` | `-` | 负号样式 |
| `positive_sign` | `""` | 设 `"+"` 则正数显式带号 |
| `negative_parens` | `False` | 会计风格括号负数 `(1,234.56)` |
| `currency_symbol` | `""` | 货币符号 |
| `currency_position` | `"prefix"` | `prefix` / `suffix` |
| `currency_space` | `False` | 货币与数字间留空 |

**FormatSpec（数值规则）**

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `precision_mode` | `"decimal_places"` | 按小数位或按 `"significant"` 有效数字 |
| `precision` | `2` | 小数位数 / 有效数字位数 |
| `rounding` | `"half_up"` | 四舍五入；`"half_even"` 银行家舍入 |
| `sci_high` | `None` | 十进制指数 ≥ 该值切科学计数法 |
| `sci_low` | `None` | 十进制指数 ≤ 该值切科学计数法 |
| `group` | `True` | 是否分组 |
| `keep_negative_zero` | `True` | 负零（含舍入到零的负数）是否保留负号 |

## 舍入精确性

输入按其精确语义转换：`str`/`Decimal` 按十进制数位，`float` 按其二进制精确值，
`Fraction`/`int` 原样。舍入把数值缩放到目标量级后用整数 `divmod` 取余，
半值用 `2*r == 分母` 判定，没有浮点误差，也不受超长小数影响。

经典对比：`2.675` 作为 float 是 2.674999999999999822...，精确舍入到两位是 `2.67`；
作为十进制字符串 "2.675" 恰好半值，结果是 `2.68`（HALF_UP）。库不会把二者混为一谈。

## 科学计数法切换边界数据

规格：4 位有效数字，`sci_high=4`，`sci_low=-3`，HALF_UP。
切换判断在**舍入之后**进行（如 `0.00099999` 舍入进位到 1e-3 后记法随之改变），
切换前后解析回的数值完全相同。

| 输入 | 输出 | 舍入后精确值 | 记法 |
| --- | --- | --- | --- |
| `9999.4` | `9,999` | 9999 | 定点 |
| `9999.5` | `1.000e+4` | 10000 | 半值进位，指数跨过 4 → 科学 |
| `9999.4999` | `9,999` | 9999 | 定点 |
| `9999.5001` | `1.000e+4` | 10000 | 科学 |
| `0.01` | `0.01000` | 1/100 | 指数 -2，定点 |
| `0.001` | `1.000e-3` | 1/1000 | 指数 -3，科学 |
| `0.00099994` | `9.999e-4` | 9999/10⁷ | 指数 -4，科学 |
| `0.00099999` | `1.000e-3` | 1/1000 | 舍入进位后指数变为 -3 |
| `9998.5` (HALF_EVEN) | `9,998` | 9998 | 半值取偶，不进位 |
| `9999.5` (HALF_EVEN) | `1.000e+4` | 10000 | 半值取偶，9999 奇 → 进位 |

## 对拍

参照实现 `diff_test.py` 不导入 numfmt 的任何内部函数，解析与舍入均走独立的
Decimal 路线：字符串由 decimal 模块自己的解析器处理（指数写法、负零符号独立
判定），float 按二进制精确值展开为十进制，Fraction 在 800 位精度下相除；舍入用
高精度 `quantize`，每个参照结果再用 Fraction 精确验证“最近邻 + 半值规则”
（校验基准由参照自己的解析结果推出，不经过 numfmt 的解析代码）。随机值覆盖
十进制字符串（含超长小数/指数/负零）、float（含 5e-324 次正规数、1.79e308
极值）、大整数、Fraction、精确半值、Decimal；配置覆盖科学计数法阈值及随机
货币/括号/正号组合，其余维度数字由 `python3 coverage_stats.py` 统计产出
（与代码同源，非手写）：

```
精度模式: 2 种 (decimal_places, significant)
精度取值: decimal_places 0–25 (26 个), significant 1–25 (25 个)
舍入规则: 2 种 (half_up, half_even)
分组宽度: 5 种 ((3,), (3, 2), (2,), (4,), (3, 2, 2))
语言配置: 7 套 (en_US, de_DE, fr_FR, zh_CN, hi_IN, en_US_accounting, de_DE_signed)
```

默认 20,000 例，已验证 100,000 例一致。

## 性能数据

环境：Python 3.12.3 / WSL2 (Linux 6.18, x86_64)，100,000 次混合类型输入，
每个配置跑 3 轮取最优：

| 配置 | 总耗时 | 单次 | 吞吐 |
| --- | --- | --- | --- |
| 默认（2 位小数，en_US 分组） | 298.5 ms | 2.99 µs | 335 K 次/s |
| 6 位有效数字 + 科学阈值（de_DE） | 335.2 ms | 3.35 µs | 298 K 次/s |
| 印度式分组 + 货币（hi_IN） | 318.8 ms | 3.19 µs | 314 K 次/s |

## 运行命令

```bash
python3 selftest.py            # 28 个单元自测
python3 diff_test.py           # 20,000 例对拍
python3 diff_test.py 100000    # 100,000 例对拍
python3 coverage_stats.py      # 统计对拍覆盖数字（上文表格来源）
python3 bench.py               # 十万次性能测试
```
