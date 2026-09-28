#!/usr/bin/env bash
# 稳定性契约变异探针：在仓库的临时副本上注入真实删改，
# 证明 tools/check_contract.py 对每种破坏都以非零码失败，
# 且输出对应的失败原因。每个变异从干净副本独立施加，互不干扰。
# 不触碰工作区真实文件，结束自动清理。
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
PRISTINE="$WORK/pristine"
CASE="$WORK/case"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$PRISTINE"
cp -a "$ROOT"/. "$PRISTINE"
find "$PRISTINE" -name __pycache__ -type d -prune -exec rm -rf {} +

fail=0

fresh_copy() {
  rm -rf "$CASE"
  cp -a "$PRISTINE" "$CASE"
}

run_check() {  # $1 = 场景名；期望检查脚本非零退出
  local name=$1
  if (cd "$CASE" && python3 tools/check_contract.py >probe.log 2>&1); then
    echo "FAIL  $name：检查脚本竟然通过（应当失败）"
    fail=1
  else
    echo "PASS  $name -> 检查脚本非零退出，命中："
    grep '^  FAIL' "$CASE/probe.log" | sed 's/^/        /'
  fi
}

echo "== 0. 干净副本基线（应当通过） =="
fresh_copy
if (cd "$CASE" && python3 tools/check_contract.py >probe.log 2>&1); then
  echo "PASS  干净副本检查通过"
else
  echo "FAIL  干净副本检查未通过"
  cat "$CASE/probe.log"
  exit 1
fi

echo
echo "== 1. 删除已发布错误码 STORE_CONN =="
fresh_copy
python3 - "$CASE/refactored/errors.py" << 'PY'
import re, sys
path = sys.argv[1]
src = open(path).read()
new = re.sub(r'\n    "STORE_CONN": ErrorSpec\(.*?\),\n', '\n',
             src, count=1, flags=re.S)
assert new != src, "mutation did not apply"
open(path, "w").write(new)
PY
run_check "删除错误码"

echo
echo "== 2. CACHE_BACKEND_DOWN 分类 cache -> internal（语义漂移） =="
fresh_copy
python3 - "$CASE/refactored/errors.py" << 'PY'
import sys
path = sys.argv[1]
src = open(path).read()
old = '"CACHE_BACKEND_DOWN": ErrorSpec(\n        Category.CACHE, True,'
new = '"CACHE_BACKEND_DOWN": ErrorSpec(\n        Category.INTERNAL, True,'
assert old in src, "mutation target not found"
open(path, "w").write(src.replace(old, new, 1))
PY
run_check "分类漂移"

echo
echo "== 3. STORE_QUERY 可重试标记 False -> True（语义漂移） =="
fresh_copy
python3 - "$CASE/refactored/errors.py" << 'PY'
import sys
path = sys.argv[1]
src = open(path).read()
old = 'Category.STORAGE, False, "store query failed: {sql}"'
new = 'Category.STORAGE, True, "store query failed: {sql}"'
assert old in src, "mutation target not found"
open(path, "w").write(src.replace(old, new, 1))
PY
run_check "可重试标记漂移"

echo
echo "== 4. 手工篡改对照表产物（不重新生成） =="
fresh_copy
python3 - "$CASE/contracts/fault_matrix.json" << 'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data["rows"][0]["refactored"]["retryable"] = \
    not data["rows"][0]["refactored"]["retryable"]
json.dump(data, open(path, "w"), ensure_ascii=False, indent=2)
PY
run_check "对照表手工漂移"

echo
if [ "$fail" -eq 0 ]; then
  echo "变异探针结论：全部删改均被检查脚本拦截，工作区未被修改。"
else
  echo "变异探针结论：存在漏检！"
fi
exit $fail
