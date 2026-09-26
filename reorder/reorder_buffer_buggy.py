"""现网版本（有缺陷）的消息重排缓冲。

仅用于回归测试复现四类线上问题，请勿在生产使用。
已知缺陷：
  1. 某个序号长期缺失时，后续消息被无限阻塞（无空洞超时兜底）。
  2. 重复到达的消息被交付两次（无判重，"不大于期望序号"即直接交付）。
  3. 序号回绕时判重/排序失效（使用普通整数比较与排序）。
  4. 超时释放只清空缓冲交付，不推进 _next 基线，
     已交付消息随后会被再次交付。
"""


class BuggyReorderBuffer:
    def __init__(self, mod_bits=16, release_timeout=None, now=None):
        self.mod = 1 << mod_bits
        self.release_timeout = release_timeout
        self._now = now
        self._next = 0                # 下一个期望交付的序号
        self._buf = {}                # seq -> payload
        self._waiting_since = None

    def push(self, seq, payload):
        """返回本次触发交付的 payload 列表。"""
        delivered = []

        # 缺陷2：无判重——"不大于期望序号"的消息直接交付
        # 缺陷3：普通整数比较，回绕后旧序号被误判为"未来"消息
        if seq <= self._next:
            delivered.append(payload)
            if seq == self._next:
                self._next = (self._next + 1) % self.mod
                while self._next in self._buf:
                    delivered.append(self._buf.pop(self._next))
                    self._next = (self._next + 1) % self.mod
        else:
            # 缺陷1：next 缺失时后续消息无限积压，无超时兜底
            self._buf[seq] = payload

        # 缺陷4：超时释放直接交付缓冲内容，但不推进 _next 基线
        if (self.release_timeout is not None and self._buf
                and self._now is not None):
            now = self._now()
            if self._waiting_since is None:
                self._waiting_since = now
            elif now - self._waiting_since >= self.release_timeout:
                for s in sorted(self._buf):   # 缺陷3：整数排序，回绕顺序错
                    delivered.append(self._buf[s])
                self._buf.clear()
                self._waiting_since = None
        elif not self._buf:
            self._waiting_since = None

        return delivered
