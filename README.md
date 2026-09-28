# 随机会话标识与盐生成修复

本项目仅使用 Python 3 标准库，修复同秒进程冲突、输出有规律、并发重复和长度不足四类问题。

## 文件说明

- `secure_ids.py`：修复后的生产实现。
- `legacy_ids.py`：有缺陷的旧实现，仅用于稳定复现问题，不应在业务代码中导入。
- `tests/test_identifiers.py`：缺陷复现与修复后的回归测试。
- `tools/collect_statistics.py`：大样本统计脚本。
- `statistics_1m.json`：本机生成 1,000,000 个标识的统计证据。

## 使用方式

```python
from secure_ids import generate_session_id, generate_salt

session_id = generate_session_id()
salt = generate_salt()
```

默认输出均为 32 个 URL-safe Base64 字符，字符集为：

```text
ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_
```

32 个字符承载 192 位熵；如需更长，可传入大于或等于 32 的整数长度。长度小于 32 会抛出 `ValueError`。

## 随机来源与种子管理

`secure_ids.py` 每次调用都通过 `os.urandom()` 向操作系统 CSPRNG 请求新的随机字节，不使用 wall clock、PID、计数器或 Python 的 `random.Random` 作为随机源，也不在用户空间保存或复用种子。

- **进程重启**：重启不会恢复用户空间计数器或 PRNG 状态；下一次调用重新从操作系统 CSPRNG 获取随机字节。
- **容器复制**：普通容器共享宿主机内核，复制容器文件系统不会复制 `os.urandom()` 的后续输出；前提是宿主机操作系统 CSPRNG 正常且有足够熵。
- **时钟回拨**：标识不包含时间戳，也不依赖单调时钟，因此时钟回拨不会造成重复、顺序倒推或种子复用。
- **虚拟机快照恢复**：应用层不持久化随机状态；仍依赖平台的 CSPRNG 重播种保证，生产环境应确保虚拟机监控程序提供 `virtio-rng` 等正常熵源。

随机标识在数学上是极低概率碰撞，而不是可由代码“保证永不碰撞”。192 位空间下，100 万个标识的生日碰撞概率约为 `10^-46` 量级；并发时没有共享计数器竞争窗口。

## 已复现的旧问题

`tests/test_identifiers.py` 中固定旧时间为 `1700000000`，稳定覆盖：

1. 多个新进程在同一秒启动，首个标识相同。
2. 连续标识是固定的“10 位秒级时间戳 + 6 位计数器”格式。
3. 多个线程在计数器读改写竞争窗口内产生重复。
4. 旧默认会话标识只有 16 字符、盐只有 8 字符，低于 32 字符安全下限。

修复后的测试同时覆盖长度与字符集、连续值分布、线程并发和多进程并发。

## 百万样本统计

运行：

```bash
python3 tools/collect_statistics.py --count 1000000 --length 32 --output statistics_1m.json
```

最近一次结果：

- 生成数量：1,000,000；唯一数量：1,000,000；重复数：0。
- 位分布：192,000,000 个编码位中，1 位 95,994,750 个，占 `0.49997266`；0 位占 `0.50002734`。
- 字符分布：64 个符号的期望频次为 500,000；观测最小 498,659、最大 501,894；卡方统计量 `58.056`。
- 最长相同前缀：6，出现在排序后的相邻样本 `Fut4vC8iH1Y-_nI_HpiGQRBb_ADxDNvm` 与 `Fut4vCRgLeYOe0y7ablI9id2K8zWHued`。
- 排序相邻前缀长度分布：`{0: 63, 1: 4032, 2: 252281, 3: 714628, 4: 28524, 5: 464, 6: 7}`。

卡方统计量在 63 个自由度下属于正常随机波动范围；统计脚本只描述样本，不把随机结果当成确定性证明。

## 接口兼容性

保留 `generate_session_id(length=32)` 和 `generate_salt(length=32)` 的调用形态，并提供 `generate_id` 别名；位置参数和关键字参数仍可使用。

有意的兼容性变化：

- 默认长度由旧的 16/8 增加到 32；数据库字段至少应支持 32 字符，建议预留 64 字符。
- 显式请求小于 32 的长度现在会失败，而不是继续生成低强度标识。
- 新字符集包含 `-` 和 `_`，不包含 `+`、`/`、`=`，可安全用于 URL、Header 和大多数文本存储。

## 历史标识处理

- 历史会话标识应视为不透明字符串继续接受，直到自然过期；高安全要求系统可在部署时作废现有会话并要求重新登录。
- 历史盐必须继续与原密码哈希和参数一起用于校验；不要单独替换盐，否则原密码无法验证。
- 新注册、改密、重置密码或重新哈希时使用 `generate_salt()` 生成新盐。
- 如果系统需要长期区分多种格式，建议增加独立的 `id_version` 或哈希方案字段，不要假设所有历史标识都达到新长度。


## 可配置标识策略（第三代）

`id_policies.py` 在第二代固定形态之上引入可配置策略，生成仍只使用 `os.urandom()`，策略切换不引入任何时间、计数器或用户空间状态，输出保持不可预测。

策略可配置项：

- **长度下限**：`random_length` 决定随机部分长度，且所有策略强制满足 128 位熵下限（`MIN_ENTROPY_BITS`），低于下限的策略在构造时直接抛出 `ValueError`。
- **分段**：`segment_size` + `separator` 把随机部分切成定长分组（如 `7M52-U2ZO-...`），分隔符不允许出现在字符集内。
- **前缀 / 类型标记**：`prefix` 必须以字母表外的分隔符结尾（如 `s3_`、`hx_`），用于路由与识别，不贡献熵。
- **大小写与字符集**：`ALPHABETS` 提供 base64url / base62 / base32（大写）/ hex（小写）/ HEX（大写）/ digits，字符集不允许有重复字符。

内置策略（完整数据见 `policy_report.json`）：

| 策略 | 前缀 | 随机字符 | 分段 | 总长 | 熵 | 100 万个碰撞概率 |
| --- | --- | --- | --- | --- | --- | --- |
| legacy_session（一代） | 无 | 6 位计数器 | 无 | 16 | 19.93 位 | ~1 |
| legacy_salt（一代） | 无 | 0 | 无 | 8 | 0 位 | ~1 |
| secure_ids_default（二代） | 无 | 32 | 无 | 32 | 192 位 | 10^-46.1 |
| v3_standard | `s3_` | 32 | 无 | 35 | 192 位 | 10^-46.1 |
| v3_segmented | 无 | 32 | 4 | 39 | 160 位 | 10^-36.5 |
| v3_hex | `hx_` | 40 | 无 | 43 | 160 位 | 10^-36.5 |
| v3_compact | 无 | 22 | 无 | 22 | 131 位 | 10^-27.8 |

```python
from id_policies import POLICIES, generate_id, classify_id

new_id = generate_id(POLICIES["v3_segmented"])
result = classify_id("1700000000000000")  # generation=1, policy='legacy_session'
```

## 历史标识识别与迁移分类

`classify_id(value)` 对任意历史标识给出确定性分类：第 1 代（时间戳+计数器 / 截断盐）、第 2 代（32+ 位 base64url）、第 3 代（逐一匹配各策略的完整形态），无法识别时返回 `no-match` 并附每个策略的失败原因。每条结果带 `rule` 与 `reason`，可用 `verify_classification(value, result)` 逐条复核；多个策略同时命中时全部列入 `candidates`。

```bash
python3 tools/policy_report.py table --output policy_report.json          # 各策略长度与熵估计
python3 tools/policy_report.py corpus --output samples/historical_ids.txt # 生成混合各代的样本
python3 tools/policy_report.py classify samples/historical_ids.txt     --output classification_report.jsonl                                  # 逐条分类，可核对
```

`classification_report.jsonl` 每行一条记录，含 `value`、`generation`、`policy`、`rule`、`reason`、`verified` 字段，可直接按行审计。

## 运行测试

```bash
python3 -m unittest discover -s tests -v
```

仅运行统计脚本：

```bash
python3 tools/collect_statistics.py --count 1000000 --length 32
```
