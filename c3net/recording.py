"""QLREC1: match recordings written by the QLadder recorder (a Sich module). See cossacks3-net.md, section 10.

    "QLREC1\n", then for every frame a room member sent:
        u32 ms since the room was created, u32 payload length, u16 code, u32 id from, u32 id to, payload
Frames with code 0xFFFF are markers of the recorder itself: a JSON object
({"ev": "create" | "join" | "leave" | "lock" | "master" | "close", ...}).
Files may be gzip-compressed.
"""

import gzip
import json
import struct
from typing import Iterator, NamedTuple

MAGIC = b"QLREC1\n"
MARKER = 0xFFFF
_HEAD = struct.Struct("<IIHII")


class Entry(NamedTuple):
    ms: int
    code: int
    id_from: int
    id_to: int
    payload: bytes

    def marker(self) -> dict:
        return json.loads(self.payload.decode("latin-1")) if self.code == MARKER else {}


class NotARecording(ValueError):
    pass


def read(data: bytes) -> Iterator[Entry]:
    """Entries of a recording (bytes of a .rec or .rec.gz file). A truncated tail is ignored."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if not data.startswith(MAGIC):
        raise NotARecording("not a QLREC1 recording")
    pos = len(MAGIC)
    while pos + _HEAD.size <= len(data):
        ms, length, code, id_from, id_to = _HEAD.unpack_from(data, pos)
        pos += _HEAD.size
        if pos + length > len(data):
            return
        yield Entry(ms, code, id_from, id_to, data[pos:pos + length])
        pos += length


def open_recording(path: str) -> Iterator[Entry]:
    with open(path, "rb") as f:
        return read(f.read())
