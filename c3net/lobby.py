"""The lobby transport: frames, values and parser trees. See cossacks3-net.md, sections 4 and 6.

Every message between a game client and the lobby server (TCP, port 31523 by
default) is a frame: a 14-byte header and a payload. In a room, the server
relays the game's own traffic (LAN_* codes) between the players in the same
frames.
"""

import struct
from typing import Iterator, NamedTuple

HEADER = struct.Struct("<IHII")  # payload length, code, id from, id to

# message codes (the names follow Sich, https://github.com/3skcassoc/sich, xconst.lua)
CODES = {
    0x0032: "LAN_PARSER", 0x0064: "LAN_CLIENT_INFO", 0x00C8: "LAN_SERVER_INFO",
    0x0191: "PING", 0x0192: "SERVER_CLIENTINFO", 0x0193: "USER_CLIENTINFO",
    0x0194: "SERVER_SESSION_MSG", 0x0195: "USER_SESSION_MSG", 0x0196: "SERVER_MESSAGE", 0x0197: "USER_MESSAGE",
    0x0198: "SERVER_REGISTER", 0x0199: "USER_REGISTER", 0x019A: "SERVER_AUTHENTICATE", 0x019B: "USER_AUTHENTICATE",
    0x019C: "SERVER_SESSION_CREATE", 0x019D: "USER_SESSION_CREATE", 0x019E: "SERVER_SESSION_JOIN",
    0x019F: "USER_SESSION_JOIN", 0x01A0: "SERVER_SESSION_LEAVE", 0x01A1: "USER_SESSION_LEAVE",
    0x01A2: "SERVER_SESSION_LOCK", 0x01A3: "USER_SESSION_LOCK", 0x01A4: "SERVER_SESSION_INFO",
    0x01A5: "USER_SESSION_INFO", 0x01A6: "USER_CONNECTED", 0x01A7: "USER_DISCONNECTED",
    0x01AA: "SERVER_SESSION_UPDATE", 0x01AB: "SERVER_SESSION_CLIENT_UPDATE", 0x01AC: "USER_SESSION_CLIENT_UPDATE",
    0x01AD: "SERVER_VERSION_INFO", 0x01AE: "USER_VERSION_INFO", 0x01AF: "SERVER_SESSION_CLOSE",
    0x01B0: "USER_SESSION_CLOSE", 0x01B5: "SERVER_SESSION_KICK", 0x01B7: "SERVER_SESSION_CLSCORE",
    0x01B8: "USER_SESSION_CLSCORE", 0x01BB: "SERVER_SESSION_PARSER", 0x01BC: "USER_SESSION_PARSER",
    0x01BD: "USER_SESSION_RECREATE", 0x01BE: "USER_SESSION_REJOIN", 0x01DE: "SERVER_UPDATE_STATS",
    0x0456: "LAN_DO_START", 0x0457: "LAN_DO_START_GAME", 0x0460: "LAN_DO_READY", 0x0461: "LAN_DO_READY_DONE",
    0x04B0: "LAN_RECORD",
}

# parser ids: the first u32 of SERVER_SESSION_PARSER / LAN_PARSER payloads
PARSERS = {
    1: "LAN_GENERATE", 2: "LAN_READYSTART", 3: "LAN_START", 4: "LAN_ROOM_READY", 5: "LAN_ROOM_START",
    6: "LAN_ROOM_CLIENT_CHANGES", 7: "LAN_GAME_READY", 8: "LAN_GAME_ANSWER_READY", 9: "LAN_GAME_START",
    10: "LAN_GAME_SURRENDER", 11: "LAN_GAME_SURRENDER_CONFIRM", 12: "LAN_GAME_SERVER_LEAVE",
    13: "LAN_GAME_SESSION_RESULTS", 14: "LAN_GAME_SYNC_REQUEST", 15: "LAN_GAME_SYNC_DATA",
    16: "LAN_GAME_SYNC_GAMETIME", 17: "LAN_GAME_SYNC_ALIVE", 100: "LAN_ROOM_SERVER_DATASYNC",
    101: "LAN_ROOM_SERVER_DATACHANGE", 102: "LAN_ROOM_CLIENT_DATACHANGE", 103: "LAN_ROOM_CLIENT_LEAVE",
    200: "LAN_MODS_MODSYNC_REQUEST", 201: "LAN_MODS_MODSYNC_PARSER", 202: "LAN_MODS_CHECKSUM_REQUEST",
    203: "LAN_MODS_CHECKSUM_ANSWER", 204: "LAN_MODS_CHECKSUM_REQUESTCANJOIN",
    205: "LAN_MODS_CHECKSUM_ANSWERCANJOIN", 206: "LAN_MODS_CHECKSUM_ANSWERCANNOTJOIN",
    300: "LAN_ADVISER_CLIENT_DATACHANGE",
}

VICTORY_STATE = {0: "none", 1: "win", 2: "lose"}  # gc_player_victorystate_*


class Frame(NamedTuple):
    code: int
    id_from: int
    id_to: int
    payload: bytes

    @property
    def name(self) -> str:
        return CODES.get(self.code, f"0x{self.code:04X}")


def read_frames(data: bytes) -> Iterator[Frame]:
    """Frames of a captured TCP stream (one direction). Stops at a truncated frame."""
    pos = 0
    while pos + HEADER.size <= len(data):
        length, code, id_from, id_to = HEADER.unpack_from(data, pos)
        pos += HEADER.size
        if pos + length > len(data):
            return
        yield Frame(code, id_from, id_to, data[pos:pos + length])
        pos += length


def frame(code: int, payload: bytes = b"", id_from: int = 0, id_to: int = 0) -> bytes:
    return HEADER.pack(len(payload), code, id_from, id_to) + payload


class Short(ValueError):
    pass


class Reader:
    """Values of lobby payloads: u8/u32, strings with a 1-, 2- or 4-byte length, parser trees."""

    def __init__(self, data: bytes, pos: int = 0):
        self.data, self.pos = data, pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise Short
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def string(self, size: int = 1, encoding: str = "latin-1") -> str:
        n = {1: self.u8, 4: self.u32}[size]() if size != 2 else struct.unpack("<H", self.take(2))[0]
        return self.take(n).decode(encoding)

    def parser(self, depth: int = 0) -> dict:
        """{"k": key, "v": value, "c": [children]}: key and value are u32-length strings, then a u32 child count."""
        if depth > 64:
            raise Short
        key, value, count = self.string(4), self.string(4), self.u32()
        return {"k": key, "v": value, "c": [self.parser(depth + 1) for _ in range(min(count, 100000))]}


def parser_payload(data: bytes) -> tuple:
    """(parser id, tree) of a SERVER_SESSION_PARSER / LAN_PARSER payload."""
    r = Reader(data)
    return r.u32(), r.parser()


def results(tree: dict) -> list:
    """LAN_GAME_SESSION_RESULTS (parser 13): [{"id", "slot", "result"}]; id 0 is a computer player."""
    out = []

    def walk(node):
        f = {c["k"]: c["v"] for c in node.get("c", [])}
        if "id" in f and "res" in f:
            try:
                out.append({"id": int(f["id"]), "slot": int(f["ind"]) if "ind" in f else None,
                            "result": VICTORY_STATE.get(int(f["res"]), f["res"])})
            except ValueError:
                pass
        for c in node.get("c", []):
            walk(c)

    walk(tree)
    return out


def session_update(data: bytes) -> dict:
    """SERVER_SESSION_UPDATE: game name, lobby status ("map name"), money, fog of war, battlefield."""
    r = Reader(data)
    return {"gamename": r.string(1), "mapname": r.string(1), "money": r.u32(), "fog": r.u8(), "battlefield": r.u8()}


def session_lock(data: bytes) -> list:
    """SERVER_SESSION_LOCK: [(player id, lobby team)]; the lobby team is a placeholder in regular rooms."""
    r = Reader(data)
    return [(r.u32(), r.u8()) for _ in range(r.u32())]


def clscore(data: bytes) -> tuple:
    """SERVER_SESSION_CLSCORE: (player id, result code), see section 7.4."""
    return struct.unpack("<Ii", data[:8])
