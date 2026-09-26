"""Small demo: convert a sample document with both link modes."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from richtext2text import Config, convert

HTML = """
<h1>Quarterly Report</h1>
<p>Sales grew <b>12%</b>. See the
<a href="https://example.com/details">full dashboard</a>.</p>
<h2>Highlights</h2>
<ul>
  <li>Product
    <ol><li>shipped v2</li><li>fixed 34 bugs</li></ol>
  </li>
  <li>Team hired 3 engineers</li>
</ul>
<blockquote><p>Best quarter so far. -- CEO</p></blockquote>
<pre>growth = revenue_q2 / revenue_q1  # 1.12</pre>
<table>
  <tr><th>Region</th><th>Revenue</th></tr>
  <tr><td>EMEA</td><td>4.2M</td></tr>
  <tr><td>APAC</td><td>3.8M</td></tr>
</table>
"""

print("=== link_mode=url (default) ===")
print(convert(HTML))
print("=== link_mode=text ===")
print(convert(HTML, Config(link_mode="text")))
