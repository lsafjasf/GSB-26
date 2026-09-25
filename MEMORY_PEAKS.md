# Memory Peak Measurements

Configuration: Python 3, 64 KiB maximum payload/message. Allocator
numbers are new traced bytes after parser setup, measured by
`tracemalloc`; they include fixed parser buffers, transient output
copies, and caller-side test stream allocations made during that
phase. Use the fixed buffer columns for parser-retained bounds.

| Case | Result | Internal logical peak | Fixed buffer capacity | `tracemalloc` peak | At-limit behavior |
| --- | --- | ---: | ---: | ---: | --- |
| length prefix: one 64 KiB message in one feed | 1 message emitted | 0 KiB | 0 KiB | 198023 B | Complete frame is emitted directly; no retained payload buffer. |
| length prefix: 64 KiB message split across feeds | 1 message after final feed | 64 KiB | 64 KiB | 66925 B | First partial payload lazily allocates one fixed 64 KiB buffer. |
| length prefix: declares 10 MiB with 1 MiB input | rejected at frame offset 0 | 0 KiB | 0 KiB | 2099443 B | Header is rejected before payload buffering; framer is marked broken. |
| delimiter: one 64 KiB message split across feeds | 1 message after delimiter | 64 KiB | 64 KiB | 132506 B | First byte allocates one fixed 64 KiB byte buffer. |
| delimiter: 1 MiB feed with no delimiter | rejected at frame offset 0, stream offset 65536 | 64 KiB | 64 KiB | 1116649 B | Parsing stops at the message-size boundary; no unbounded growth. |

Guaranteed parser-retained bounds for the default configuration:

- `LengthPrefixFramer`: at most 64 KiB for a fragmented payload; complete one-shot payloads are copied directly to the returned message.
- `DelimiterFramer`: at most `max_message_size + len(delimiter) - 1`, i.e. 64 KiB for the default one-byte newline.
- On rejection the parser enters a broken state and later `feed` calls raise instead of accepting more data.
