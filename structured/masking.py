"""脱敏规则的唯一集中定义处。

任何敏感字段的脱敏逻辑只允许写在本文件的 RULES 中；
字段声明通过 mask="规则名" 引用，业务代码中不得出现脱敏逻辑。
"""


def _phone(value):
    return value[:3] + "****" + value[-4:] if len(value) >= 7 else "****"


def _email(value):
    local, sep, domain = value.partition("@")
    return local[:1] + "***@" + domain if sep else "***"


def _card(value):
    return "*" * max(len(value) - 4, 0) + value[-4:]


def _id_card(value):
    return value[:4] + "*" * (len(value) - 8) + value[-4:] if len(value) > 8 else "****"


RULES = {
    "phone": _phone,
    "email": _email,
    "card": _card,
    "id_card": _id_card,
}


def apply(rule, value):
    return RULES[rule](str(value))
