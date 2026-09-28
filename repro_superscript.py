"""复现：数字比较（numeric_collation 默认开启）遇到上标数字。

²(U+00B2) 是数字字符（str.isdigit() 为真），但不是正则 \\d 能匹配的
十进制数字（str.isdecimal() 为假），int('²') 会抛 ValueError。
修复前：含该字符的记录一进来，整批 sorted 直接抛异常终止。
修复后：统一口径，转换失败降级为字符比较，整批排序正常完成。
"""

import json

from collator import Collator

collator = Collator(json.load(open("rules.json", encoding="utf-8")))

records = ["a10", "a²", "x1²", "a1", "a2", "²", "³"]
print("修复前会抛: ValueError: invalid literal for int() with base 10: '²'")
print("修复后排序结果:", collator.sort(records))
print("is_covered('²') =", collator.is_covered("²"), "（上标数字不再被误判为已覆盖）")
