# tablediff — 表级差异对比库（Python 3，仅标准库）

迁移前后核对两张表是否一致。按主键对齐比较，行序不同不影响结果；
主键缺失/重复、表结构不一致都会显式报告，绝不按位置静默匹配。

## 文件

| 文件 | 说明 |
|---|---|
| `tablediff.py` | 库本体（`Table` / `compare` / `DiffReport`） |
| `selftest.py` | 单元测试 + 300 组随机数据集对拍（与独立实现的朴素全量比较逐一核对差异集合） |
| `benchmark.py` | 性能与内存基准：空表 / 单侧列 / 超宽表 / 百万行表 |
| `demo.py` | 生成报告样例 |
| `sample_report.txt` | 报告样例（文本 + JSON） |
| `benchmark_results.txt` | 实测耗时与内存数据 |

## 运行命令

```bash
cd table_diff
python3 selftest.py     # 自测（单元测试 + 随机对拍）
python3 benchmark.py    # 性能与内存基准
python3 demo.py         # 生成 sample_report.txt
```

## 用法

```python
from tablediff import Table, compare

left  = Table.from_dicts("迁移前.users", rows_before)
right = Table.from_dicts("迁移后.users", rows_after)

rep = compare(left, right,
              key="id",                              # 支持复合主键 key=["k1","k2"]
              ignore_columns=["updated_at"],         # 忽略列
              tolerances={"balance": {"atol": 0.01}, # 数值容差（绝对/相对）
                          "*": 0.0},
              max_examples=1000,                     # 行级明细截断，计数始终精确
              max_field_examples=64)                 # 每条修改行的字段明细上限
                                                      # （None=不限），超限置标记

print(rep.to_text())      # 文本报告
rep.to_json()             # JSON 报告（忽略列/容差等规则记录在 rules 段）
rep.summary["equal"]      # 总体结论
```

## 差异模型

- **新增**：主键仅存在于右表；**删除**：主键仅存在于左表；
  **修改**：主键两侧都有但字段值不同（细化到列，含左右值）。
- **计数与占比**：新增/删除/修改行/修改字段均给出计数及相对
  `max(左行数, 右行数)` 的占比；字段级占比相对 `共同行数 × 比较列数`。
- **主键缺失**（任一键列为 None）与**主键重复**：该行不参与对齐比较，
  在报告 `keys` 段计数并给出示例（重复键保留首行参与比较，其余排除并计数）。
- **表结构不一致**：仅左/右表存在的列、同名列类型不一致都列在 `schema` 段；
  只对两侧同名且类型兼容（int/float 互容）的非主键列做值比较；主键列只用于
  对齐、类型不一致列只报结构冲突，二者都不计入 `compared_columns`。
- **明细截断**：`max_examples` 限制每类差异行数，`max_field_examples` 限制
  每条修改行保留的字段明细数（默认 64，`None` 不限）。计数始终精确；任一层
  超限都会在报告置标记（`diff.truncated`，并细分为 `rows_truncated` 与
  `fields_truncated`，被截断的行带 `fields_truncated`），不会被当成全量明细。
- **忽略列与容差**：忽略列完全不参与比较；容差仅对数值生效
  （`abs(a-b) <= max(atol, rtol*max(|a|,|b|))`），规则原样记录在报告 `rules` 段。

## 对拍方法

`selftest.py` 中的 `naive_diff` 是不复用库代码的独立朴素实现。
随机生成 300 组数据集（随机行序、随机增删改、容差边界值、重复/缺失主键、
单侧列、类型扰动、随机忽略列与容差），断言库输出的
新增/删除/修改（含字段级左右值）三个集合与朴素结果**完全相等**，且各类计数一致。

## 性能与内存（实测，Python 3.12，Linux；完整数据见 benchmark_results.txt）

| 场景 | 规模 | 比较耗时 | 进程峰值 RSS |
|---|---|---|---|
| 空表 | 0 行 | <1 ms | 13.4 MB |
| 单侧存在列 | 10 万行 × 2 比较列 | 0.10 s | 69.8 MB |
| 超宽表 | 2 万行 × 999 比较列 | 1.35 s | 326 MB |
| 百万行表 | 100 万行 × 7 比较列 | 1.37 s | 693 MB |

内存口径：`resource.getrusage(RUSAGE_SELF).ru_maxrss`（含数据本身）。
大表场景建议用 `max_examples` / `max_field_examples` 限制明细条数（计数不受影响，
超限时报告会显式置截断标记）。
