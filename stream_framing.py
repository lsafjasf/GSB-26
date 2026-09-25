from __future__ import annotations

import struct
from typing import List, Optional, Union


ByteString = Union[bytes, bytearray, memoryview]

LENGTH_HEADER_SIZE = 4
LENGTH_HEADER_FORMAT = ">I"


class FramingError(Exception):
    """Base class for malformed or rejected byte streams."""

    def __init__(
        self,
        message: str,
        *,
        frame_offset: Optional[int] = None,
        stream_offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.frame_offset = frame_offset
        self.stream_offset = stream_offset
        self.limit = limit


class LengthLimitError(FramingError):
    """Raised when a declared length-prefix payload is larger than allowed."""


class BufferLimitError(FramingError):
    """Raised when buffering another byte would cross the configured limit."""


class DelimiterLimitError(BufferLimitError):
    """Raised when a complete delimiter is not found before the byte limit."""


def _is_bounded_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class LengthPrefixFramer:
    """
    Incremental framer for four-byte, unsigned, big-endian length prefixes.

    A complete frame is ``uint32 payload_length + payload``. Payload bytes are
    streamed directly from a feed when they already form a complete frame.
    Incomplete payloads use one fixed-capacity bytearray, allocated lazily.
    """

    def __init__(
        self,
        max_payload_size: int = 64 * 1024,
        max_buffer_size: Optional[int] = None,
    ) -> None:
        if not _is_bounded_int(max_payload_size):
            raise ValueError("max_payload_size must be a non-negative integer")
        if max_payload_size > 0xFFFFFFFF:
            raise ValueError("max_payload_size cannot exceed the uint32 range")

        if max_buffer_size is None:
            max_buffer_size = max_payload_size
        if not _is_bounded_int(max_buffer_size):
            raise ValueError("max_buffer_size must be a non-negative integer")
        if max_buffer_size < max_payload_size:
            raise ValueError("max_buffer_size must be >= max_payload_size")

        self.max_payload_size = max_payload_size
        self.max_buffer_size = max_buffer_size

        self._broken = False
        self._state = "header"
        self._stream_cursor = 0
        self._frame_start = 0
        self._header_size = 0
        self._header_value = 0
        self._expected_payload_size = 0
        self._payload_size = 0
        self._payload_buffer: Optional[bytearray] = None
        self._peak_buffer_size = 0

    @property
    def buffer_size(self) -> int:
        """Logical number of payload bytes currently retained."""
        return self._payload_size if self._state == "payload" else 0

    @property
    def buffer_capacity(self) -> int:
        """Capacity currently allocated for the internal payload buffer."""
        return len(self._payload_buffer) if self._payload_buffer is not None else 0

    @property
    def peak_buffer_size(self) -> int:
        """Highest logical buffer size observed during this framer's lifetime."""
        return self._peak_buffer_size

    def feed(self, chunk: ByteString) -> List[bytes]:
        if self._broken:
            raise FramingError("framer is broken after a previous framing error")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("chunk must be bytes, bytearray, or memoryview")

        messages: List[bytes] = []
        view = memoryview(chunk)
        position = 0
        stream_cursor = self._stream_cursor

        try:
            while position < len(view):
                if self._state == "header":
                    while position < len(view) and self._header_size < LENGTH_HEADER_SIZE:
                        self._header_value = (self._header_value << 8) | view[position]
                        self._header_size += 1
                        position += 1
                        stream_cursor += 1

                    if self._header_size < LENGTH_HEADER_SIZE:
                        break

                    declared_size = self._header_value
                    if declared_size > self.max_payload_size:
                        self._broken = True
                        self._stream_cursor = stream_cursor
                        raise LengthLimitError(
                            (
                                f"declared payload size {declared_size} exceeds "
                                f"limit {self.max_payload_size}"
                            ),
                            frame_offset=stream_cursor - LENGTH_HEADER_SIZE,
                            stream_offset=stream_cursor,
                            limit=self.max_payload_size,
                        )

                    self._expected_payload_size = declared_size
                    self._state = "payload"

                    if declared_size == 0:
                        messages.append(b"")
                        self._start_next_frame(stream_cursor)

                if self._state == "payload":
                    remaining = self._expected_payload_size - self._payload_size
                    available = len(view) - position
                    if available == 0:
                        break
                    take = min(remaining, available)

                    if self._payload_buffer is None:
                        if take == remaining:
                            messages.append(bytes(view[position : position + take]))
                            self._start_next_frame(stream_cursor + take)
                        else:
                            self._payload_buffer = bytearray(self.max_buffer_size)
                            self._payload_buffer[0:take] = view[position : position + take]
                            self._payload_size = take
                            self._peak_buffer_size = max(self._peak_buffer_size, take)
                    else:
                        buffer = self._payload_buffer
                        old_size = self._payload_size
                        buffer[old_size : old_size + take] = view[
                            position : position + take
                        ]
                        self._payload_size = old_size + take
                        self._peak_buffer_size = max(
                            self._peak_buffer_size, self._payload_size
                        )

                        if self._payload_size == self._expected_payload_size:
                            messages.append(bytes(buffer[: self._payload_size]))
                            self._start_next_frame(stream_cursor + take)

                    position += take
                    stream_cursor += take

            self._stream_cursor = stream_cursor
            return messages
        except Exception:
            self._broken = True
            self._stream_cursor = stream_cursor
            raise

    def _start_next_frame(self, frame_start: int) -> None:
        self._stream_cursor = frame_start
        self._frame_start = frame_start
        self._state = "header"
        self._header_size = 0
        self._header_value = 0
        self._expected_payload_size = 0
        self._payload_size = 0


class DelimiterFramer:
    """
    Incremental delimiter-based framer.

    Multi-byte delimiters are matched with KMP so partial and overlapping
    candidates remain correct across feed boundaries. Candidate delimiter bytes
    are retained only until a mismatch proves they are payload, or until the
    final delimiter byte completes a message.
    """

    def __init__(
        self,
        delimiter: ByteString = b"\n",
        max_message_size: int = 64 * 1024,
        max_buffer_size: Optional[int] = None,
    ) -> None:
        if not isinstance(delimiter, (bytes, bytearray, memoryview)):
            raise TypeError("delimiter must be bytes, bytearray, or memoryview")
        delimiter = bytes(delimiter)
        if not delimiter:
            raise ValueError("delimiter must contain at least one byte")
        if not _is_bounded_int(max_message_size):
            raise ValueError("max_message_size must be a non-negative integer")

        required_buffer_size = max_message_size + len(delimiter) - 1
        if max_buffer_size is None:
            max_buffer_size = required_buffer_size
        if not _is_bounded_int(max_buffer_size):
            raise ValueError("max_buffer_size must be a non-negative integer")
        if max_buffer_size < required_buffer_size:
            raise ValueError(
                "max_buffer_size must be >= max_message_size + len(delimiter) - 1"
            )

        self.delimiter = delimiter
        self.max_message_size = max_message_size
        self.max_buffer_size = max_buffer_size
        self._prefix_table = self._build_prefix_table(delimiter)

        self._broken = False
        self._stream_cursor = 0
        self._frame_start = 0
        self._match_size = 0
        self._buffer_size = 0
        self._buffer: Optional[bytearray] = None
        self._peak_buffer_size = 0

    @property
    def buffer_size(self) -> int:
        """Logical number of bytes retained for the current incomplete frame."""
        return self._buffer_size

    @property
    def buffer_capacity(self) -> int:
        """Capacity currently allocated for the internal buffer."""
        return len(self._buffer) if self._buffer is not None else 0

    @property
    def peak_buffer_size(self) -> int:
        """Highest logical buffer size observed during this framer's lifetime."""
        return self._peak_buffer_size

    def feed(self, chunk: ByteString) -> List[bytes]:
        if self._broken:
            raise FramingError("framer is broken after a previous framing error")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("chunk must be bytes, bytearray, or memoryview")

        messages: List[bytes] = []
        view = memoryview(chunk)
        stream_cursor = self._stream_cursor
        delimiter = self.delimiter
        delimiter_size = len(delimiter)

        try:
            for local_offset in range(len(view)):
                byte = view[local_offset]
                absolute_offset = stream_cursor + local_offset

                while self._match_size and byte != delimiter[self._match_size]:
                    self._match_size = self._prefix_table[self._match_size - 1]

                if byte == delimiter[self._match_size]:
                    self._match_size += 1
                else:
                    self._match_size = 0

                if self._match_size == delimiter_size:
                    message_size = self._buffer_size - (delimiter_size - 1)
                    if message_size < 0 or message_size > self.max_message_size:
                        raise FramingError(
                            "internal delimiter matcher produced an invalid size"
                        )
                    if self._buffer is None:
                        messages.append(b"")
                    else:
                        messages.append(bytes(self._buffer[:message_size]))
                    self._start_next_frame(absolute_offset + 1)
                    continue

                if self._buffer_size >= self.max_buffer_size:
                    self._broken = True
                    self._stream_cursor = absolute_offset
                    raise DelimiterLimitError(
                        (
                            f"delimiter not found within {self.max_message_size} "
                            "message bytes"
                        ),
                        frame_offset=self._frame_start,
                        stream_offset=absolute_offset,
                        limit=self.max_message_size,
                    )

                if self._buffer is None:
                    self._buffer = bytearray(self.max_buffer_size)
                self._buffer[self._buffer_size] = byte
                self._buffer_size += 1
                self._peak_buffer_size = max(
                    self._peak_buffer_size, self._buffer_size
                )

            self._stream_cursor = stream_cursor + len(view)
            return messages
        except Exception:
            self._broken = True
            raise

    def _start_next_frame(self, next_frame_start: int) -> None:
        self._stream_cursor = next_frame_start
        self._frame_start = next_frame_start
        self._match_size = 0
        self._buffer_size = 0

    @staticmethod
    def _build_prefix_table(delimiter: bytes) -> List[int]:
        table = [0] * len(delimiter)
        matched = 0
        for index in range(1, len(delimiter)):
            while matched and delimiter[index] != delimiter[matched]:
                matched = table[matched - 1]
            if delimiter[index] == delimiter[matched]:
                matched += 1
            table[index] = matched
        return table


def encode_length_prefix(message: ByteString) -> bytes:
    """Encode one length-prefixed frame."""
    size = len(message)
    if size > 0xFFFFFFFF:
        raise ValueError("message is too large for a uint32 length prefix")
    return struct.pack(LENGTH_HEADER_FORMAT, size) + bytes(message)
