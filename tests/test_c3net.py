import gzip
import json
import struct

import pytest

from c3net import lobby, recording, room, stream, upgrades
from c3net.__main__ import main


def rec(owner, section, body):
    """A record block: 00 03 <owner> <section> 00 <body> 01."""
    return b"\x00\x03" + bytes([owner, section, 0]) + body + b"\x01"


def s16(text):
    return struct.pack("<H", len(text)) + text.encode("latin-1")


def sync(units, key=0x0102AB):
    """A sync block: [(uid, tag or None, target or None, (x, z) or None)]."""
    out = bytes([0x09]) + key.to_bytes(3, "little") + struct.pack("<I", len(units))
    for uid, tag, target, pos in units:
        flags = (0x08 if tag is not None else 0) | (0x01 if target else 0) | (0x02 if pos else 0)
        out += uid.to_bytes(3, "little") + bytes([flags])
        if tag is not None:
            out += struct.pack("<I", tag)
        if target:
            out += target.to_bytes(3, "little")
        if pos:
            out += struct.pack("<ff", *pos)
    return out


# ------------------------------------------------------------ the match stream

def test_new_unit_and_construction():
    new = rec(0, 13, b"\x01" + s16("units") + s16("musketeer") + struct.pack("<ffiii", 1.0, 2.0, 10800, 11111, 25))
    construct = rec(1, 33, struct.pack("<Bi", 1, 20) + s16("swibar") + struct.pack("<ffBi", 5.0, 6.0, 1, 2)
                    + struct.pack("<ii", 11200, 11201))
    a, b = stream.parse(new + construct)
    assert (a.owner, a.name, a.fields["base"], a.fields["uid"]) == (0, "ReadNew", "musketeer", 11111)
    assert (b.owner, b.name, b.fields["cid"], b.fields["sid"], b.fields["uids"]) == (1, "ReadConstruct", 20, "swibar",
                                                                                     [11200, 11201])


def test_stats_layout():
    # player 0: gathered food 900 and gold 120; units cost 50 food; player 1: gathered wood 300
    m0 = 0b11 | (1 << (12 + 1)) | (1 << (12 + 2)) | (1 << (12 + 4))
    m1 = 1 << 1
    body = struct.pack("<ii", m0, m1) + struct.pack("<iiiI", 900, 0, 120, 50) + struct.pack("<iiii", 0, 300, 0, 0)
    [r] = stream.parse(rec(stream.OWNER_PROGRESS, 10, body))
    assert r.name == "ReadStats"
    assert r.fields["players"][0] == {"total": {"food": 900, "wood": 0, "gold": 120}, "units": {"food": 50}}
    assert r.fields["players"][1]["total"]["wood"] == 300


def test_resources_on_hand_are_inverted():
    body = struct.pack("<H", 0b1) + bytes([0b110, 0b100]) + struct.pack("<i", ~4300) + bytes([0x81])
    [r] = stream.parse(rec(stream.OWNER_PROGRESS, 8, body))
    assert r.fields["players"][0] == {"food": ("abs", 4300), "wood": ("delta", -1)}


def test_orders_hit_points_and_moves():
    attack = rec(1, 23, struct.pack("<iiBBi", 2, 777, 1, 0, 2) + struct.pack("<ii", 11, 12))
    patrol = rec(1, 23, struct.pack("<iiBBi", 5, 0, 0, 0, 1) + struct.pack("<ffi", 1.5, -2.5, 13))
    hp = rec(0, 61, struct.pack("<i", 2) + struct.pack("<iBi", 11, 1, 80) + struct.pack("<iB", 12, 0))
    move = rec(0, 11, struct.pack("<ffBBii", 0.0, 1.0, 0, 1, 0, 1) + struct.pack("<iff", 11, 3.0, 4.0)
               + struct.pack("<HB", 7, 0))
    a, p, h, m = stream.parse(attack + patrol + hp + move)
    assert (a.fields["type"], a.fields["target"], a.fields["uids"]) == (2, 777, [11, 12])
    assert stream.ORDER_TYPES[a.fields["type"]] == "attackobj"
    assert (p.fields["x"], p.fields["z"], p.fields["uids"]) == (1.5, -2.5, [13])
    assert h.fields["hp"] == [(11, 80), (12, None)]
    assert m.fields["units"] == [(11, 3.0, 4.0)] and (m.fields["squad"], m.fields["squad_player"]) == (7, 0)


def test_sync_block():
    death = stream.TAG_DEATH | stream.TAG_VISUAL_NONE
    [s] = stream.parse(sync([(11111, death, None, None), (11112, stream.TAG_ATTACK | stream.TAG_NONE, 11111, (7.5, -1.0))]))
    assert isinstance(s, stream.Sync) and s.key == 0x0102AB
    assert s.units[0] == {"uid": 11111, "tag": death}
    assert s.units[1] == {"uid": 11112, "tag": stream.TAG_ATTACK | stream.TAG_NONE, "target": 11111, "x": 7.5, "z": -1.0}


def test_unknown_layout_is_skipped_to_the_next_block():
    unknown = rec(0, 59, bytes([5, 0, 0, 0, 1, 0, 0, 0, 0, 0]))  # ReadSync: depends on the reader's state
    peace = rec(0, 57, b"\x01")
    blocks = list(stream.parse(unknown + sync([(1, 0, None, None)]) + peace))
    assert [(type(b).__name__, getattr(b, "name", None)) for b in blocks] == [
        ("Record", "ReadSync"), ("Sync", None), ("Record", "ReadPeaceTime")]
    assert blocks[0].fields is None
    assert list(stream.parse(b"\x05garbage")) == []


def test_truncated_record():
    new = rec(0, 13, b"\x01" + s16("units") + s16("musketeer"))[:-6]
    assert [b.fields for b in stream.parse(new)] == [None]


# ------------------------------------------------------------ lobby and room

def test_frames_and_parser_trees():
    tree = lobby_tree("", "", [lobby_tree("*", "", [lobby_tree("id", "1"), lobby_tree("ind", "0"), lobby_tree("res", "1")]),
                               lobby_tree("*", "", [lobby_tree("id", "0"), lobby_tree("ind", "1"), lobby_tree("res", "2")])])
    data = lobby.frame(0x0032, struct.pack("<I", 13) + tree, 1, 0) + lobby.frame(0x01B7, struct.pack("<Ii", 1, 2), 1)
    f1, f2 = lobby.read_frames(data)
    assert f1.name == "LAN_PARSER" and f2.name == "SERVER_SESSION_CLSCORE"
    parser_id, parsed = lobby.parser_payload(f1.payload)
    assert lobby.PARSERS[parser_id] == "LAN_GAME_SESSION_RESULTS"
    assert lobby.results(parsed) == [{"id": 1, "slot": 0, "result": "win"}, {"id": 0, "slot": 1, "result": "lose"}]
    assert lobby.clscore(f2.payload) == (1, 2)


def lobby_tree(key, value="", children=()):
    z = lambda t: struct.pack("<I", len(t)) + t.encode()  # noqa: E731
    return z(key) + z(value) + struct.pack("<I", len(children)) + b"".join(children)


REAL_SYNC = "1,20,0,0,0|0,24,0,1|0|0|0|0|0|0|x|x|x|0|0|0|3|2|1|0|0|0|1|2|0|0|0|1|0|0|2|1|-1|0"


def test_room_datasync():
    d = room.datasync(REAL_SYNC)
    kinds = [(s["slot"], s["kind"]) for s in d["slots"] if s["kind"] != "empty"]
    assert kinds == [(0, "player"), (1, "computer"), (8, "closed"), (9, "closed"), (10, "closed")]
    assert d["slots"][1]["difficulty"] == 0 and d["slots"][1]["cid"] == 24  # 24+: a random nation
    assert room.describe(d)["relief"] == "highlands" and room.describe(d)["peace_time"] == "20 min"
    assert d["battle"] == -1
    assert room.datasync("1|1|1|3|0|0") is None


def test_game_name_and_lobby_status():
    assert room.game_name('"1x1 no rush"\t""\t03EEB') == {"name": "1x1 no rush", "password": "", "kind": "0",
                                                           "checksum": "3EEB"}
    st = room.lobby_status("1|1|1|3|0|0")
    assert (st["exists"], st["humans"], st["computers"], st["closed"]) == (True, 1, 1, 3)
    assert room.decode_text("Козак".encode("cp1251")) == "Козак" and room.decode_text("Козак".encode()) == "Козак"


def test_upgrade_ids_meaning():
    assert upgrades.meaning("ukraca.12") == ("building", "aca", 12)
    assert upgrades.meaning("engcen.1") == ("building", "cen", 1)
    assert upgrades.meaning("eurgol.2") == ("building", "gol", 2)
    assert upgrades.meaning("engbar.musketeer.2.4") == ("training", "musketeer", "defense", 4)
    assert upgrades.meaning("null") is None


# ------------------------------------------------------------ recordings and the command line

def recording_bytes():
    entries = []

    def add(ms, code, frm, payload):
        entries.append(struct.pack("<IIHII", ms, len(payload), code, frm, 0) + payload)

    add(0, 0xFFFF, 1, json.dumps({"ev": "create", "id": 1}).encode())
    add(10, 0x01BB, 1, struct.pack("<I", 100) + lobby_tree("", "", [lobby_tree("s", REAL_SYNC)]))
    add(1000, 0x04B0, 1, rec(stream.OWNER_PROGRESS, 10, struct.pack("<ii", 0, 0)))
    add(2000, 0x04B0, 1, rec(0, 33, struct.pack("<Bi", 1, 20) + s16("swicen") + struct.pack("<ffBi", 5.0, 6.0, 1, 0))
        + rec(0, 13, b"\x01" + s16("units") + s16("peaaus") + struct.pack("<ffiii", 1.0, 2.0, 1, 500, 2)))
    add(3000, 0x04B0, 1, sync([(500, stream.TAG_DEATH, None, None)]))
    return recording.MAGIC + b"".join(entries)


def test_recording_reader(tmp_path):
    data = recording_bytes()
    entries = list(recording.read(gzip.compress(data)))
    assert entries[0].marker() == {"ev": "create", "id": 1} and entries[-1].code == 0x04B0
    assert len(list(recording.read(data[:-3]))) == len(entries) - 1  # a truncated tail
    with pytest.raises(recording.NotARecording):
        list(recording.read(b"nope"))


def test_command_line(tmp_path, capsys):
    path = tmp_path / "m.rec"
    path.write_bytes(recording_bytes())
    main(["summary", str(path)])
    out = json.loads(capsys.readouterr().out)
    me = out["players"]["0"]
    assert me["nation"] == 20 and me["units_made"] == {"peaaus": 1} and me["units_lost"] == 1
    assert me["buildings_placed"] == {"swicen": 1}
    main(["dump", "--json", str(path)])
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[1]["parser"] == "LAN_ROOM_SERVER_DATASYNC" and rows[3]["blocks"][1]["record"] == "ReadNew"


def test_dmscript_interpreter():
    from c3net import dmscript
    src = """
    procedure AddUpgrade(var country : TCountry; const upgid : String; var ind : Integer);
    begin
       country.upgrade[ind].id := upgid;
       ind := ind+1;
    end;
    procedure Init(var country : TCountry; var csid : String);
    begin
       const _eng = 2;
       var cid : Integer = 2;
       var ind : Integer;
       type TStruct = class
          place : String;
       end;
       var s : TStruct;
       s.place := csid+'bar';
       AddUpgrade(country, 'null', ind);
       case cid of
          _eng, 3 : AddUpgrade(country, csid+'cen.1', ind);
       else
          AddUpgrade(country, 'x', ind);
       end;
       var i : Integer;
       for i:=1 to 3 do
       if (i<>2) then
       AddUpgrade(country, s.place+'.musketeer.1.'+IntToStr(i), ind);
       country.count := ind;
    end;
    """
    interp = dmscript.Interpreter([src])
    country = dmscript.Box()
    sid = dmscript.Cell("eng")
    interp.invoke(interp.globals.find("init"), [lambda: country, sid.get],
                  [dmscript.RefCell(lambda: country, lambda v: None), dmscript.RefCell(sid.get, sid.set)])
    ids = [country.fields["upgrade"].items[i].fields["id"] for i in range(country.fields["count"])]
    assert ids == ["null", "engcen.1", "engbar.musketeer.1.1", "engbar.musketeer.1.3"]
    consts = dmscript.read_constants("gc_a = 2;\ngc_b = gc_a shl 3;\n")
    assert consts == {"gc_a": 2, "gc_b": 16}


def test_gui_records():
    # the host's pause, as in a recorded match: 00 04, section 66 (u16), the Boolean, 01; a record after it
    payload = bytes.fromhex("000442000101") + rec(stream.OWNER_PROGRESS, 8, b"\x01\x00\x02\x02\x81")
    blocks = list(stream.parse(payload))
    assert blocks[0] == stream.Record(stream.OWNER_GUI, 66, "ReadPause", {"pause": 1})
    assert blocks[1].name == "ReadRes" and blocks[1].fields["players"] == {0: {"food": ("delta", -1)}}
    # an unknown GUI section is skipped to the next block
    blocks = list(stream.parse(bytes.fromhex("0004630007070701") + rec(0, 13, b"")))
    assert blocks[0] == stream.Record(stream.OWNER_GUI, 99, "?99", None) and blocks[1].owner == 0
