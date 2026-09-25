"""流式协议分帧库（仅标准库）。

提供两种分帧器：
  - LengthPrefixFramer: N 字节大端长度前缀 + 包体
  - DelimiterFramer:    分隔符结尾的包

共同特性：
  - 增量喂入（feed），产出完整消息，残余数据留在内部缓冲
  - 结果与切分方式无关
  - 长度/缓冲均有上限，恶意构造立即报错并指出帧在流中的绝对偏移
  - buffer_peak 记录内部缓冲峰值（字节）
"""

from __future__ import annotations


class FramingError(Exception):
    """分帧错误基类。offset 为出错帧在流中的绝对字节偏移。"""

    def __init__(self, offset: int, message: str):
        self.offset = offset
        super().__init__(message)


class FrameTooLargeError(FramingError):
    """帧声明长度（或分隔符前长度）超过 max_frame_size。"""

    def __init__(self, offset: int, declared: int, limit: int):
        self.declared = declared
        self.limit = limit
        super().__init__(
            offset,
            f"frame at stream offset {offset} declares length {declared}, "
            f"exceeds max_frame_size {limit}",
        )


class BufferLimitExceededError(FramingError):
    """缓冲超过 max_buffer（如分隔符迟迟不出现）。"""

    def __init__(self, offset: int, size: int, limit: int):
        self.size = size
        self.limit = limit
        super().__init__(
            offset,
            f"buffered {size} bytes waiting for frame starting at stream "
            f"offset {offset}, exceeds max_buffer {limit}",
        )


class _BaseFramer:
    def __init__(self) -> None:
        self._buf = bytearray()
        self._offset = 0          # _buf[0] 在流中的绝对偏移
        self.buffer_peak = 0      # 内部缓冲历史峰值（字节）

    def _append(self, data: bytes) -> None:
        self._buf += data
        if len(self._buf) > self.buffer_peak:
            self.buffer_peak = len(self._buf)

    def _consume(self, n: int) -> None:
        del self._buf[:n]
        self._offset += n

    def reset(self) -> None:
        """错误后复用前必须 reset；错误发生时缓冲已自动清空。"""
        self._buf.clear()
        self.buffer_peak = 0

    @property
    def buffered(self) -> int:
        return len(self._buf)


class LengthPrefixFramer(_BaseFramer):
    """长度前缀分帧：header_size 字节大端无符号长度 + 包体。

    缓冲上界：header_size + max_frame_size + 单次 feed 长度
    （声明超限立即拒绝，不会为超限帧累积数据；解析前需先
    接收本次 feed，故上界含单次喂入量）。
    """

    def __init__(self, max_frame_size: int = 1 << 20, header_size: int = 4):
        super().__init__()
        if not 1 <= header_size <= 8:
            raise ValueError("header_size must be in [1, 8]")
        self.max_frame_size = max_frame_size
        self.header_size = header_size
        self.max_buffer = header_size + max_frame_size

    def encode(self, message: bytes) -> bytes:
        if len(message) > self.max_frame_size:
            raise FrameTooLargeError(-1, len(message), self.max_frame_size)
        return len(message).to_bytes(self.header_size, "big") + message

    def feed(self, data: bytes) -> list[bytes]:
        self._append(data)
        out: list[bytes] = []
        hs = self.header_size
        while len(self._buf) >= hs:
            declared = int.from_bytes(self._buf[:hs], "big")
            if declared > self.max_frame_size:
                offset = self._offset
                self._buf.clear()  # 保证缓冲有界；调用方应 reset/重连
                raise FrameTooLargeError(offset, declared, self.max_frame_size)
            if len(self._buf) < hs + declared:
                break
            out.append(bytes(self._buf[hs:hs + declared]))
            self._consume(hs + declared)
        return out


class DelimiterFramer(_BaseFramer):
    """分隔符分帧：消息以 delimiter 结尾，delimiter 不属于消息体。

    注意：分隔符字节若出现在包体内会按协议语义切分（如需包体内
    包含分隔符，请使用 LengthPrefixFramer 或上层转义）。

    缓冲上界：max_buffer + 单次 feed 长度
    （默认 max_buffer = max_frame_size + len(delimiter)）。
    分隔符长时间不出现 -> BufferLimitExceededError，绝不无限缓冲。
    """

    def __init__(self, delimiter: bytes = b"\n", max_frame_size: int = 1 << 20,
                 max_buffer: int | None = None):
        super().__init__()
        if not delimiter:
            raise ValueError("delimiter must be non-empty")
        self.delimiter = delimiter
        self.max_frame_size = max_frame_size
        self.max_buffer = (max_buffer if max_buffer is not None
                           else max_frame_size + len(delimiter))

    def encode(self, message: bytes) -> bytes:
        if len(message) > self.max_frame_size:
            raise FrameTooLargeError(-1, len(message), self.max_frame_size)
        return message + self.delimiter

    def feed(self, data: bytes) -> list[bytes]:
        self._append(data)
        out: list[bytes] = []
        dlen = len(self.delimiter)
        while True:
            idx = self._buf.find(self.delimiter)
            if idx < 0:
                break
            if idx > self.max_frame_size:
                offset = self._offset
                self._buf.clear()
                raise FrameTooLargeError(offset, idx, self.max_frame_size)
            out.append(bytes(self._buf[:idx]))
            self._consume(idx + dlen)
        if len(self._buf) > self.max_buffer:
            offset, size = self._offset, len(self._buf)
            self._buf.clear()  # 报错而不是无限缓冲
            raise BufferLimitExceededError(offset, size, self.max_buffer)
        return out
