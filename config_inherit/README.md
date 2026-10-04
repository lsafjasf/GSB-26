# config_inherit — 多环境配置继承（Python 3，仅标准库）

解决"多个环境各自维护一份配置、公共项改一次要同步十几遍"的问题：
公共项只写在 `base.json`，环境只写差异，本地临时覆盖放 `local.json`。

## 覆盖顺序（写死，后者覆盖前者）

```
base  ->  环境继承链（父环境 -> 子环境，可多级）  ->  local
```

- `base.json`：公共配置，必须存在；可用 `"__required__": ["a.b", ...]` 声明必需键。
- `envs/<env>.json`：环境差异，必须存在；可用 `"__inherits__": "<父环境>"` 继承另一个环境。
- `local.json`：本机临时覆盖，可选，不进版本库时也不影响加载。

## 合并规则

- 字典 + 字典：递归深合并（环境只写要改的字段）。
- 标量 / 列表：整体替换（列表不做元素级合并）。
- 同一键一边是字典、另一边不是字典：**类型冲突，报错**并指出两层的名称与文件。
- 合并后 `__required__` 中的键缺失或为 `null`：**报错**，列出已检查的层；绝不静默取默认值。
- 继承链成环、环境文件缺失、JSON 非法、环境名非法：均报错并指出问题层。

## 合并结果样例

`prod.cn` 继承 `prod`，再叠加 `local.json`：

```
base -> env:prod -> env:prod.cn -> local
```

结果见 `examples/output/merged.json`，例如：

| 键 | 生效值 | 来源层 |
| --- | --- | --- |
| `db.host` | `prod-db.cn.internal` | `env:prod.cn`（覆盖 `env:prod`） |
| `db.pool_size` | `50` | `env:prod`（覆盖 `base` 的 10） |
| `log.level` | `DEBUG` | `local`（覆盖 `base`→`env:prod`） |
| `service.port` | `9090` | `local`（覆盖 `base` 的 8080） |
| `db.port` | `5432` | `base`（无覆盖） |

## 溯源数据

每个生效值都能回答"来自哪一层"，见 `examples/output/provenance.json`：

```json
"log.level": {
  "value": "DEBUG",
  "layer": "local",
  "file": "config_inherit/examples/local.json",
  "history": [
    {"layer": "base",      "file": ".../base.json",         "value": "INFO"},
    {"layer": "env:prod",  "file": ".../envs/prod.json",    "value": "WARN"},
    {"layer": "local",     "file": ".../local.json",        "value": "DEBUG"}
  ]
}
```

`layer_order` 给出本次生效的层顺序；`local_layer.present` 标明 local 层是否参与。

## 运行方式

```bash
# 回归测试（18 个用例）
python3 -m unittest discover -s config_inherit/tests -v

# 查看某环境的合并结果
python3 -m config_inherit --config-dir config_inherit/examples --env prod.cn

# 查看溯源数据 / 重新生成样例产物
python3 -m config_inherit --config-dir config_inherit/examples --env prod.cn --provenance
python3 -m config_inherit --config-dir config_inherit/examples --env prod.cn --write config_inherit/examples/output

# 库方式调用
from config_inherit import load_config
merged, provenance = load_config("config_inherit/examples", "prod.cn")
```

出错时 CLI 退出码为 2，错误信息指出问题层与文件，例如：

```
配置错误: 必需键 'db.host' 在所有层中都未定义（已检查的层: base -> env:dev）
配置错误: 键 'db' 类型冲突: 层 env:dev（.../envs/dev.json）提供的是 非对象，但层 base（.../base.json）已将其定义为 对象
配置错误: 环境继承链出现环: a -> b -> a
```

## 目录结构

```
config_inherit/
  loader.py            # 核心：链解析、深合并、溯源、校验
  __main__.py          # CLI
  examples/            # 样例配置（base / envs / local）
  examples/output/     # 合并结果与溯源数据样例
  tests/               # 回归测试
```

## 边界用例覆盖

- 无覆盖（全部来自 base）、单层覆盖、多层覆盖（含环境继承 + local）
- 必需项缺失、必需项为 null、必需项由环境层补齐
- 字典深合并、列表整体替换、字典/标量类型冲突
- 继承环（含自继承）、环境文件缺失、base 缺失、非法 JSON、非法环境名（防路径穿越）
- local 缺失（允许）、`--no-local` 禁用、保留键 `__inherits__`/`__required__` 不进合并结果
