"""误伤评估：在刻意构造的良性语料上统计误伤率。

方法：
  1. 构造一批"良性但长得像"的样本（Scunthorpe 问题）与真正的违规样本；
  2. 分别用 无防护 / 词边界 / 词边界+白名单 三种配置扫描；
  3. 统计良性样本上的误伤命中数（FP）与违规样本上的漏判数（FN）。

运行：python3 eval_fp.py
"""

from sensitive_matcher import SensitiveMatcher

SENSITIVE = ["ass", "sex", "damn", "木马", "水枪", "赌博"]

# 良性样本：包含敏感词子串或处于白名单语境，但语义无害
BENIGN = [
    "I passed the class with assistance.",       # ass in class/assistance
    "Sussex and Essex are counties.",            # sex in Sussex/Essex
    "The bass guitar sounds classic.",           # ass in bass/classic
    "Damnation is a theological term.",          # damn in damnation
    "我们周末去游乐场坐旋转木马。",               # 木马 in 旋转木马
    "孩子拿着玩具水枪在院子里玩。",               # 水枪 in 玩具水枪
    "He said damn, that's my ass!",              # 真违规（对照，不计入良性）
]

WHITELIST = ["旋转木马", "水枪"]
# 注意：最后一条是真违规样本，从良性集中剔除
BENIGN, VIOLATION = BENIGN[:-1], [BENIGN[-1]]


def evaluate(**kw):
    m = SensitiveMatcher(**kw)
    m.add_all(SENSITIVE)
    m.build()
    fp = sum(len(m.find_all(t)) for t in BENIGN)
    fn = sum(1 for t in VIOLATION if not m.find_all(t))
    return fp, fn


def main():
    configs = [
        ("无防护", dict()),
        ("词边界", dict(boundary="both")),
        ("边界+白名单", dict(boundary="both", whitelist=WHITELIST)),
    ]
    print(f"良性样本 {len(BENIGN)} 条，违规样本 {len(VIOLATION)} 条，"
          f"敏感词 {len(SENSITIVE)} 个\n")
    print(f"{'配置':<12} {'误伤(FP)':>8} {'漏判(FN)':>8}")
    for name, kw in configs:
        fp, fn = evaluate(**kw)
        print(f"{name:<12} {fp:>8} {fn:>8}")


if __name__ == "__main__":
    main()
