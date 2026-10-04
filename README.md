# framing — 流式协议分帧库（纯标准库 Python 3）

> 另一组件：分片并行查询引擎 `qengine` 见 [`QE_README.md`](QE_README.md)
> （并行扫描、聚合分片边界口径、串行/并行逐行对拍、并行度耗时基准）。

按字节流增量喂入，产出完整消息；长度前缀与分隔符两种分帧方式；
结果与切分方式无关；长度与缓冲均有硬上限，恶意输入立即报错并指出帧偏移。

## 文件

- `framing.py` — 库源码（`LengthPrefixFramer` / `DelimiterFramer`）
- `test_framing.py` — 切分等价性对拍 + 边界用例集（23 个用例）
- `bench_buffer.py` — 缓冲峰值测量（含 tracemalloc 进程峰值）

## 运行

```sh
python3 test_framing.py -v   # 自测
python3 bench_buffer.py      # 内存峰值数据
```

## 用法

```python
from framing import LengthPrefixFramer, DelimiterFramer

f = LengthPrefixFramer(max_frame_size=1 << 20)   # 4 字节大端长度前缀
wire = f.encode(b"hello") + f.encode(b"")        # 编码（含零长度包）
msgs = f.feed(wire[:3]) + f.feed(wire[3:])       # 任意切分喂入

d = DelimiterFramer(delimiter=b"\n", max_frame_size=1 << 20)
msgs = d.feed(b"a\nb\nc")                        # -> [b"a", b"b"]，b"c" 留缓冲
```

## 缓冲上界与超限行为

| 分帧器 | 内部缓冲上界 | 超限行为 |
|---|---|---|
| LengthPrefix | `header_size + max_frame_size + 单次feed` | 声明长度超限 → `FrameTooLargeError`（含流偏移），缓冲清零 |
| Delimiter | `max_buffer + 单次feed`（默认 `max_frame_size + len(delimiter)`） | 分隔符迟迟不出现 → `BufferLimitExceededError`，缓冲清零 |

报错后缓冲自动清空，即使调用方继续喂入也不会无限增长；复用前调 `reset()`。
`framer.buffer_peak` 可随时读取历史缓冲峰值。

实测（12.5 MiB 流、64 KiB 帧上限，详见 `bench_buffer.py` 输出）：
逐字节喂入时缓冲峰值 65540 B（= 上界），进程峰值 0.19 MiB；
1 MiB 大块喂入时峰值 ≈ 上界 + 1 MiB，与理论一致。

## 设计说明

- 分隔符协议下，包体内的分隔符字节即帧边界（协议语义）；需要包体
  内嵌任意字节（含分隔符）时请用长度前缀分帧，或在上层做转义。
- 错误中的 `offset` 是出错帧在整个流中的绝对字节偏移，跨多次 feed 累计。
