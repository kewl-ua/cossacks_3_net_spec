"""Room data: the game name, the lobby status and the room datasync. See cossacks3-net.md, sections 6.3.1 and 7."""

import re
from typing import Optional

SLOTS = 12  # gc_MaxPlayerCount

# generator settings, in datasync order (value -> meaning)
SEASON = {0: "summer", 1: "winter", 2: "desert"}
TERRAIN = {0: "land", 1: "mediterranean", 2: "peninsulas", 3: "islands", 4: "continents", 5: "continent",
           6: "lakes", 7: "coast", 8: "rivers", 9: "no water"}
RELIEF = {0: "plain", 1: "hills", 2: "mountains", 3: "highlands", 4: "plateau", 5: "desert"}
START_RESOURCES = {0: "normal", 1: "rich", 2: "thousands", 3: "millions"}
MINES = {0: "few", 1: "medium", 2: "many"}
MAP_SIZE = {3: "small", 0: "normal", 1: "large (2x)", 2: "huge (4x)"}
GENERATOR = [("season", SEASON), ("terrain", TERRAIN), ("relief", RELIEF), ("resources", START_RESOURCES),
             ("mines", MINES), ("size", MAP_SIZE)]

# additional settings (gc_mapsettings_*), in datasync order
ADDITIONAL = [
    ("starting_units", {0: "default", 1: "army", 2: "large army", 3: "huge army", 4: "many peasants",
                        5: "different nations", 6: "towers", 7: "cannons", 8: "cannons and howitzers",
                        9: "18th century barracks", 10: "17th century barracks", 11: "village", 12: "log cabins",
                        13: "union"}),
    ("balloon", {0: "default", 1: "no balloons", 2: "balloons"}),
    ("cannons", {0: "default", 1: "no cannons, towers and walls", 2: "expensive cannons"}),
    ("peace_time", {0: "none", 1: "10 min", 2: "20 min", 3: "30 min", 4: "45 min", 5: "60 min", 6: "90 min",
                    7: "2 h", 8: "3 h", 9: "4 h", 11: "15 min"}),
    ("century18", {0: "default", 1: "never", 2: "from the start"}),
    ("capture", {0: "default", 1: "no peasant capture", 2: "no peasant and centre capture", 3: "cannons only"}),
    ("market_dip", {0: "default", 1: "no diplomatic centre", 2: "no market", 3: "neither", 4: "expensive mercenaries"}),
    ("allies", {0: "default", 1: "side by side"}),
    ("autosave", {}),
    ("limit", {0: "no limit", **{i: f"{n} units" for i, n in enumerate((500, 750, 1000, 1500, 2200, 3000, 5000, 8000), 1)}}),
    ("speed", {0: "normal", 1: "fast", 2: "very fast", -1: "adjustable"}),
    ("adviser", {0: "default", 1: "no adviser"}),
]

DIFFICULTY = {0: "normal", 1: "hard", 2: "very hard", 3: "impossible"}  # aidifficulty of computer players


def decode_text(raw) -> str:
    """Game text (room names, nicknames): UTF-8 when it decodes, else Windows-1251."""
    if isinstance(raw, str):
        raw = raw.encode("latin-1")
    for encoding in ("utf-8", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1")


def game_name(gamename: str) -> dict:
    """'"name"<TAB>"password"<TAB>0<checksum>' -> {"name", "password", "kind", "checksum"}.

    kind: "0" a regular room, "r" a rating (quick play) room, "h" a historical battle.
    checksum: 4 hex digits of the script library's MD5 ("3EEB" for the unmodded 2.2.3 scripts).
    """
    parts = gamename.split("\t")
    if len(parts) != 3:
        return {"name": gamename, "password": "", "kind": None, "checksum": None}
    name, password, tail = (p.strip('"') for p in parts)
    return {"name": name, "password": password, "kind": tail[:1] or None, "checksum": tail[1:] or None}


def lobby_status(mapname: str) -> Optional[dict]:
    """The room's "map name" as the lobby shows it: flags|humans|computers|closed|ping|rank[|quick play...]."""
    parts = mapname.split("|")
    if len(parts) < 6 or not all(re.fullmatch(r"-?\d+", p) for p in parts[:6]):
        return None
    flags, humans, computers, closed, ping, rank = (int(p) for p in parts[:6])
    return {"exists": bool(flags & 1), "full": bool(flags & 2), "locked": bool(flags & 4), "humans": humans,
            "computers": computers, "closed": closed, "ping": ping, "rank": rank, "quick_play": parts[6:]}


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def datasync(s: str) -> Optional[dict]:
    """The room datasync (parser 100, key "s") -> {"slots": [...], setting: value, ..., "battle", "stage"}.

    Slots are gMap.players in order; a slot is a player ("lanid,cid,team,color,ready"),
    a computer ("-difficulty,cid,team,color"), closed ("x") or empty ("0").
    """
    parts = (s or "").split("|")
    if len(parts) < SLOTS + len(GENERATOR) + len(ADDITIONAL):
        return None
    slots = []
    for i, entry in enumerate(parts[:SLOTS]):
        fields = [_int(x) for x in entry.split(",")]
        if entry == "x":
            slots.append({"slot": i, "kind": "closed"})
        elif entry == "0":
            slots.append({"slot": i, "kind": "empty"})
        elif len(fields) >= 4 and None not in fields[:4]:
            lead, cid, team, color = fields[:4]
            slot = {"slot": i, "cid": cid, "team": team, "color": color}
            if len(fields) >= 5 and lead > 0:
                slot.update(kind="player", id=lead, ready=bool(fields[4]))
            else:
                slot.update(kind="computer", difficulty=-lead)
            slots.append(slot)
    out = {"slots": slots}
    values = [_int(x) for x in parts[SLOTS:]]
    for (key, _), value in zip(GENERATOR + ADDITIONAL, values):
        out[key] = value
    rest = values[len(GENERATOR) + len(ADDITIONAL):]
    out["battle"] = rest[0] if rest else None       # historical battle index, -1: a random map
    out["stage"] = rest[1] if len(rest) > 1 else None
    return out


def describe(settings: dict) -> dict:
    """Setting values -> their meaning ("terrain": "land", ...)."""
    out = {}
    for key, names in GENERATOR + ADDITIONAL:
        value = settings.get(key)
        if value is not None and names:
            out[key] = names.get(value, value)
    return out
