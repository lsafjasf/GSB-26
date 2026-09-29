# GSB-26 构建产物依赖清单生成工具

`dep_manifest.py` —— 仅依赖 Python 3 标准库，从依赖声明 + 锁文件计算**含传递依赖**的完整组件清单，并与实际构建产物核对差异。

## 功能

- 完整依赖集合：从直接依赖出发遍历锁文件依赖图，记录每个组件的名称、版本、来源、直接引入者（`direct` 标记）与**全部引入路径**（`introduced_by`，同一组件只出现一次）。
- 产物核对：报告两类差异——`missing_in_artifact`（声明存在但产物缺失）与 `uncovered_in_artifact`（产物出现但清单未覆盖），另附版本不一致提示。
- 元数据缺失标记：无版本号（`missing_version`）、来源不明（`unknown_source`）、声明了但未入锁文件（`not_in_lockfile`）的组件标记 `needs_review: true`，**绝不猜测版本**（字段为 `null`）。
- 可复现输出：键排序、顺序固定、无时间戳，同一输入多次生成字节级一致（清单内含输入文件 SHA-256 指纹便于合规核对）。

## 运行命令

```bash
# 1) 生成清单
python3 dep_manifest.py generate \
  --deps examples/deps.json --lock examples/lock.json \
  -o examples/manifest.json

# 2) 与产物核对（存在差异时退出码为 1，可接入 CI）
python3 dep_manifest.py check \
  --manifest examples/manifest.json --artifact examples/artifact.txt \
  -o examples/diff_report.json

# 3) 一步完成
python3 dep_manifest.py all \
  --deps examples/deps.json --lock examples/lock.json \
  --artifact examples/artifact.txt \
  --manifest-out examples/manifest.json --report-out examples/diff_report.json

# 自测（含可复现性测试与 3000 组件性能测试）
python3 test_dep_manifest.py
```

## 输入格式

- `deps.json`：`{"direct": ["name", {"name": "x", "version": "1.0", "source": "..."}]}`
- `lock.json`：`{"packages": [{"name", "version", "source", "dependencies": [...]}]}`，`version`/`source` 允许为 `null`（将触发人工确认标记）。组件身份为 **名称+版本**，允许同名不同版本的多条记录，各自独立成组件。
- `artifact.txt`：每行一个组件，`name` 或 `name@version`，`#` 开头为注释。以 `@` 开头的名字（如 `@scope/pkg`）合法，开头的 `@` 不作版本分隔符；同名不同版本的行各自保留。

## 样例与产物

- 样例输入：`examples/deps.json`、`examples/lock.json`、`examples/artifact.txt`
- 生成清单：`examples/manifest.json`（7 个组件，`crypto-lib` 展示了 3 条引入路径）
- 差异报告：`examples/diff_report.json`（`legacy-driver` 产物缺失、`mystery-bin` 清单未覆盖）

## 性能数据

自测中的合成图（3000 个组件、约 6000 条依赖边、3200 条引入路径）在本机
（Python 3.12）生成清单耗时约 **0.05s**，清单体积约 4.9 MiB；精确数据以
`python3 test_dep_manifest.py` 输出的 `[perf]` 行为准。

注：引入路径按定义全量枚举，若依赖图存在病态的指数级路径组合，清单体积会
相应增长；常规森林/菱形结构无此问题。
