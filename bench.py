"""大文档转换性能测试：生成数 MB 的混合结构 HTML，测量转换耗时。"""

import argparse
import sys
import time

from richtext2text import Config, convert

SECTION = """
<section>
<h2>第 {i} 节 性能测试章节标题</h2>
<p>这是第 {i} 段正文，包含 <b>加粗文本</b>、<i>斜体文本</i>、
<code>inline_code()</code> 以及一个
<a href="https://example.com/doc/{i}">参考链接 {i}</a>，
用于模拟真实检索语料中的混合行内元素。</p>
<ul>
  <li>无序要点 A-{i}</li>
  <li>无序要点 B-{i}
    <ol start="1">
      <li>嵌套有序子项 {i}.1</li>
      <li>嵌套有序子项 {i}.2</li>
    </ol>
  </li>
</ul>
<blockquote><p>第 {i} 节引用文字，应当带引用前缀输出。</p></blockquote>
<pre>def section_{i}(x):
    if x &gt; {i}:
        return x * 2   # 代码块内容必须逐字保留
    return x</pre>
<table>
  <tr><th>列一</th><th>列二</th><th>列三</th><th>列四</th></tr>
  <tr><td>r{i}c1</td><td>r{i}c2</td><td>r{i}c3</td><td>r{i}c4</td></tr>
  <tr><td>s{i}c1</td><td>s{i}c2</td><td>s{i}c3</td><td>s{i}c4</td></tr>
</table>
</section>
"""


def make_document(target_bytes: int) -> str:
    parts = ["<html><body><h1>性能测试文档</h1>"]
    i = 0
    size = len(parts[0])
    while size < target_bytes:
        chunk = SECTION.format(i=i)
        parts.append(chunk)
        size += len(chunk)
        i += 1
    parts.append("</body></html>")
    return "".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="richtext2text 性能测试")
    parser.add_argument("--mb", type=float, default=4.0, help="文档大小 (MiB)")
    parser.add_argument("--runs", type=int, default=5, help="重复次数")
    parser.add_argument("--link-mode", default="url", choices=["url", "text"])
    args = parser.parse_args()

    html = make_document(int(args.mb * 1024 * 1024))
    size = len(html.encode("utf-8"))
    cfg = Config(link_mode=args.link_mode)
    print(f"输入: {size / 1024 / 1024:.2f} MiB, link_mode={args.link_mode}, "
          f"Python {sys.version.split()[0]}")

    # 预热 + 正确性抽查
    out = convert(html, cfg)
    print(f"输出: {len(out.encode('utf-8')) / 1024 / 1024:.2f} MiB")

    times = []
    for _ in range(args.runs):
        start = time.perf_counter()
        convert(html, cfg)
        times.append(time.perf_counter() - start)

    best = min(times)
    avg = sum(times) / len(times)
    mib = size / 1024 / 1024
    print(f"耗时: best={best:.3f}s avg={avg:.3f}s runs={args.runs}")
    print(f"吞吐: best={mib / best:.1f} MiB/s avg={mib / avg:.1f} MiB/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
