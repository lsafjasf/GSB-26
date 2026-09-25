"""生成约 10 万行的合成代码库用于性能测试。"""

import pathlib
import random

OUT = pathlib.Path(__file__).resolve().parent / "corpus"


def gen_function(idx, rng):
    name = f"func_{idx}"
    lines = []
    if rng.random() < 0.5:
        lines.append(f"# {name} 的前置注释")
    deco = "@lru_cache(maxsize=None)\n" if rng.random() < 0.2 else ""
    lines.append(f"{deco}def {name}(arg_{idx}, step={idx % 7}):")
    lines.append(f'    """{name} 的文档。')
    lines.append("")
    lines.append("    Args:")
    lines.append(f"        arg_{idx} (int): 输入值。")
    lines.append("        step (int): 步长。")
    lines.append("")
    lines.append("    Returns:")
    lines.append("        int: 计算结果。")
    lines.append('    """')
    lines.append(f"    total = arg_{idx}  # 行尾注释")
    for j in range(idx % 5 + 3):
        lines.append(f"    total += step * {j}  # 累加第 {j} 项")
        if rng.random() < 0.3:
            lines.append(f"    # 中间说明 {j}")
    lines.append("    return total")
    return lines


def gen_class(idx, rng):
    lines = [f"class Worker{idx}:", f'    """Worker{idx} 类。"""', ""]
    for m in range(3):
        lines.append(f"    # 方法 {m} 的说明")
        lines.append(f"    def method_{m}(self, value):")
        lines.append(f"        return value + {m}")
        lines.append("")
    return lines


def main():
    rng = random.Random(42)
    OUT.mkdir(exist_ok=True)
    total = 0
    file_idx = 0
    while total < 100_000:
        lines = [f"# 模块 {file_idx}", ""]
        for i in range(40):
            lines += gen_function(file_idx * 100 + i, rng)
            lines += ["", ""]
            if i % 10 == 0:
                lines += gen_class(i, rng)
                lines += ["", ""]
        path = OUT / f"mod_{file_idx:03d}.py"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        total += len(lines)
        file_idx += 1
    print(f"生成 {file_idx} 个文件，共 {total} 行 -> {OUT}")


if __name__ == "__main__":
    main()
