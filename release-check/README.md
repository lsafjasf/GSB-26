# 发布前检查清单自动化

纯 Python 3 标准库实现，无第三方依赖。

## 运行命令

```bash
# 全部通过场景（退出码 0）
python3 release_check.py --config checks.example.json --json report.json

# 带病发布场景（退出码 1，输出失败报告）
python3 release_check.py --config checks.broken.json

# 自测（7 个用例）
python3 tests/self_test.py

# 性能基准（产物规模可调）
python3 perf_benchmark.py --files 20 --size-mb 50
```

退出码：`0` 全部通过；`1` 存在检查失败；`2` 配置非法 / 检查项缺失（运行前拒绝执行，避免假通过）。

## 检查项类型

| 类型 | 作用 | 必填字段 |
|---|---|---|
| `file_exists` | 产物文件存在 | `path` |
| `checksum` | 读取真实产物计算哈希并比对 | `path`, `algorithm`, `expected` |
| `version_format` | 版本号文件内容匹配正则 | `path`, `pattern` |
| `metadata_field` | JSON 元数据字段存在 / 值一致（支持 `a.b.c` 嵌套） | `path`, `field`（`expected` 可选） |
| `artifact_consistency` | 清单声明的产物逐一读取真实文件重算 sha256 比对 | `manifest` |

每个检查项必填 `id`、`type`、`severity`（`critical`/`major`/`minor`）、`fix`（修复建议）。

## 关键设计

- **绑定真实产物**：`checksum` 与 `artifact_consistency` 均流式读取真实文件重新计算哈希，绝不只看声明文件判定通过。
- **精确定位**：每条失败包含文件、字段、期望值、实际值与修复建议；多项失败按 `critical > major > minor` 排序输出。
- **运行前校验**：检查项为空、类型未知、severity 非法、id 重复、缺 `fix`、正则非法、必填字段缺失等都会在执行任何检查前报错并退出码 2。
- **配置非法示例**：`{"checks": []}` 直接报「检查项缺失，拒绝运行」。

## 文件清单

- `release_check.py` — 主脚本
- `checks.example.json` — 检查项配置样例（对应 `demo-artifacts/`，全部通过）
- `checks.broken.json` — 带病发布配置（对应 `demo-artifacts-broken/`）
- `failure-report.sample.txt` / `failure-report.sample.json` — 失败报告样例（5 项失败：篡改、缺失、版本不一致、格式非法）
- `tests/self_test.py` — 自测（全部通过 / 单项失败 / 多项失败排序 / 产物缺失 / 一致性读真实产物 / 非法配置 / 退出码）
- `perf_benchmark.py` — 大规模产物性能基准

## 性能数据（本机实测，sha256，流式 1MiB 块读取）

| 产物规模 | 检查项数 | 检查耗时 | 吞吐 |
|---|---|---|---|
| 20 个 × 50 MiB（1 GiB） | 21 | 0.93s | ~2150 MiB/s |
| 200 个 × 10 MiB（2 GiB） | 201 | 2.54s | ~1580 MiB/s |
| 5 个 × 200 MiB（1 GiB） | 6 | 1.06s | ~1880 MiB/s |

注：每个产物被哈希两次（`checksum` 项 + `artifact_consistency` 项），实际哈希数据量为产物总量的 2 倍；耗时随数据量近似线性增长。
