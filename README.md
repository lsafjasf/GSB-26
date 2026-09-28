# sensid：敏感数据发现库（Python 3，仅标准库）

从文本中识别身份证号、银行卡号、手机号、护照号等敏感数据，输出命中位置、类型、
置信度与判定理由；被校验环节否定的候选也会记录否定原因，便于审计与调参。

## 运行命令

```bash
# 单元测试（边界情形：空文本/超大文本/换行分隔符/异常字符/冲突消解）
python3 -m unittest discover -s tests -v

# 构造数据集 + 混淆矩阵 + 阈值扫描（输出到 results/）
python3 evaluate.py

# 吞吐与边界情形基准（输出到 results/benchmark.md）
python3 bench.py

# 扫描任意文本文件（JSON 输出，--show-rejected 显示被否定候选）
python3 -m sensid scan 某文件.txt --threshold 0.5 --show-rejected
cat 某文件.txt | python3 -m sensid scan

# 逐条列出规范化（剔除零宽/格式控制字符）前后的命中集合差异
python3 -m sensid scan 某文件.txt --diff-normalization

# 一键全跑
bash run_all.sh
```

库用法：

```python
from sensid import Scanner
result = Scanner(threshold=0.5).scan(text)
for m in result.matches:      # 命中：类型/位置/置信度/理由
    print(m.type, m.start, m.end, m.score, m.reasons)
for r in result.rejected:     # 被否定候选及原因
    print(r.type, r.raw, r.reason)
for c in result.conflicts:    # 重叠冲突记录
    print(c.kept_type, "压制", c.dropped_type)

# 规范化前后命中集合差异（recovered=规范化找回的命中，lost=规范化后消失的命中）
from sensid import normalization_diff
diff = normalization_diff(text)
```

## 扫描前规范化（零宽/格式控制字符）

扫描前先剔除 Unicode Cf 类字符（零宽空格 U+200B、ZWNJ/ZWJ、双向格式控制、
Word Joiner、软连字符、BOM/ZWNBSP 等），再执行候选生成与校验，避免肉眼相同、
字面被不可见字符拆开的号码漏报。规范化时同步维护"规范化下标 -> 原文下标"
映射，命中、否定与冲突记录中的 start/end/raw 均换算回原文偏移，
`text[m.start:m.end] == m.raw` 恒成立，报告位置可直接在原文中使用。
`Scanner(normalize=False)` 可关闭该行为；`normalization_diff(text)` 或
CLI 的 `--diff-normalization` 逐条列出两种模式下的命中集合差异。

## 判定依据（正则初筛 + 校验的组合判定）

置信度由各项得分累加（上限 1.0），默认阈值 0.5；任一硬性校验失败即否定并记录原因。

| 类型 | 初筛 | 校验项（得分） | 否定条件 |
|---|---|---|---|
| id_card（18位身份证） | `\d{17}[\dXx]` | 格式(+0.10)、ISO7064-MOD11-2 校验位(+0.30)、省级地区码(+0.10)、出生日期合法 | 地区码未知 / 日期非法 / 校验位不匹配 |
| id_card（15位老版） | `\d{15}` | 格式(+0.10)、出生日期合法(+0.10)；无校验位，置信度上限 0.6 | 日期非法 |
| bank_card（银行卡） | 13-19 位数字（允许空格/连字符/换行分隔） | 长度(+0.10)、Luhn 校验(+0.30)、已知 BIN 前缀(+0.10：62银联/4 Visa/51-55 MC/34,37 Amex/35 JCB/30,36,38 Diners) | 长度越界 / Luhn 失败 |
| phone（手机号） | `1[3-9]` + 9 位（允许分隔符） | 格式(+0.10)、号段在运营商已分配号段表内(+0.30) | 号段未分配 |
| passport（护照） | `[EGPSD]\d{8}` | 前缀+格式(+0.15)；无公开校验位，基础分 0.45，必须靠上下文加分才过阈值 | 格式不合法 / 无上下文时低于阈值 |

上下文调整（命中位置前后各 24 字符）：
- 含「身份证/卡号/手机/护照…」等指示词：+0.10
- 含「订单/编号/流水/数量/金额…」等非敏感编号指示词：-0.15

边界保护：候选前后若是数字、ASCII 字母，或"分隔符+数字"（即属于更长数字串的
一部分）直接弃用，避免从订单号、时间戳中切出"手机号"，也避免把以 X 结尾的
18 位身份证的前 17 位数字误当银行卡号。

## 规则冲突消解

同一串数字可能同时满足多类模式（如 62 开头的 18 位串既是合法身份证又通过 Luhn）。
消解规则是确定性的，依次比较：

1. 置信度高者优先；
2. 跨度更长者优先；
3. 类型优先级：`id_card > bank_card > phone > passport`；
4. 起始位置靠前者优先。

被丢弃的重叠候选记录到 `ScanResult.conflicts`（含双方类型、位置、得分与规则说明）。

## 评估结果（构造数据集，seed=42，1790 个埋入实体）

混淆矩阵与阈值扫描见 `results/evaluation.md` 与 `results/confusion_matrix.csv`。
默认阈值 0.5 下总体：精确率 0.943 / 召回率 0.953 / 误报率 0.054 / 漏报率 0.047。
阈值扫描显示：升到 0.9 可将误报率压到 0（精确率 1.0），代价是漏报率升到 0.31；
降到 0.4 召回率 0.99，误报率基本持平。主要残余误报来自碰巧通过 Luhn 的随机
数字串（约 10% 的随机 16 位串天然通过 Luhn，属真实歧义）；护照类漏报来自
无上下文的裸号码（设计上要求上下文佐证以压制产品编号误报）。

## 吞吐

见 `results/benchmark.md`：单线程约 8-9 MB/s（含校验、上下文分析与冲突消解），
空文本、纯空白、emoji/零宽字符/孤立代理项/全角数字等异常输入均无异常返回。

## 已知边界

- 全角数字不属于分隔符，不会归一化命中（不会崩溃）。
- 号码允许的分隔符：空格、制表符、换行、连字符。
- 手机号段表、BIN 表为内置快照，可按需扩充（`sensid/detectors.py`）。
