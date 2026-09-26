"""The match stream: LAN_RECORD (0x04B0) payloads. See cossacks3-net.md, section 5.

Cossacks 3 is host-authoritative: the host runs the simulation and sends its
results to the other players. A payload is a sequence of blocks:

    00 03 OO SS 00 <body> 01    a record of a script state machine (5.3):
                                OO owner (player 0-11, 14 = "progress"),
                                SS section index in the machine's .aix file
    09 <u24> <u32 n> <n units>  unit state sync (5.6)

    >>> for block in parse(payload):
    ...     if isinstance(block, Record) and block.name == "ReadNew":
    ...         print(block.owner, block.fields["base"])
"""

import struct
from typing import Iterator, NamedTuple, Optional, Union

MAX_PLAYERS = 12              # gc_MaxPlayerCount
OWNER_ENV = 12                # gc_playerind_env: fields, trees
OWNER_MISC = 13               # gc_playerind_misc
OWNER_PROGRESS = 14           # gc_playerind_progress: the "progress" machine
OWNER_POOL = 15               # gc_playerind_pool

RESOURCES = (None, "food", "wood", "stone", "gold", "iron", "coal")  # gc_resource_type_* (0 = none)

# state tag bits (gc_statetag_*), in sync blocks
TAG_NONE = 1 << 0            # essential_none: normal life
TAG_BIRTH = 1 << 1           # essential_birth: a building under construction
TAG_DEATH = 1 << 2           # essential_death
TAG_IDLE, TAG_WALK, TAG_TURN = 1 << 3, 1 << 4, 1 << 5
TAG_ACTION_NONE, TAG_ATTACK, TAG_BUILD, TAG_EXTRACT = 1 << 6, 1 << 7, 1 << 8, 1 << 9
TAG_EXEC_NONE, TAG_EXEC_MOVE = 1 << 10, 1 << 11
TAG_WEAPON_NONE, TAG_WEAPON_0, TAG_WEAPON_1, TAG_WEAPON_2 = 1 << 12, 1 << 13, 1 << 14, 1 << 15
TAG_RES_NONE, TAG_RES_FOOD, TAG_RES_WOOD, TAG_RES_STONE = 1 << 16, 1 << 17, 1 << 18, 1 << 19
TAG_VISUAL_NONE = 1 << 20
TAG_STAGE_0, TAG_STAGE_1, TAG_STAGE_2, TAG_STAGE_3 = 1 << 21, 1 << 22, 1 << 23, 1 << 24
TAG_HIDE = 1 << 25
TAG_SYNC_STP, TAG_SYNC_ENDPOINT = 1 << 29, 1 << 30

# section indexes of data/scripts/units/global.aix (players' machines) and
# data/scripts/progress/progress.aix (the progress machine); records use the Read* ones
GLOBAL_SECTIONS = {
    6: "ReadSquadNew", 8: "ReadSquadListAction", 11: "ReadMove", 13: "ReadNew", 15: "ReadFree", 17: "ReadDeath",
    19: "ReadPlayer", 21: "ReadRally", 23: "ReadOrder", 25: "ReadUpgrade", 27: "ReadProduce", 29: "ReadSearch",
    31: "ReadStand", 33: "ReadConstruct", 35: "ReadApply", 37: "ReadLeaveOrder", 39: "ReadLeave", 41: "ReadProj",
    43: "ReadProjFree", 45: "ReadNewP", 47: "ReadStop", 49: "ReadTrade", 51: "ReadWall", 53: "ReadGate",
    55: "ReadFreeList", 57: "ReadPeaceTime", 59: "ReadSync", 61: "ReadSyncUnitsParams", 64: "ReadPackage",
    70: "ReadTradeResources",
}
PROGRESS_SECTIONS = {8: "ReadRes", 10: "ReadStats", 12: "ReadScenario", 15: "ReadLanSyncData"}

# gc_obj_order_type_*
ORDER_TYPES = {
    0: "none", 1: "move", 2: "attackobj", 3: "gainres", 4: "produce", 5: "patrol", 6: "attackpoint",
    7: "continueattackpoint", 8: "performupgrade", 9: "fishing", 10: "creategates", 11: "buildwallcontinue",
    12: "buildwall", 13: "gotomine", 14: "gototransport", 15: "leavetransport", 16: "leavebuilding", 17: "build",
    18: "guard", 19: "repair", 20: "exitunits",
}


class Short(ValueError):
    """The data ended inside a value, or does not match the expected layout."""


class Record(NamedTuple):
    owner: int                 # player slot 0-11, or OWNER_PROGRESS
    section: int               # section index in the owner's .aix
    name: str                  # "ReadNew", "ReadStats"... ("?<n>" if not a known section)
    fields: Optional[dict]     # None: no layout known (skipped to the next block)


class Sync(NamedTuple):
    key: int                   # 3 bytes after the 09: purpose unknown
    units: list                # [{"uid", "tag"?, "target"?, "x"?, "z"?, "dir"?}]


Block = Union[Record, Sync]


class Buf:
    """RecordCustomRead*: Boolean/Byte 1 byte, Word 2, Integer/Float 4, String u16 length + bytes (latin-1)."""

    def __init__(self, data: bytes, pos: int = 0):
        self.data, self.pos = data, pos

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise Short
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def byte(self) -> int:
        return self.take(1)[0]

    boolean = byte

    def word(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def int(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def float(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def string(self) -> str:
        return self.take(self.word()).decode("latin-1")

    def count(self) -> int:
        n = self.int()
        if n < 0 or n > 100000:
            raise Short
        return n

    def ints(self, n: int) -> list:
        return [self.int() for _ in range(n)]


# ------------------------------------------------------------ progress machine

def read_res(b: Buf) -> dict:
    """ReadRes (writeres.inc): {"players": {slot: {resource: ("abs", amount) | ("delta", change)}}}.

    The game keeps resources on hand bit-inverted (setres = not amount): an absolute
    value is sent inverted, a small change (|d| <= 127) as one byte, sign in bit 7.
    """
    mask, out = b.word(), {}
    for i in range(MAX_PLAYERS):
        if mask & (1 << i):
            changed, compressed = b.byte(), b.byte()
            for j in range(7):
                if changed & (1 << j):
                    if compressed & (1 << j):
                        v = b.byte()
                        out.setdefault(i, {})[RESOURCES[j] or "none"] = ("delta", -(v - 128) if v >= 128 else v)
                    else:
                        out.setdefault(i, {})[RESOURCES[j] or "none"] = ("abs", ~b.int())
    return {"players": out}


STAT_GROUPS = (  # (name, mask, first bit): mask 0 bits start after the MAX_PLAYERS player bits
    ("total", 0, 0), ("upgrades", 0, 6), ("mines", 0, 12),
    ("units", 1, 0), ("buildings", 1, 6), ("life", 1, 12), ("buy", 1, 18), ("sell", 1, 24),
)


def read_stats(b: Buf) -> dict:
    """ReadStats (writestats.inc): {"players": {slot: {group: {resource: amount}}}}, running totals."""
    masks = (b.int(), b.int())
    out = {}
    for i in range(MAX_PLAYERS):
        if masks[0] & (1 << i):
            groups = {}
            for name, m, base in STAT_GROUPS:
                shift = base + (MAX_PLAYERS if m == 0 else 0)
                for j in range(1, 7):
                    if masks[m] & (1 << (shift + j)):
                        groups.setdefault(name, {})[RESOURCES[j]] = b.int()
            out[i] = groups
    return {"players": out}


LAN_ECONOMY = ("idle", "idle_mines", "food", "wood", "stone", "gold", "iron", "coal")


def read_lan_sync(b: Buf) -> dict:
    """ReadLanSyncData (writelansyncdata.inc): peasants idle and at work; squads' state."""
    mask, economy = b.word(), {}
    for i in range(MAX_PLAYERS):
        if mask & (1 << i):
            fields = b.byte()
            if fields:
                player = b.byte()
                economy[player] = {name: b.word() for bit, name in enumerate(LAN_ECONOMY) if fields & (1 << bit)}
    squads = {}
    for i in range(MAX_PLAYERS):
        items = []
        for _ in range(b.count()):
            uid, hold = b.int(), b.boolean()
            items.append((uid, bool(hold), None if hold else b.word()))
        if items:
            squads[i] = items
    return {"economy": economy, "squads": squads}


PROGRESS_RECORDS = {8: read_res, 10: read_stats, 15: read_lan_sync}


# ------------------------------------------------------------ players' machines

def _uids_after(b: Buf, fields: dict) -> dict:
    fields["uids"] = b.ints(b.count())
    return fields


def _order(b: Buf) -> dict:
    f = {"type": b.int(), "target": b.int(), "clear": b.boolean(), "lock": b.boolean()}
    count = b.count()
    if f["type"] in (5, 6):  # patrol, attack a point
        f["x"], f["z"] = b.float(), b.float()
    f["uids"] = b.ints(count)
    return f


def _sync_units(b: Buf) -> dict:
    units = []
    for _ in range(b.count()):
        uid = b.int()
        units.append((uid, b.int() if b.boolean() else None))
    return {"hp": units}


def _move(b: Buf) -> dict:
    f = {"dx": b.float(), "dz": b.float(), "add": b.boolean(), "first": b.boolean(), "mode": b.int()}
    f["units"] = [(b.int(), b.float(), b.float()) for _ in range(b.count())]
    f["squad"] = b.word()
    if f["squad"]:
        f["squad_player"] = b.byte()
    return f


def _squad_action(b: Buf) -> dict:
    f = {"server": b.boolean(), "action": b.int(), "state": b.boolean(), "form": b.int()}
    f["squads"] = b.ints(b.count())
    f["uids"] = b.ints(b.count())
    return f


def _wall(b: Buf) -> dict:
    f = {"server": b.boolean(), "usage": b.byte(), "cid": b.byte(), "id": b.byte()}
    f["pieces"] = [(b.byte(), b.float(), b.float(), b.int()) for _ in range(b.count())]  # sprite, x, z, uid
    f["num"] = b.int()
    f["uids"] = b.ints(b.count())
    return f


GLOBAL_RECORDS = {
    6: lambda b: _uids_after(b, {"server": b.boolean(), "player": b.int(), "sid": b.string(), "form": b.int(),
                                 "officer": b.int(), "drummer": b.int(), "position": b.boolean(),
                                 "in_squad": b.boolean(), "squad": b.int()}),
    8: _squad_action,
    11: _move,
    13: lambda b: {"server": b.boolean(), "race": b.string(), "base": b.string(), "x": b.float(), "z": b.float(),
                   "cid": b.int(), "uid": b.int(), "num": b.int()},
    15: lambda b: {"uid": b.int(), "value": b.int()},
    17: lambda b: _uids_after(b, {"server": b.boolean(), "mode": b.int(), "key": b.float()}),
    19: lambda b: {"uid": b.int(), "capture": b.boolean()},
    21: lambda b: {"uid": b.int(), "set": b.boolean(), "x": b.float(), "z": b.float()},
    23: _order,
    25: lambda b: _uids_after(b, {"server": b.boolean(), "upgrade": b.int(), "state": b.boolean()}),
    27: lambda b: _uids_after(b, {"member": b.int(), "cid": b.int(), "amount": b.int(), "state": b.boolean()}),
    29: lambda b: _uids_after(b, {"server": b.boolean()}),
    31: lambda b: _uids_after(b, {"server": b.boolean()}),
    33: lambda b: _uids_after(b, {"server": b.boolean(), "cid": b.int(), "sid": b.string(), "x": b.float(),
                                  "z": b.float(), "clear": b.boolean()}),
    35: lambda b: {"a": b.int(), "b": b.int(), "c": b.int(), "d": b.int()},
    37: lambda b: _uids_after(b, {"server": b.boolean()}),
    39: lambda b: {"server": b.boolean(), "uid": b.int()},
    41: lambda b: {"uid": b.int(), "target": b.int(), "use_target_pos": b.boolean(),
                   "from": (b.float(), b.float(), b.float()), "weapon": b.int(), "to": (b.float(), b.float(), b.float()),
                   "cid": b.int(), "id": b.int(), "weapon_index": b.int(), "key": b.float(), "proj_uid": b.int(),
                   "num": b.int()},
    43: lambda b: {"uid": b.int(), "value": b.int()},
    45: lambda b: {"server": b.boolean(), "race": b.string(), "base": b.string(), "x": b.float(), "z": b.float(),
                   "roll": b.float(), "player": b.int(), "id": b.int(), "uid": b.int(), "num": b.int()},
    47: lambda b: _uids_after(b, {}),
    49: lambda b: {"server": b.boolean(), "sell": b.byte(), "buy": b.byte(), "amount": b.int()},
    51: _wall,
    53: lambda b: _uids_after(b, {"server": b.boolean()}),
    55: lambda b: _uids_after(b, {}),
    57: lambda b: {"server": b.boolean()},
    61: _sync_units,
    64: lambda b: {"server": b.boolean(), "text": b.string()},
    70: lambda b: {"server": b.boolean(), "player": b.byte(), "to": b.byte(), "resource": b.byte(), "amount": b.int()},
}


# ------------------------------------------------------------ blocks

def sync_block(data: bytes, pos: int = 0) -> tuple:
    """A sync block at `pos`: (Sync, end position)."""
    if pos + 8 > len(data) or data[pos] != 0x09:
        raise Short
    key = data[pos + 1] | data[pos + 2] << 8 | data[pos + 3] << 16
    count = struct.unpack_from("<I", data, pos + 4)[0]
    pos += 8
    units = []
    for _ in range(count):
        if pos + 4 > len(data):
            raise Short
        unit = {"uid": data[pos] | data[pos + 1] << 8 | data[pos + 2] << 16}
        flags = data[pos + 3]
        pos += 4
        if flags & 0xE0:
            raise Short  # no such flag: not a sync block after all
        if flags & 0x08:
            if pos + 4 > len(data):
                raise Short
            unit["tag"] = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        if flags & 0x01:
            if pos + 3 > len(data):
                raise Short
            unit["target"] = data[pos] | data[pos + 1] << 8 | data[pos + 2] << 16
            pos += 3
        if flags & 0x02:
            if pos + 8 > len(data):
                raise Short
            unit["x"], unit["z"] = struct.unpack_from("<ff", data, pos)
            pos += 8
        if flags & 0x04:
            if pos + 4 > len(data):
                raise Short
            unit["dir"] = struct.unpack_from("<f", data, pos)[0]
            pos += 4
        units.append(unit)
    return Sync(key, units), pos


def _is_record_start(data: bytes, pos: int) -> bool:
    return data[pos:pos + 2] == b"\x00\x03" and pos + 5 <= len(data) and data[pos + 4] == 0


def _boundary(data: bytes, pos: int) -> Optional[int]:
    """The end marker (01) of the record at `pos` whose layout is unknown: the one before a block that parses."""
    q = data.find(b"\x01", pos + 5)
    while q >= 0:
        nxt = q + 1
        if nxt == len(data) or _is_record_start(data, nxt):
            return q
        if data[nxt] == 0x09:
            try:
                sync_block(data, nxt)
                return q
            except Short:
                pass
        q = data.find(b"\x01", q + 1)
    return None


def record_name(owner: int, section: int) -> str:
    names = PROGRESS_SECTIONS if owner == OWNER_PROGRESS else GLOBAL_SECTIONS
    return names.get(section, f"?{section}")


def parse(data: bytes) -> Iterator[Block]:
    """The blocks of a LAN_RECORD payload, in order. Stops at the first byte that starts no block."""
    pos = 0
    while pos < len(data):
        if data[pos] == 0x09:
            try:
                block, pos = sync_block(data, pos)
            except Short:
                return
            yield block
            continue
        if not _is_record_start(data, pos):
            return
        owner, section = data[pos + 2], data[pos + 3]
        grammar = (PROGRESS_RECORDS if owner == OWNER_PROGRESS else GLOBAL_RECORDS).get(section)
        if grammar:
            b = Buf(data, pos + 5)
            try:
                fields = grammar(b)
                if b.pos < len(data) and data[b.pos] == 0x01:
                    yield Record(owner, section, record_name(owner, section), fields)
                    pos = b.pos + 1
                    continue
            except (Short, UnicodeDecodeError):
                pass
        end = _boundary(data, pos)
        yield Record(owner, section, record_name(owner, section), None)
        if end is None:
            return
        pos = end + 1
