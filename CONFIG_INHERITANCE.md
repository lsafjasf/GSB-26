# 多环境配置继承系统（base < env < local）

公共项只在 `config/base.json` 写一次；各环境只写差异；`local` 做本机覆盖。
纯 Python 3 标准库，无第三方依赖。

## 文件

- `config_layers.py` — 合并引擎 + 溯源 + 报错 + CLI（源码）
- `test_config_layers.py` — 回归测试（29 个用例，`python3 test_config_layers.py -v`）
- `config/base.json` — 基础层（公共项唯一来源）
- `config/env/dev.json` / `staging.json` / `prod.json` — 环境层，`prod` 通过 `"extends": "staging"` 继承
- `config/local.json` — 本机层样例（实际项目建议加入 `.gitignore`，缺文件时自动跳过）
- `samples/merged_prod.json` — prod 合并结果样例
- `samples/merged_prod_with_provenance.txt` — 合并结果 + 逐项溯源数据
- `samples/merged_dev.json` — dev 合并结果样例

## 覆盖顺序

合并严格按下面顺序进行，**后一层逐叶子覆盖前一层**（嵌套对象深度合并；
标量和数组整体替换，不做逐元素合并）：

```
base  <  env 继承链（父环境 → 子环境，按 extends 解析）  <  local
```

以 prod 为例，实际参与的层是：

```
base < env:staging < env:prod < local
```

## 合并结果样例（prod）

`samples/merged_prod.json`（节选）：

```json
{
  "app":    { "debug": false, "name": "order-service", "port": 80 },
  "db":     { "host": "127.0.0.1", "pool_size": 50, "port": 15432 },
  "logging":{ "level": "WARN", "sinks": ["stdout", "file"] }
}
```

典型路径：`db.host` 经 base→staging→prod 三次定义后，又被 local 覆盖；
`features.new_checkout` 在 base 为 false、staging 打开，prod 未写，故
透传 staging 的值。

## 溯源数据

合并结果 `MergedConfig` 除 `data` 外还返回与配置同构的 `provenance`
树；`provenance_map()` 给出扁平溯源，每个生效叶子值都能回答来源层：

```
path                      <= source layer
app.debug                 <= env:prod
app.name                  <= base
app.port                  <= env:prod
db.host                   <= local
db.pool_size              <= env:prod
db.port                   <= local
features.beta_api         <= base
features.new_checkout     <= env:staging
logging.level             <= env:prod
logging.sinks             <= local
```

代码中查询：`merged.source_of("db.host") -> "local"`；
`MergedConfig.layers` 给出实际参与的层顺序。

## 错误处理（不静默取默认值）

- **必需项缺失**：`--require db.password` 或 API `required=[...]` 校验。
  缺失时报错并列出检查过的每一层：
  `缺少必需配置项 'db.password'；已检查的层（低 -> 高优先级）: base < env:dev < local`
- **必需项来源层不符**：`--require app.name@env:prod` 可要求某项必须由
  指定层提供，报错时同时给出实际来源层。
- **结构性冲突**：同一路径上一层给映射、另一层给标量/数组，抛
  `LayerConflictError`，消息指出问题层和点分路径，例如层 `env:prod`
  把 `db` 写成了字符串。
- **文件/继承问题**：base 缺失、环境文件缺失（含被哪个层 `extends`
  引用）、`extends` 形成循环、JSON 语法错误（含行列号）均报错并指明层。

## 运行方式

```bash
# 合并某环境（local 存在时自动叠加）
python3 config_layers.py prod

# 合并并打印逐项溯源
python3 config_layers.py prod --provenance

# 必需项校验：任意层提供即可 / 必须由指定层提供
python3 config_layers.py dev --require db.host
python3 config_layers.py dev --require db.password@local

# 自定义配置目录 / 输出落盘
python3 config_layers.py prod --config-dir config > merged.json

# 回归测试
python3 test_config_layers.py -v
```

作为库使用：

```python
from config_layers import load_environment

merged = load_environment("prod", "config",
                          required=["db.host", {"path": "secrets.key", "layer": "local"}])
print(merged.data)                # 生效配置
print(merged.source_of("db.host"))  # 'local'
```

## 测试覆盖的情形

| 情形 | 用例 |
|---|---|
| 覆盖无覆盖 | `test_no_override_single_base_layer` |
| 单层覆盖 | `test_single_layer_override` |
| 多层覆盖（顺序） | `test_multi_layer_override_order` |
| 环境 extends 链 | `test_env_chain_parent_merges_before_child` |
| 深度合并 / 叶子级溯源 | `test_deep_merge_nested_dicts_with_per_leaf_provenance` |
| 数组整体替换 | `test_list_replaced_wholesale_not_elementwise` |
| 必需项缺失 | `test_missing_required_any_layer_reports_checked_layers` |
| 必需项指定层缺失/不符 | `test_required_from_specific_layer_*` |
| 映射 vs 标量冲突 | `test_mapping_vs_scalar_conflict_names_layer_and_path` 等 |
| 文件缺失 / extends 断链 / 循环 / 非法 JSON | `LoadEnvironmentTest` 各用例 |
| 溯源完整性（每叶子必可溯源） | `test_provenance_map_covers_every_leaf` |
