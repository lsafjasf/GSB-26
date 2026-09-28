"""消息重排缓冲（修复版），仅依赖标准库。

功能：按序号重排乱序到达的消息，向上游严格按序、无重复交付。

设计要点
--------
1. 序号空间为模 2^mod_bits 的环，比较采用 RFC 1982 风格的
   序号算术（serial number arithmetic）：a “落后于” b 当且仅当
   0 < (b - a) % 2^N < 2^(N-1)。只要缓冲窗口 max_window 远小于
   2^(N-1)，回绕场景下的判重与先后关系就是良定义的。
2. 判重规则（可验证）：
   - 序号 s 已在缓冲中            -> 重复，丢弃并计数；
   - s 落后于 next_expected
     （落在已交付的半个序号空间内）-> 重复，丢弃并计数；
   - s 落在接收窗口
     [next_expected, next_expected + max_window) 内 -> 缓冲；
   - s 在窗口之外                 -> 溢出，按 on_full 策略处理。
3. 空洞处理：next_expected 缺失且缓冲非空时开始计时，超过
   gap_timeout 即报告缺口 [next_expected, min_buffered - 1]，
   把 next_expected 推进到最小缓冲序号并继续交付。基线单调前进，
   已交付消息永远不会再次交付。每个缺口独立起算：缺口等待锚点
   (_gap_since) 只属于当前缺口，一旦基线向前推进（缺口被跳过或被
   迟到消息补齐）即清除，新缺口重新计时，不会复用上一个缺口的时间。
4. 缓冲上界：任意时刻缓冲条目数 <= max_window。溢出策略 on_full：
     - "reject"（默认）：拒绝新消息，计入 dropped；
     - "expire"：立即按空洞处理推进基线（等价于超时立即触发），
       先按序交付可交付内容，再为新消息腾出空间。
"""

import time


def seq_distance(a, b, mod):
    """a 到 b 的正向距离 (b - a) % mod。"""
    return (b - a) % mod


class GapEvent:
    """一次空洞跳过事件：缺口为 [gap_start, gap_end]（含端点，模序号空间）。"""

    __slots__ = ("gap_start", "gap_end")

    def __init__(self, gap_start, gap_end):
        self.gap_start = gap_start
        self.gap_end = gap_end

    def __repr__(self):
        return f"GapEvent([{self.gap_start}, {self.gap_end}])"


class ReorderBuffer:
    def __init__(self, mod_bits=16, max_window=1024, gap_timeout=5.0,
                 on_full="reject", now=None, start_seq=0):
        if max_window >= (1 << (mod_bits - 1)):
            raise ValueError("max_window 必须小于序号空间的一半，"
                             "否则回绕判重无法良定义")
        if on_full not in ("reject", "expire"):
            raise ValueError("on_full 必须是 'reject' 或 'expire'")
        self.mod = 1 << mod_bits
        self.half = self.mod >> 1
        self.max_window = max_window
        self.gap_timeout = gap_timeout
        self.on_full = on_full
        self._now = now or time.monotonic

        self.next_expected = start_seq % self.mod
        self._buf = {}
        self._gap_since = None

        # 可观测指标
        self.duplicates = 0      # 判重丢弃次数
        self.dropped = 0         # 缓冲满拒绝次数
        self.gaps = []           # GapEvent 列表
        self.max_occupancy = 0   # 缓冲占用峰值（条数）

    # ---- 序号比较（RFC 1982 风格） ----

    def _is_behind(self, seq):
        """seq 是否落后于 next_expected（即落在已交付的半空间内）。"""
        d = seq_distance(seq, self.next_expected, self.mod)
        return 0 < d <= self.half

    def _in_window(self, seq):
        """seq 是否落在接收窗口 [next_expected, next_expected + max_window)。"""
        return seq_distance(self.next_expected, seq, self.mod) < self.max_window

    # ---- 主流程 ----

    def push(self, seq, payload):
        """接收一条消息，返回本次触发交付的 payload 列表（按序）。"""
        seq %= self.mod

        if seq in self._buf or self._is_behind(seq):
            self.duplicates += 1
            return []

        delivered = []
        if len(self._buf) >= self.max_window or not self._in_window(seq):
            if self.on_full == "reject":
                self.dropped += 1
                return []
            delivered.extend(self._make_room(seq))

        self._buf[seq] = payload
        if len(self._buf) > self.max_occupancy:
            self.max_occupancy = len(self._buf)

        delivered.extend(self._drain())
        return delivered

    def flush(self):
        """检查空洞超时，必要时跳过缺口继续交付。返回交付列表。"""
        return self._drain()

    # ---- 内部 ----

    def _drain(self):
        delivered = []
        # 每个缺口必须独立起算：只要基线缺失被消除（缺口被跳过或被
        # 补齐）而向前推进，旧锚点立即失效，新缺口重新计时。循环每次
        # 排空后重新评估，因此一次调用内可连续处理多个缺口。
        while True:
            if self.next_expected in self._buf:
                # 头部到位：此前的等待（若有）随缺口消除而结束
                self._gap_since = None
                while self.next_expected in self._buf:
                    delivered.append(self._buf.pop(self.next_expected))
                    self.next_expected = (self.next_expected + 1) % self.mod

            if self.next_expected in self._buf:
                continue

            # 基线缺失
            if not self._buf:
                self._gap_since = None
                break

            now = self._now()
            if self._gap_since is None:
                self._gap_since = now
                break
            if now - self._gap_since < self.gap_timeout:
                break
            self._skip_gap()  # 内部重置锚点，下一轮按新缺口独立起算
        return delivered

    def _skip_gap(self):
        """报告缺口并把基线推进到最小缓冲序号（按正向序号距离定义）。"""
        target = min(self._buf,
                     key=lambda s: seq_distance(self.next_expected, s, self.mod))
        gap_end = (target - 1) % self.mod
        self.gaps.append(GapEvent(self.next_expected, gap_end))
        self.next_expected = target
        self._gap_since = None

    def _make_room(self, seq):
        """on_full='expire'：推进基线直到 seq 可入窗且有空间。"""
        delivered = []
        while len(self._buf) >= self.max_window or not self._in_window(seq):
            if self._buf:
                self._skip_gap()
                delivered.extend(self._drain())
            else:
                # 缓冲已空但 seq 仍在窗口外：发送端跳号超过窗口，
                # 记整段缺口并直接把基线对齐到 seq。
                self.gaps.append(GapEvent(self.next_expected,
                                          (seq - 1) % self.mod))
                self.next_expected = seq
                self._gap_since = None
        return delivered
