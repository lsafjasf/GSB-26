# 敏感词多模式匹配引擎（Python 3，纯标准库）

长文本一次扫描匹配成千上万个敏感词。核心是自建 **Aho-Corasick 自动机**
（Trie + 失败指针 + 字典后缀链），不是逐词扫全文，也不拼正则。支持可选
归一化（大小写/全半角/空白/零宽字符）且**保留到原文偏移的位置映射**，
并提供词边界、白名单两级误伤控制，以及与朴素逐词扫描的严格对拍。

## 目录结构

```
sensitive/
  normalize.py     归一化配置、Normalizer、Normalized（位置映射）
  data/            繁简 1:1 / 同音字表（随包静态文件 + build_tables.py）
eval/              标注样本集 + 可复跑误伤评估脚本（见 eval/README.md）
  ahocorasick.py   Aho-Corasick 自动机（Trie/失败指针/字典后缀链）
  engine.py        SensitiveEngine：归一化 + AC + 边界 + 白名单，Hit 结果
  naive.py         朴素逐词 str.find 扫描（参考实现，仅供对拍）
tests/
  test_matcher.py           重叠命中/前缀、归一化映射、边界、白名单
  test_crosscheck.py        随机 fuzz 对拍 + 3000+ 词大规模对拍
  test_false_positives.py   误伤评估与刻意构造的误伤样本
bench/
  benchmark.py     对拍 + 吞吐/内存基准（可复现，固定随机种子）
```

## 快速开始

```python
from sensitive import SensitiveEngine, NormalizeConfig

eng = SensitiveEngine(["法轮", "天安门"], whitelist=["天安门广场"])
for h in eng.find_all("去天安门广场，练习法\u200b轮"):
    print(h.word, h.start, h.end)      # 法轮 8 11（原文区间含零宽字符）
```

## 运行命令

```bash
# 全部自测（43 个：单测 + 60 组随机对拍 + 3000 词大规模对拍 + 评估管线）
python3 -m unittest discover -s tests -v

# 误伤评估：从标注集现场重算各策略 命中/误伤/P/R + 逐条策略差异
python3 eval/run_eval.py --out eval/last_report.txt --json eval/last_results.json

# 重建随包繁简/同音字表（需联网，产物已随库；运行期离线）
python3 sensitive/data/build_tables.py

# 大规模对拍 + 吞吐/内存（默认 10000 词 / 50 万码点，固定种子可复现）
python3 bench/benchmark.py

# 自定义规模
python3 bench/benchmark.py --words 50000 --text 2000000 --runs 3
```

环境：Python 3.10+（开发于 3.12），仅标准库。

## 命中规则（重叠与前缀）

- 返回**全部重叠命中**：每个命中给 `word / start / end`（原文码点偏移，
  0 基、半开区间，`text[start:end]` 即被命中的原文片段，可能包含被归一化
  删除的字符），另附 `norm_start / norm_end` 供调试。
- 排序：`(start, end, word)` 升序。同一起点命中多个词时，**短词在前**：
  `["a","ab","abc"]` 扫 `"xabc"` 得到 a(1,2)、ab(1,3)、abc(1,4)。
- 一词是另一词前缀（`a`/`abc`）、后缀（`yz`/`xyz`，走字典后缀链产出）、
  周期性重叠（`aa` 在 `aaaa` 中命中 3 次）全部保留，不做“最长匹配覆盖”。
  如需“同起点只取最长”，由调用方按 `start` 自行过滤（业务规则差异大，不内置）。
- 归一化后相同的不同原始词形（如 `ABC` 与 `ａｂｃ`）视为同一个词，保留
  第一个插入的原始词形；归一化后为空的词（纯零宽字符）被拒绝，记录在
  `engine.skipped_words`。

## 归一化与位置映射

所有变换在码点层面进行，且**只做一对一替换或删除**（`str.casefold` 的一对多
展开如 `ß→ss` 会跳过、不折叠；繁简只收单字 1:1），因此归一化文本的每个码点
都能对应回原文一个码点。单码点变换按固定顺序串接：零宽删除 → 全半角 →
繁→简 → 同音折叠 → 空白 → 大小写折叠。繁简/同音字表是随包的静态文本
（`sensitive/data/`），运行期完全离线；`build_tables.py` 记录了上游来源与
sha256，可重建复现。

`Normalized` 结构：

- `text`：归一化文本；
- `orig_pos[i]`：归一化文本第 `i` 个码点在原文中的偏移；
- `to_orig_span(s, e)`：命中归一化区间 `[s,e)` 映射为
  `[orig_pos[s], orig_pos[e-1]+1)`，即覆盖首字到尾字的完整原文范围
  （中间被删的零宽字符/空白一并包含，方便高亮/审计）。

可选配置（`NormalizeConfig`，词典与文本强制使用同一配置）：

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| `casefold` | 开 | `str.casefold()` 一对一大小写折叠（一对多展开跳过） |
| `width` | 开 | 全角 ASCII（U+FF01–U+FF5E） → 半角；`U+3000` 全角空格 → 空格 |
| `t2s` | 开 | 繁→简 **严格单字 1:1**（随包 3151 条，源自 OpenCC）；一对多/词级转换不处理 |
| `homophone` | **关** | 同音折叠到代表字（带声调/单音字/GB2312 一级常用字，753 组）；`homophone_table` 可自定义。误伤面大，默认关 |
| `whitespace` | `collapse` | `keep` 保留 / `collapse` 所有 Unicode 空白折叠成一个 ASCII 空格 / `remove` 全部删除 |
| `zero_width` | 开 | 删除 U+200B/200C/200D/2060/FEFF/180E、U+2061–2064；变体选择符 U+FE00–FE0F 不删 |

示例：原文 `"练Ｃ\u200bat"`，归一化后 `"练cat"`，`orig_pos=(0,1,3,4)`；
词 `cat` 的归一化区间 `[1,4)` 映射回原文 `[1,5)`，
`text[1:5] == "Ｃ\u200bat"`。

预设：`DEFAULT_CONFIG`（上表默认值）、`RAW_CONFIG`（全部关闭，原样匹配）。

## 误伤控制

### 1. 词边界 `use_boundary=True`

- 在**归一化文本**上判定：命中区间左侧不是词字符、右侧不是词字符才上报；
  判定字符谓词可自定义（`is_word_char`）。
- 默认谓词只认 ASCII 字母/数字/下划线：因此 `cat` 不会命中 `scatter`、
  `bobcat`、`category`、`cat123`，但 CJK 不作为词字符，「我爱天安门」中的
  「天安门」照常命中。需要 Unicode 词字符可传
  `lambda c: c.isalnum() or c == '_'`（注意会把 CJK 相邻也算粘连）。
- 因为判定基于归一化后的相邻字符，`s\u200bcat` 删零宽后等于 `scat`，
  边界同样生效，口径与匹配一致。

### 2. 白名单 `whitelist=[...]`

- 白名单词走同一套归一化，并构建第二个 AC；
- 某白名单命中区间在归一化文本上**完整覆盖**敏感词区间时放行该命中
  （白名单允许更长，如敏感词「天安」被「天安门」覆盖；短白名单不能放行
  长敏感词）。多个重叠白名单区间都生效。

### 3. 误伤评估：标注集 + 可复跑脚本（各归一化策略对照）

误伤评估已从少量手工样例升级为**可复跑的数据管线**（代码在 `eval/`，
语料 `eval/corpus.py`、档位 `eval/profiles.py`、脚本 `eval/run_eval.py`）：

- 19 条人工标注样本（17 个真值命中），覆盖大小写/全半角/繁体/零宽/
  同音替字/拆字空格规避与正常文本、白名单；真值区间指原文，与策略无关。
- 每条命身份为 `(样本, 词, 原文start, 原文end)`，可跨策略直接做集合差。
- 脚本现场扫描重算每个档位的 reported/TP/FP/FN 与 precision/recall，
  列出**每条误伤/漏报**，并相对基准档 **逐条** 输出 `+新增/-消失` 命中
  （标明 TP/FP）。固定开词边界、统一白名单，差异只来自归一化。

复跑：

```bash
python3 eval/run_eval.py --out eval/last_report.txt --json eval/last_results.json
python3 eval/run_eval.py --diff-from base_default homophone_on ws_remove
```

本次真实输出（数字以脚本重跑为准，完整清单见 `eval/last_report.txt` 与
`eval/README.md`）：

| 档位 | reported | TP | FP(误伤) | FN | precision | recall |
| --- | ---:| ---:| ---:| ---:| ---:| ---:|
| `raw` 全关 | 6 | 6 | 0 | 11 | 100.0% | 35.3% |
| `base_default` 基线 | 16 | 14 | 2 | 3 | 87.5% | 82.4% |
| 关 `casefold` | 12 | 10 | 2 | 7 | 83.3% | 58.8% |
| 关 `width` | 15 | 13 | 2 | 4 | 86.7% | 76.5% |
| 关 `t2s` | 11 | 11 | 0 | 6 | 100.0% | 64.7% |
| 关 `zero_width` | 14 | 12 | 2 | 5 | 85.7% | 70.6% |
| `whitespace=remove` | 12 | 9 | 3 | 8 | 75.0% | 52.9% |
| 开 `homophone` | 20 | 16 | 4 | 1 | 80.0% | 94.1% |
| 全开（同音+remove） | 16 | 11 | 5 | 6 | 68.8% | 64.7% |

代表性误伤/差异（逐条，节选自报告）：

- 繁简：关 `t2s` 少 3 条繁体 TP，同时少 2 条误伤——「代辦證件」里的
  「辦證」被折成「办证」邻接命中（S09/S10），属繁简带来的 FP。
- 同音：开 `homophone` 净 +4 条：抓到「供击→攻击」「供势→攻势」2 条
  对抗 TP，但「办公室」的「公室」与正常词「公事」被折成「攻势」，2 条
  真误伤。带声调、限常用字、多音字剔除仍有此碰撞，故默认关闭。
- 空白 remove：抓到「代　辦　證」拆字，但英文 `a cat sat` 删空格后
  粘连成词，边界判词内，反漏 5 条英文 TP——对英文文本是净负收益。

#### 各策略适用边界与关闭

- `casefold` / `width`：解决大小写、全角规避，碰撞面极小，默认开；
  关闭：`casefold=False` / `width=False`。
- `t2s`：繁简混排建议开，单字 1:1 不猜词；双字词跨字邻接会多报，
  需要时 `t2s=False`。一对多（發/髮、乾）本就不处理。
- `homophone`：仅建议对少量重点词、高对抗渠道临时开启（或用
  `homophone_table` 只折叠目标词涉及的字），全局开启会误伤同音正常词；
  默认 `homophone=False`。
- `zero_width`：通用默认开；代价仅是原文 span 会含不可见字符；
  `zero_width=False` 关闭。
- `whitespace`：一般文本用 `collapse`（默认）；纯 CJK 高对抗可
  `remove`，但英文文本会跨词粘连；`keep` 最保守。
- 词边界与白名单仍是两层基础误伤控制（见上两节），与归一化解耦。

## 实现要点

- 自动机节点用并行数组：`next[dict] / fail / out / dict_link`。用
  **字典后缀链（dict_link）** 枚举输出，而不是把输出复制到整条失败链：
  后者在大量词共享前缀时输出存储退化为二次增长。
- 扫描复杂度 O(文本长度 + 命中数)，含全部重叠；构建 O(全部模式字符数)。
- 输出在每个结束位置按最长模式优先产出（dict_link 走向更短的后缀模式）。

## 吞吐与内存（本机实测）

Python 3.12.3，16 核，单线程纯 Python；时间取 best-of-N，内存为
`tracemalloc` 建库峰值（含词典与 trie）；文本为高植入密度的随机文本，
命中数很高（命中产出是主要开销）。

| 规模（词 × 码点） | trie 节点 | 建库峰值内存 | 建库 | AC 扫描 | 朴素扫描 | 命中数 | 加速比 |
| --- | ---:| ---:| ---:| ---:| ---:| ---:| ---:|
| 10,000 × 500K | 38,053 | 10.4 MB | 0.30 s | 0.23 s（2.20 M 码点/s） | 1.73 s | 24,537 | 7.6× |
| 10,000 × 1M（低密度） | 38,053 | 10.4 MB | 0.31 s | 0.45 s（2.22 M 码点/s） | 3.44 s | 38,168 | 7.6× |
| 50,000 × 2M | 170,701 | 50.0 MB | 1.56 s | 1.58 s（1.27 M 码点/s） | 32.44 s | 278,065 | 20.5× |

三组场景 AC 与朴素的命中集合（词 × 起点 × 终点）**逐一比较完全一致**，
`benchmark.py` 退出码 0。复现：

```bash
python3 bench/benchmark.py
python3 bench/benchmark.py --words 50000 --cjk-words 10000 --text 2000000 --runs 2
python3 bench/benchmark.py --density 0.02 --text 1000000
```

说明：朴素法耗时随词数线性增长（5 万词时慢 20 倍以上），AC 只随文本与命中
数增长；内存随 trie 节点数线性，约 290–300 字节/节点（dict 稀疏转移的
Python 对象开销），17 万节点 50 MB 量级。
