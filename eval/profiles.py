"""评估用策略档位（profiles）。

每个档位是一个 :class:`Profile`：

* ``name``        报告中显示的稳定名字；
* ``config``      :class:`sensitive.normalize.NormalizeConfig`；
* ``description`` 该档改了什么、适用边界一句话说明。

档位分两类：

1. **逐策略隔离档**：在公共基线 ``BASE_CONFIG``
   （casefold + width + 繁简 + 零宽 + 空白 collapse，同音关）上 **只翻转
   一个开关**，因此两档之间命中集合的差异可以干净地归因到该策略。
2. **原始档 / 组合档**：``raw``（全部关闭）与 ``all_aggressive``
   （再叠同音 + 空白 remove），用于看端点组合效果。
"""

from __future__ import annotations

from dataclasses import dataclass

from sensitive.normalize import (
    DEFAULT_CONFIG,
    RAW_CONFIG,
    NormalizeConfig,
    WS_COLLAPSE,
    WS_REMOVE,
)


@dataclass(frozen=True)
class Profile:
    name: str
    config: NormalizeConfig
    description: str


# 公共基线：默认归一化（繁简已含），同音关，空白折叠
BASE_CONFIG = DEFAULT_CONFIG


def _base(**overrides) -> NormalizeConfig:
    """在默认配置上覆盖部分开关（dataclass.replace 等价手写）。"""
    from dataclasses import replace

    return replace(DEFAULT_CONFIG, **overrides)


PROFILES: tuple[Profile, ...] = (
    Profile(
        "raw",
        RAW_CONFIG,
        "全部归一化关闭，原样逐字匹配（召回下限，仅对照用）",
    ),
    Profile(
        "base_default",
        BASE_CONFIG,
        "基线：大小写+全半角+繁简(1:1)+零宽+空白collapse；同音关",
    ),
    Profile(
        "no_casefold",
        _base(casefold=False),
        "关闭大小写折叠：基线之上只关 casefold，看大小写规避漏报",
    ),
    Profile(
        "no_width",
        _base(width=False),
        "关闭全半角：基线之上只关 width，看全角字符规避漏报",
    ),
    Profile(
        "no_t2s",
        _base(t2s=False),
        "关闭繁→简：基线之上只关 t2s，看繁体规避漏报",
    ),
    Profile(
        "no_zero_width",
        _base(zero_width=False),
        "关闭零宽删除：基线之上只关 zero_width，看零宽拆字漏报",
    ),
    Profile(
        "ws_keep",
        _base(whitespace="keep"),
        "空白保留 keep：相对 collapse，正常文本中的空格不再折叠",
    ),
    Profile(
        "ws_remove",
        _base(whitespace=WS_REMOVE),
        "空白删除 remove：相对 collapse 能抓拆字空格，跨词粘连误伤更大",
    ),
    Profile(
        "homophone_on",
        _base(homophone=True),
        "开启同音折叠(带声调/常用字)：抓同音替字，但会误伤同音正常词",
    ),
    Profile(
        "all_aggressive",
        _base(homophone=True, whitespace=WS_REMOVE),
        "全攻：基线 + 同音折叠 + 空白删除（召回上限，误伤最大）",
    ),
)

PROFILE_BY_NAME = {p.name: p for p in PROFILES}
