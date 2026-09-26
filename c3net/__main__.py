"""Command line: python -m c3net dump|summary FILE, python -m c3net upgrades GAME_DIR."""

import argparse
import json
import sys
from collections import Counter, defaultdict

from . import lobby, recording, room, stream


def _short(value, limit=12):
    if isinstance(value, list) and len(value) > limit:
        return value[:limit] + [f"... {len(value) - limit} more"]
    return value


def dump(path, as_json=False):
    game_start = None
    for e in recording.open_recording(path):
        row = {"ms": e.ms, "code": lobby.CODES.get(e.code, f"0x{e.code:04X}"), "from": e.id_from, "to": e.id_to}
        if e.code == recording.MARKER:
            row["marker"] = e.marker()
        elif e.code in (0x0032, 0x01BB):
            parser_id, tree = lobby.parser_payload(e.payload)
            row["parser"] = lobby.PARSERS.get(parser_id, parser_id)
            row["tree"] = tree
        elif e.code == 0x01AA:
            row.update(lobby.session_update(e.payload))
        elif e.code == 0x04B0:
            if game_start is None:
                game_start = e.ms
            row["t"] = round((e.ms - game_start) / 1000, 3)
            row["blocks"] = []
            for block in stream.parse(e.payload):
                if isinstance(block, stream.Sync):
                    row["blocks"].append({"sync": _short(block.units, 4)})
                else:
                    fields = {k: _short(v) for k, v in (block.fields or {}).items()}
                    row["blocks"].append({"record": block.name, "owner": block.owner, "fields": fields})
        else:
            row["bytes"] = len(e.payload)
        if as_json:
            print(json.dumps(row, ensure_ascii=False))
        else:
            head = f'{row["ms"]:>9} {row["code"]:<24} {row["from"]:>4}->{row["to"]:<4}'
            rest = {k: v for k, v in row.items() if k not in ("ms", "code", "from", "to")}
            print(head, json.dumps(rest, ensure_ascii=False)[:300])


def summary(path):
    """Per player slot: nation, units made, buildings placed, upgrades started, deaths, final ReadStats."""
    game_start, host = None, None
    nation, made, placed, upgrades, stats = {}, defaultdict(Counter), defaultdict(Counter), defaultdict(list), {}
    owner, dead = {}, set()
    datasync = None
    for e in recording.open_recording(path):
        if e.code == 0x01BB and game_start is None:  # the last datasync before the game starts
            parser_id, tree = lobby.parser_payload(e.payload)
            if parser_id == 100:
                s = next((c["v"] for c in tree["c"] if c["k"] == "s"), None)
                datasync = room.datasync(s)
        if e.code != 0x04B0:
            continue
        if game_start is None:
            game_start = e.ms
        t = (e.ms - game_start) / 1000
        blocks = list(stream.parse(e.payload))
        if host is None and any(isinstance(b, stream.Sync) or b.owner == stream.OWNER_PROGRESS for b in blocks):
            host = e.id_from  # only the host sends sync blocks and progress records
        if e.id_from != host:
            continue  # a client's request: what the host then did is in its own records
        for b in blocks:
            if isinstance(b, stream.Sync):
                dead.update(u["uid"] for u in b.units if u.get("tag", 0) & stream.TAG_DEATH)
                continue
            f = b.fields
            if f is None:
                continue
            if b.name == "ReadStats":
                for slot, groups in f["players"].items():
                    stats[slot] = groups
            elif b.name == "ReadNew" and f["race"] == "units":
                made[b.owner][f["base"]] += 1
                owner[f["uid"]] = b.owner
            elif b.name == "ReadConstruct" and f["server"]:
                placed[b.owner][f["sid"]] += 1
                nation.setdefault(b.owner, f["cid"])
            elif b.name == "ReadUpgrade" and f["server"] and f["state"]:
                upgrades[b.owner].append((round(t), f["upgrade"]))
    lost = Counter(owner[uid] for uid in dead if uid in owner)
    out = {"datasync": datasync, "players": {}}
    for slot in sorted(set(made) | set(placed) | set(stats)):
        out["players"][slot] = {
            "nation": nation.get(slot), "units_made": dict(made[slot]), "units_lost": lost[slot],
            "buildings_placed": dict(placed[slot]), "upgrades_started": upgrades[slot], "stats": stats.get(slot),
        }
    print(json.dumps(out, ensure_ascii=False, indent=1))


def upgrades_cmd(game_dir, nation=None):
    from . import upgrades
    ids = upgrades.upgrade_ids(game_dir, [nation] if nation is not None else upgrades.PLAYABLE)
    for cid, items in sorted(ids.items()):
        for i, upgrade_id in enumerate(items):
            print(cid, i, upgrade_id, upgrades.meaning(upgrade_id) or "")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="c3net", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="every frame of a QLREC1 recording, decoded")
    d.add_argument("file")
    d.add_argument("--json", action="store_true", help="one JSON object per line")
    s = sub.add_parser("summary", help="per player: units, buildings, upgrades, losses, the game's statistics")
    s.add_argument("file")
    u = sub.add_parser("upgrades", help="the upgrade list of every nation, from your game folder (~45 s)")
    u.add_argument("game_dir")
    u.add_argument("--nation", type=int)
    args = ap.parse_args(argv)
    if args.cmd == "dump":
        dump(args.file, args.json)
    elif args.cmd == "summary":
        summary(args.file)
    else:
        upgrades_cmd(args.game_dir, args.nation)


if __name__ == "__main__":
    sys.exit(main())
