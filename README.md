# 区域化排序库（localesort）

按语言习惯排序通讯录/目录：汉字按拼音、拉丁文按字母，变音符号与大小写分级比较，
数字串按数值比较。仅使用 Python 3 标准库。

## 文件

- `localesort.py` — 排序库（`LocaleSorter`：`key` / `compare` / `sort` /
  `uncovered_report` / `coverage_check` / `explain`；`validate_config` 配置校验）
- `sort_rules.json` — 排序规则配置（由 `make_config.py` 生成）
- `make_config.py` — 规则数据表（字母、拼音、多音字、符号位次），生成配置
- `test_localesort.py` — 自测（全序断言、稳定性、对拍、边界情形、配置校验、
  完整性检查、未覆盖策略）
- `verify_rules.py` — 可复跑验证：配置校验 + 完整性检查 + 三策略排序对比，
  生成 `coverage_report.txt`
- `benchmark.py` — 十万条记录性能测试，输出 `uncovered_report.txt`
- `uncovered_report.txt` — 未覆盖字符报告（benchmark 生成）
- `coverage_report.txt` — 规则完整性检查报告（verify_rules 生成）

## 排序规则（配置声明）

- **主级**：符号（显式位次）< 数字串（按数值）< 字母/拼音（字典序）< 未知字符（码位）
- **次级**：变音符号等级（无<尖<抑<弯<分<鼻<软）/ 汉语声调（1–4）
- **三级**：大小写（默认小写在前，可配 `case_first`）
- **开关**：`numeric`（数字串按数值）、`unknown_position`（未知字符回退 start/end）
- **多音字**：`cjk` 条目可声明 `alt` 读音，排序用主读音，`sorter.polyphones` 可查全部读音
- **未覆盖字符**：回退到末尾、彼此按码位排序；`uncovered_report()` 统计出现次数
- **未覆盖策略**：`uncovered_policy` 显式选择未覆盖字符的处理方式——
  `codepoint`（默认，回退码位）、`error`（抛 `UncoveredCharError`）、
  `fold`（NFKD 去变音 + casefold 折叠进已覆盖等价类，类内排在原字符之后；
  无法折叠的仍回退码位）

## 配置校验与完整性检查

- `validate_config(config)` 返回问题列表（空 = 通过）：表间字符冲突、符号位次
  重复、枚举值非法、条目结构错误等；`LocaleSorter` 构造时对非法配置抛
  `ConfigError`
- `sorter.coverage_check()` 扫描配置声明的映射表与全部已分配 Unicode 码位，
  按字符类别（Lu/Ll/Lo/Nd/So/…）报告未被任何规则覆盖的类别、数量与样例；
  结果含 sha256 `digest`，同一配置 + 同一 Unicode 数据版本下可复算验证
- `sorter.explain(text)` 逐字符解释排序键来源（命中哪张表 / 走了哪条未覆盖
  策略），策略切换后的排序结果可解释
- `sorter.coverage_check()` 为纯函数，不修改排序器状态

## 运行命令

```bash
python3 make_config.py        # 重新生成 sort_rules.json
python3 -m unittest test_localesort -v   # 运行全部自测
python3 verify_rules.py       # 配置校验 + 完整性检查 + 三策略排序对比
python3 benchmark.py          # 十万条记录计时 + 生成未覆盖字符报告
```

## 测试覆盖

- 全序断言：随机 400 串样本上验证自反、反对称（4000 对）、传递（2 万三元组）、
  完全性，以及键排序与比较器排序一致、打乱后排序结果唯一
- 稳定性：相同排序键（如 `a01`/`a1`、重复汉字）保持原始相对顺序
- 对拍：字符集排序结果与配置声明的 `expected_order` 逐项一致；
  另与测试内独立实现的键函数对拍
- 边界：空集合、单元素、空串、混合语言、含数字与符号的串、未知字符回退

## 性能数据（benchmark.py，100,000 条记录）

环境：Python 3.12.3，Linux x86-64，单线程 `sorted(key=...)`。

| 运行 | 耗时 |
|------|------|
| 1 | 0.524 s |
| 2 | 0.513 s |
| 3 | 0.464 s |

约 4.4–5.2 µs/条（含键生成）；排序结果已校验有序。
