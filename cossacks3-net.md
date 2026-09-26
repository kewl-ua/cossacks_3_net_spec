# Cossacks 3 Network Protocol and Match Stream

| | |
|---|---|
| Revision | 0.1, 2026-09-26 |
| Game version | 2.2.3 (core 1.0.0.7), unmodded scripts (room checksum `3EEB`) |
| Status | Reverse-engineered, verified on recorded matches |
| Reference implementation | [`c3net`](c3net/) (Python, this repository) |

This document describes what travels between Cossacks 3 and its lobby server
during an online game: the lobby frames, the room data, and — the main part —
the **match stream** in which the game host sends the state of the match to
the other players. With it, a lobby server can follow a match as it happens:
units trained and lost, buildings, upgrades, resources gathered and spent,
positions, fights and the result, with no mod on the players' side.

Not affiliated with GSC Game World. No game files are included here; the
layouts were read from the game's own script handlers (`Write*` / `Read*`
sections of the `.aix` state machines, `dmscript.global` constants) and
checked against real matches.

## Contents

1. [Introduction](#1-introduction)
2. [Architecture](#2-architecture)
3. [Data types](#3-data-types)
4. [Lobby protocol](#4-lobby-protocol)
5. [Rooms](#5-rooms)
6. [The match stream](#6-the-match-stream)
7. [What the data means](#7-what-the-data-means)
8. [QLREC1 recordings](#8-qlrec1-recordings)
9. [Constants](#9-constants)
- [Appendix A. Annotated examples](#appendix-a-annotated-examples)
- [Appendix B. Reference implementation](#appendix-b-reference-implementation)
- [Appendix C. Revision history](#appendix-c-revision-history)

---

## 1. Introduction

### 1.1 Scope

Covered:

- the lobby frame and the messages a match goes through (create, update,
  lock, parsers, scores, close);
- room data: the game name, the lobby status string, the room datasync
  (slots, map and game settings), results;
- the match stream (`LAN_RECORD`, code `0x04B0`): its two block types and
  the layout of every record the host sends;
- what the values mean, including pitfalls in the game's own statistics.

Not covered: the lobby's account and social messages (clans, friends, chats —
see [Sich](https://github.com/3skcassoc/sich) `xpacket.lua`, which parses all
of them), saved games, map generation, historical battles and scenarios.

### 1.2 Verification

- Every record layout in section 6 comes from the game's handler that reads
  it and was checked on recorded matches of version 2.2.3: all blocks of
  every recording decode, each record ending exactly at its end marker.
- The recordings were one human against computer players. Two-human matches
  (client requests, 6.1) follow the same scripts but are not yet verified.
- The statistics (section 7) were compared with the game's end screen. The
  numbers balance to the unit once the rules in 7.4 are applied.

### 1.3 Conventions

- All integers are **little-endian**.
- Offsets and sizes are in bytes.
- `0x` numbers are hexadecimal.
- Field names in `snake_case` are this document's; names in `CamelCase` or
  with a `gc_` / `g` prefix are the game's own.
- "Slot" is a player's index in the room (`gMap.players[slot]`, 0–11).
  "Id" (`lanid`) is a player's lobby account number.

### 1.4 Terms

| Term | Meaning |
|---|---|
| Lobby server | The server the game connects to for accounts and rooms. The original is gone; [Sich](https://github.com/3skcassoc/sich) is a compatible open implementation. |
| Room / session | A game lobby that becomes a match. The player who created it is the **master**. |
| Host | The room master once the match starts. It runs the simulation. |
| Client | Every other player (and spectators). |
| Machine | A script state machine of the game (`.aix` file). Each player has a *global* machine; there is one *progress* machine. |
| Section | A named block of script in a machine; records refer to it by its index. |
| Record | One message of the match stream: "run this section with this data". |
| Sync block | The engine's own message with unit states and positions. |
| uid | A game object's unique id: units, buildings, fields, trees. |

---

## 2. Architecture

```
          TCP 31523                          TCP 31523
 client ───────────────┐                ┌─────────────── client
                       │  lobby server  │
 host ─────────────────┘  (relays room  └─────────────── spectator
                           traffic)
```

- The game connects to the lobby server at the address in
  `data/resources/servers.dat` (`* = host:port`, port 31523). Nothing is
  peer-to-peer: in a room, every message goes to the server, which relays it
  to the other room members.
- **The game is host-authoritative, not lockstep.**
  - The host runs the simulation. It sends each state change to the others
    as a *record* ("run section `ReadNew` of player 3's machine with this
    data") or as a *sync block* (unit states and positions).
  - A client that acts (hires, builds, orders) sends its own record to the
    host as a request. The host checks it, applies it and broadcasts the
    result.
- So one observer — the lobby server — sees the whole match: every unit,
  building, upgrade, resource and death, as the host decided it.

---

## 3. Data types

### 3.1 Lobby payloads

| Type | Size | Encoding |
|---|---|---|
| `u8` | 1 | unsigned |
| `bool` | 1 | 0 false, anything else true |
| `u16` | 2 | unsigned |
| `u32` / `i32` | 4 | unsigned / two's complement |
| `str8` | 1 + n | `u8` length, then the bytes |
| `str16` | 2 + n | `u16` length, then the bytes |
| `str32` | 4 + n | `u32` length, then the bytes |
| `version` | 1 + n | `str8` like `"1.0.0.7"`; Sich packs it as one byte per part: `0x01000007` |
| `datetime` | 8 | IEEE double, days since 1899-12-30 (Delphi `TDateTime`) |
| `parser` | ≥ 12 | a parser tree, 3.3 |

Text in the lobby is the game's text as typed: Windows-1251 or UTF-8
depending on the client. Decode as UTF-8, and fall back to Windows-1251.

### 3.2 Match stream bodies

Record bodies (6.4) use the game's `RecordCustomWrite*` encoding:

| Type | Size | Encoding |
|---|---|---|
| `Boolean` / `Byte` | 1 | |
| `Word` | 2 | `u16` |
| `Integer` | 4 | `i32` |
| `Float` | 4 | IEEE single |
| `String` | 2 + n | `u16` length, then the bytes (object codes are ASCII) |

### 3.3 Parser trees

The game's key/value trees (`Parser*` functions) travel as nodes:

| Offset | Type | Field |
|---|---|---|
| 0 | `str32` | key |
| … | `str32` | value |
| … | `u32` | number of children |
| … | node × n | children, recursively |

A message carrying a tree starts with a `u32` **parser id** (section 4.5).
The root usually has an empty key and value.

```
0d 00 00 00                    parser id 13 (LAN_GAME_SESSION_RESULTS)
00 00 00 00 00 00 00 00        root: key "", value ""
02 00 00 00                    2 children
  01 00 00 00 2a 00 00 00 00   key "*", value ""
  03 00 00 00                  3 children
    02 00 00 00 69 64  01 00 00 00 31  00 00 00 00    id = "1"
    03 00 00 00 69 6e 64  01 00 00 00 30  00 00 00 00 ind = "0"
    03 00 00 00 72 65 73  01 00 00 00 31  00 00 00 00 res = "1"
  ...
```

---

## 4. Lobby protocol

### 4.1 Frame

Every message, both ways, is a frame:

| Offset | Size | Type | Field |
|---|---|---|---|
| 0 | 4 | `u32` | payload length |
| 4 | 2 | `u16` | message code |
| 6 | 4 | `u32` | sender's id (0 before login, or from the server) |
| 10 | 4 | `u32` | recipient's id (0: the server / everyone) |
| 14 | n | | payload |

Codes `SERVER_*` go from a client to the server, and `USER_*` from the
server to a client. The server relays `LAN_*` codes between the members of a
room, unchanged.

> **Security.** The protocol has no encryption or hashing: `SERVER_REGISTER`
> and `SERVER_AUTHENTICATE` carry the e-mail and the password in clear text.
> Anyone who can read the traffic, including the server's operator and its
> logs, sees them. A server should not log payloads of these messages. Players
> should not reuse a password they use elsewhere.

### 4.2 Message codes

The codes a match uses. Sich names every code of the protocol.

| Code | Name | Payload |
|---|---|---|
| `0x0032` | `LAN_PARSER` | `u32` parser id, `parser`: game messages inside a match (4.5) |
| `0x0191` | `PING` | |
| `0x0194` | `SERVER_SESSION_MSG` | `str8` text: room chat |
| `0x0198` | `SERVER_REGISTER` | `version` core, `version` data, `str8` × 6 (e-mail, password, cd key, nickname, country, info) |
| `0x019A` | `SERVER_AUTHENTICATE` | `version`, `version`, `str8` e-mail, `str8` password, `str8` cd key |
| `0x019C` | `SERVER_SESSION_CREATE` | `u32` max players, `str8` password, `str8` game name (5.1), `str8` map name (5.2), `u32` money, `bool` fog of war, `u8` battlefield |
| `0x019E` | `SERVER_SESSION_JOIN` | `u32` master's id |
| `0x01A0` | `SERVER_SESSION_LEAVE` | — |
| `0x01A2` | `SERVER_SESSION_LOCK` | `u32` n, n × (`u32` id, `u8` lobby team): the match starts |
| `0x01AA` | `SERVER_SESSION_UPDATE` | `str8` game name, `str8` map name, `u32` money, `bool` fog, `u8` battlefield |
| `0x01AB` | `SERVER_SESSION_CLIENT_UPDATE` | `u8` lobby team (5.4) |
| `0x01AF` | `SERVER_SESSION_CLOSE` | — (the host closes the room at the end) |
| `0x01B0` | `USER_SESSION_CLOSE` | `datetime`, `u32` n, n × (`u32` id, `u32` score) |
| `0x01B7` | `SERVER_SESSION_CLSCORE` | `u32` id, `i32` result code (5.6) |
| `0x01BB` | `SERVER_SESSION_PARSER` | `u32` parser id, `parser`: room messages to the master |
| `0x01BC` | `USER_SESSION_PARSER` | `u32` parser id, `parser`, `u32` |
| `0x0456` | `LAN_DO_START` | |
| `0x0460` | `LAN_DO_READY` | |
| `0x04B0` | `LAN_RECORD` | the match stream (section 6) |

### 4.3 A match, message by message

1. The master sends `SERVER_SESSION_CREATE`. Players `SERVER_SESSION_JOIN`.
2. In the room, the master broadcasts the room state as parser 100
   (`LAN_ROOM_SERVER_DATASYNC`, 5.3) whenever it changes. Clients send their
   own changes as parser 102. `SERVER_SESSION_UPDATE` keeps the room's lobby
   entry current (5.2).
3. The master sends `SERVER_SESSION_LOCK`: the match starts.
   `LAN_DO_START`, `LAN_DO_READY` and parsers 1–9 follow while the map loads.
4. The match: `LAN_RECORD` frames (section 6), chat (`SERVER_SESSION_MSG`),
   parser 10 when a player surrenders, and parser 14 when a client asks for
   a resync.
5. The host sends parser 13 with the results (5.5), `SERVER_SESSION_CLSCORE`
   per player (5.6), and `SERVER_SESSION_CLOSE`.

### 4.4 Relaying

- The server (as Sich implements it) forwards `LAN_*` frames to the frame's
  recipient, or to every other room member when the recipient is 0.
- It forwards `SERVER_SESSION_PARSER` to the master.
- A server that wants to observe matches only needs to read what it relays.

### 4.5 Parser ids

| Id | Name | Used for |
|---|---|---|
| 1–9 | `LAN_GENERATE` … `LAN_GAME_START` | loading and start handshake |
| 10 | `LAN_GAME_SURRENDER` | a player surrenders |
| 11 | `LAN_GAME_SURRENDER_CONFIRM` | |
| 12 | `LAN_GAME_SERVER_LEAVE` | the host leaves |
| 13 | `LAN_GAME_SESSION_RESULTS` | results (5.5) |
| 14 | `LAN_GAME_SYNC_REQUEST` | a client lost sync |
| 15 | `LAN_GAME_SYNC_DATA` | |
| 16 | `LAN_GAME_SYNC_GAMETIME` | the host's game time |
| 17 | `LAN_GAME_SYNC_ALIVE` | |
| 100 | `LAN_ROOM_SERVER_DATASYNC` | the room state, from the master (5.3) |
| 101 | `LAN_ROOM_SERVER_DATACHANGE` | |
| 102 | `LAN_ROOM_CLIENT_DATACHANGE` | a client's nation / team / colour |
| 103 | `LAN_ROOM_CLIENT_LEAVE` | |
| 200–206 | `LAN_MODS_*` | mod sync and checksums |
| 300 | `LAN_ADVISER_CLIENT_DATACHANGE` | |

---

## 5. Rooms

### 5.1 Game name

`gamename` has three tab-separated, double-quoted fields:

```
"<room name>"<TAB>"<password>"<TAB><kind><checksum>
```

| Part | Meaning |
|---|---|
| kind | `0` a regular room, `r` a rating (quick play) room, `h` a historical battle |
| checksum | 4 hex digits of the MD5 of the game's script library: `3EEB` for the unmodded 2.2.3 scripts. Another value means a room with script mods. |

### 5.2 Lobby status ("map name")

The `mapname` field of the session messages is **not a map**. The master
fills it with the room's status for the lobby list:

```
<flags>|<humans>|<computers>|<closed slots>|<ping>|<rank>[|<id 1>|<id 2>|<search time>|<quick play state>]
```

- `flags`: bit 0 set means the room exists; bit 1 full; bit 2 locked.
- The last four fields appear in quick-play rooms only.
- Example: `1|1|1|3|0|0` is one human, one computer and three closed slots.

It is `0` right after the room is created. The map itself is in the
datasync.

### 5.3 Room datasync (parser 100)

The master sends the whole room as one string. It is the value of the key
`s` under the tree root:

```
<slot 0>|<slot 1>|...|<slot 11>|<season>|<terrain>|<relief>|<start resources>|<mines>|<map size>|<12 additional settings>|<battle>|<stage>
```

**Slots** are the room's 12 player places (`gMap.players`, in order: this is
the *slot* number used everywhere else):

| Form | Meaning |
|---|---|
| `id,cid,team,color,ready` | a player: lobby id, nation (9.1; 24 and more: random), team (0 = none), colour, ready flag |
| `-difficulty,cid,team,color` | a computer: difficulty 0 normal, 1 hard, 2 very hard, 3 impossible (4 fields, first ≤ 0) |
| `x` | a closed slot |
| `0` | an empty slot |

Spectators take a slot with nation `-2`.

**Map generator:**

| # | Setting | Values |
|---|---|---|
| 1 | season | 0 summer, 1 winter, 2 desert |
| 2 | terrain | 0 land, 1 mediterranean, 2 peninsulas, 3 islands, 4 continents, 5 continent, 6 lakes, 7 coast, 8 rivers, 9 no water |
| 3 | relief | 0 plain, 1 hills, 2 mountains, 3 highlands, 4 plateau, 5 desert |
| 4 | starting resources | 0 normal, 1 rich, 2 thousands, 3 millions |
| 5 | mines | 0 few, 1 medium, 2 many |
| 6 | map size | 3 small, 0 normal, 1 large (2×), 2 huge (4×) |

**Additional settings** (`gMap.settings.additional`):

| # | Setting | Values |
|---|---|---|
| 1 | starting units | 0 default, 1 army, 2 large army, 3 huge army, 4 many peasants, 5 different nations, 6 towers, 7 cannons, 8 cannons and howitzers, 9 18th c. barracks, 10 17th c. barracks, 11 village, 12 log cabins, 13 union |
| 2 | balloons | 0 default, 1 none, 2 with balloons |
| 3 | cannons | 0 default, 1 no cannons, towers and walls, 2 expensive cannons |
| 4 | peace time | 0 none, 1 10 min, 11 15 min, 2 20 min, 3 30, 4 45, 5 60, 6 90 min, 7 2 h, 8 3 h, 9 4 h |
| 5 | 18th century | 0 default, 1 never, 2 from the start |
| 6 | capture | 0 default, 1 no peasants, 2 no peasants and centres, 3 cannons only |
| 7 | market and diplomacy | 0 default, 1 no diplomatic centre, 2 no market, 3 neither, 4 expensive mercenaries |
| 8 | allies | 0 default, 1 side by side |
| 9 | autosave | |
| 10 | population limit | 0 none, 1–8: 500, 750, 1000, 1500, 2200, 3000, 5000, 8000 |
| 11 | game speed | 0 normal, 1 fast, 2 very fast, -1 adjustable |
| 12 | adviser | 0 default, 1 none |

**Battle:** the historical battle's index, or -1 on a random map. **Stage:**
the battle's stage.

### 5.4 Teams

- `SERVER_SESSION_LOCK` and `SERVER_SESSION_CLIENT_UPDATE` carry a *lobby
  team*. In regular rooms it is a placeholder: the creator sets
  `gc_MaxPlayerCount + 1` (13), and the others 0.
- In rating rooms it is the matchmaking side.
- The team a player picked in the room is in the datasync (5.3); there, 0
  means no team.

### 5.5 Results (parser 13)

The host sends one child per participant:

| Key | Value |
|---|---|
| `id` | lobby id; **0 for a computer player** |
| `ind` | the slot |
| `res` | 1 win, 2 lose (`gc_player_victorystate_*`; 0 none) |

When a player's state changes, the host may send parser 13 again. In a team
game, a player can lose before the match ends.

### 5.6 `SERVER_SESSION_CLSCORE`

A **result code**, not points. The host sends it at the end of a match that
is a rating match or lasted over 10 minutes of game time, one per human
(`_misc_LanCloseSessionSetScores`):

| Room | Winner | Loser |
|---|---|---|
| rating | +1 (−1 if they were the first to leave) | −1 |
| regular | +2 (−2 if they were the first to leave) | not sent |

---

## 6. The match stream

### 6.1 Who sends what

- **The host** broadcasts `LAN_RECORD` frames with records and sync blocks.
  Only the host sends sync blocks and progress-machine records: that tells
  an observer who the host is.
- **Clients** send the records of their own actions to the host (owner =
  their slot; `server` field false where there is one). These are requests;
  the host's broadcast that follows is what happened. *(From the game's
  scripts; the recorded matches this revision was checked on were against
  computer players, where only the host sends records.)*
- The stream carries **no game clock**. Use the arrival time, and take the
  first `LAN_RECORD` of the match as game time 0 (the map has loaded).

### 6.2 Payload

A `LAN_RECORD` payload is a sequence of blocks with no count or length in
front. The first byte of a block tells its type:

| First byte | Block |
|---|---|
| `0x00` | record (6.3) |
| `0x09` | sync block (6.7) |

### 6.3 Record block

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | `0x00` |
| 1 | 1 | `0x03` (always, in version 2.2.3) |
| 2 | 1 | **owner**: the machine the record runs in |
| 3 | 2 | **section**: `u16`, the index of the `Read*` section in that machine's `.aix` file |
| 5 | n | body: what the matching `Write*` section wrote (6.5) |
| 5 + n | 1 | `0x01`: end of record |

**Owner:**

| Owner | Machine |
|---|---|
| 0–11 | the player in that slot: `data/scripts/units/global.aix` |
| 12 | `gc_playerind_env`: the environment (fields, trees; seen as a *field* in bodies) |
| 13 | `gc_playerind_misc` |
| 14 (`0x0E`) | `gc_playerind_progress`: `data/scripts/progress/progress.aix` |
| 15 | `gc_playerind_pool` |

The body has **no length**. To find the next block you must know the
section's layout; 6.8 describes how to recover when you do not.

### 6.4 Section indexes

A machine's sections are numbered in the order of the `.aix` file,
separators included. Only the `Read*` sections appear in records.

**`global.aix`** (players):

| Index | Section | Index | Section |
|---|---|---|---|
| 6 | `ReadSquadNew` | 39 | `ReadLeave` |
| 8 | `ReadSquadListAction` | 41 | `ReadProj` |
| 11 | `ReadMove` | 43 | `ReadProjFree` |
| 13 | `ReadNew` | 45 | `ReadNewP` |
| 15 | `ReadFree` | 47 | `ReadStop` |
| 17 | `ReadDeath` | 49 | `ReadTrade` |
| 19 | `ReadPlayer` | 51 | `ReadWall` |
| 21 | `ReadRally` | 53 | `ReadGate` |
| 23 | `ReadOrder` | 55 | `ReadFreeList` |
| 25 | `ReadUpgrade` | 57 | `ReadPeaceTime` |
| 27 | `ReadProduce` | 59 | `ReadSync` |
| 29 | `ReadSearch` | 61 | `ReadSyncUnitsParams` |
| 31 | `ReadStand` | 64 | `ReadPackage` |
| 33 | `ReadConstruct` | 70 | `ReadTradeResources` |
| 35 | `ReadApply` | | |
| 37 | `ReadLeaveOrder` | | |

**`progress.aix`**:

| Index | Section |
|---|---|
| 8 | `ReadRes` |
| 10 | `ReadStats` |
| 12 | `ReadScenario` |
| 15 | `ReadLanSyncData` |

### 6.5 Record layouts

Types are those of 3.2. `uids` fields are an `Integer` count followed by
that many `Integer` uids.

A leading `server` Boolean is true when the host wrote the record, and false
in a client's request.

#### 6.5.1 `ReadRes` (progress 8) — resources on hand

| Type | Field |
|---|---|
| `Word` | mask of players present (bit = slot) |
| per player in the mask: | |
| `Byte` | `changed`: bit *r* set if resource *r* changed (9.2) |
| `Byte` | `compressed`: bit *r* set if the change is sent as one byte |
| per resource in `changed`: | `Byte` change if `compressed`, else `Integer` new amount |

- The game keeps amounts **bit-inverted** (`setres = not amount`). An
  `Integer` value *v* is the amount `~v`.
- A one-byte change *b* is `+b` for *b* < 128 and `−(b − 128)` otherwise:
  new amount = old amount + change.
- Sent frequently, and only for what changed.

#### 6.5.2 `ReadStats` (progress 10) — the game's statistics

| Type | Field |
|---|---|
| `Integer` | `mask1` |
| `Integer` | `mask2` |
| per player with bit *slot* in `mask1`, per group, per resource *r* = 1…6 whose bit is set: | `Integer` running total |

The groups and their bits (P = 12, the player bits of `mask1`):

| Group | Mask | Bit of resource *r* | Game variable |
|---|---|---|---|
| total | `mask1` | P + *r* | `stat.restotal`: gathered |
| upgrades | `mask1` | P + 6 + *r* | `stat.resonupgrade`: spent on upgrades |
| mines | `mask1` | P + 12 + *r* | `stat.resonmines`: spent on mine upgrades and workers |
| units | `mask2` | *r* | `stat.resonunits`: spent on units (7.4!) |
| buildings | `mask2` | 6 + *r* | `stat.resonbuildings` |
| life | `mask2` | 12 + *r* | `stat.resonlife`: army upkeep |
| buy | `mask2` | 18 + *r* | `stat.resbuy`: bought at the market |
| sell | `mask2` | 24 + *r* | `stat.ressell`: sold at the market |

- A bit is set when any player has a non-zero value; every player in
  `mask1` then sends that value.
- Sent about every 20 s, and at the end.

#### 6.5.3 `ReadLanSyncData` (progress 15) — workers and squads

| Type | Field |
|---|---|
| `Word` | mask of players with economy data |
| per player in the mask: | |
| `Byte` | fields present: bit 0 idle peasants, 1 idle mines, 2–7 workers on food, wood, stone, gold, iron, coal |
| if non-zero: `Byte` | the slot |
| `Word` per bit set | the values |
| then, for each of the 12 slots: | |
| `Integer` | number of squads changed |
| per squad: `Integer` uid, `Boolean` hold; if not hold: `Word` time | |

#### 6.5.4 `ReadNew` (13) — a unit appears

| Type | Field |
|---|---|
| `Boolean` | server |
| `String` | race: `units` |
| `String` | base: the unit's code (`musketeer`, `peaaus`…) |
| `Float`, `Float` | x, z |
| `Integer` | cid: **the producing building's uid** if > 0, else the nation |
| `Integer` | uid of the new unit |
| `Integer` | the player's number of objects after it |

- The owner is the record's owner.
- A player's starting units are **not** announced: they only appear in sync
  blocks (7.1).

#### 6.5.5 `ReadNewP` (45) — a field is sown

| Type | Field |
|---|---|
| `Boolean` | server |
| `String`, `String` | race `env`, base `field` |
| `Float` × 3 | x, z, roll |
| `Integer` | player: 12 (the environment owns fields) |
| `Integer` | id |
| `Integer` | uid |
| `Integer` | number of objects |

The sowing player is the record's owner. One field per record; a sowing
order produces a burst of them.

#### 6.5.6 `ReadConstruct` (33) — a building is placed

| Type | Field |
|---|---|
| `Boolean` | server |
| `Integer` | nation (the builder's) |
| `String` | building code (`engcen`, `eurmil`…) |
| `Float`, `Float` | x, z |
| `Boolean` | clear the builders' orders |
| uids | builders |

- The building's own uid is not sent: each client creates the building
  itself.
- It shows up in the next sync blocks at (x, z); 7.1 explains how to find
  it.

#### 6.5.7 `ReadUpgrade` (25) — research starts

| Type | Field |
|---|---|
| `Boolean` | server |
| `Integer` | upgrade index (7.5) |
| `Boolean` | state: true start, false cancel |
| uids | the buildings doing it |

There is no record when the research ends.

#### 6.5.8 `ReadProduce` (27) — hire

| Type | Field |
|---|---|
| `Integer` | member id (the unit's index in its nation, 7.1) |
| `Integer` | nation |
| `Integer` | amount: > 0 queued, < 0 cancelled |
| `Boolean` | state |
| uids | the buildings |

#### 6.5.9 `ReadOrder` (23) — an order to a target

| Type | Field |
|---|---|
| `Integer` | type (9.3): 2 attack, 3 gather, 13 enter a mine, 17 build, 18 guard, 19 repair… |
| `Integer` | target uid |
| `Boolean` | clear previous orders |
| `Boolean` | lock the target |
| `Integer` | n, the number of units |
| if type is 5 (patrol) or 6 (attack a point): `Float`, `Float` | x, z |
| `Integer` × n | the units |

#### 6.5.10 `ReadMove` (11) — a move order

A client's request to the host. **The host does not broadcast it:** it
shows in the sync blocks instead.

| Type | Field |
|---|---|
| `Float`, `Float` | direction x, z |
| `Boolean` | add to orders |
| `Boolean` | do first |
| `Integer` | mode |
| `Integer` | n |
| n × (`Integer` uid, `Float` x, `Float` z) | destinations |
| `Word` | squad uid |
| if non-zero: `Byte` | the squad's player |

#### 6.5.11 `ReadDeath` (17)

| Type | Field |
|---|---|
| `Boolean` | server |
| `Integer` | mode |
| `Float` | random key |
| uids | the objects |

Kills in combat are not sent this way; they show as a state change in the
sync blocks (6.7).

#### 6.5.12 Other records

| Section | Layout |
|---|---|
| `ReadSquadNew` (6) | `Boolean` server, `Integer` player, `String` formation code, `Integer` formation, `Integer` officer uid, `Integer` drummer uid, `Boolean` position, `Boolean` in squad, `Integer` squad, uids |
| `ReadSquadListAction` (8) | `Boolean` server, `Integer` action, `Boolean` state, `Integer` formation, squads (count + `Integer` × n), uids |
| `ReadFree` (15), `ReadProjFree` (43) | `Integer` uid, `Integer` |
| `ReadPlayer` (19) | `Integer` uid, `Boolean` capture |
| `ReadRally` (21) | `Integer` building uid, `Boolean` set, `Float` x, `Float` z |
| `ReadSearch` (29), `ReadStand` (31), `ReadLeaveOrder` (37), `ReadGate` (53) | `Boolean` server, uids |
| `ReadApply` (35) | `Integer` × 4 |
| `ReadLeave` (39) | `Boolean` server, `Integer` uid |
| `ReadProj` (41) | `Integer` uid, `Integer` target, `Boolean` use target position, `Float` × 3 from, `Integer` weapon, `Float` × 3 to, `Integer` nation, `Integer` id, `Integer` weapon index, `Float` random key, `Integer` projectile uid, `Integer` number of objects |
| `ReadStop` (47), `ReadFreeList` (55) | uids |
| `ReadTrade` (49) | `Boolean` server, `Byte` resource sold, `Byte` resource bought, `Integer` amount |
| `ReadWall` (51) | `Boolean` server, `Byte` usage, `Byte` nation, `Byte` id; `Integer` n, n × (`Byte` sprite, `Float` x, `Float` z, `Integer` uid); `Integer` number of objects; uids (builders) |
| `ReadPeaceTime` (57) | `Boolean` server |
| `ReadSyncUnitsParams` (61) | `Integer` n, n × (`Integer` uid, `Boolean` valid, if valid: `Integer` hit points) |
| `ReadPackage` (64) | `Boolean` server, `String` |
| `ReadTradeResources` (70) | `Boolean` server, `Byte` from slot, `Byte` to slot, `Byte` resource, `Integer` amount |
| `ReadSync` (59) | **Not decodable from the stream alone:** part of the layout depends on objects the reader already has. Skip it (6.8). |

### 6.6 End of record

The byte after the body is always `0x01`. A decoder should check it: a
missing `0x01` means the layout was wrong.

### 6.7 Sync block

The engine's own unit sync. It is sent by the host only.

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | `0x09` |
| 1 | 3 | unknown: changes from block to block, wraps around |
| 4 | 4 | `u32` n, the number of entries |
| 8 | … | n entries |

Entry:

| Size | Field | Present if |
|---|---|---|
| 3 | uid (24-bit) | always |
| 1 | flags | always |
| 4 | state tag (`u32`, below) | flags & `0x08` |
| 3 | target uid (24-bit) | flags & `0x01` |
| 8 | x, z (`Float` × 2) | flags & `0x02` |
| 4 | direction (`Float`) | flags & `0x04` |

- Bit `0x10` is set with `0x08` and adds no data.
- Bits `0x20`–`0x80` were never seen. Treat them as "not a sync block".

**State tag** (`gc_statetag_*`), one bit each:

| Bit | Name | Bit | Name |
|---|---|---|---|
| 0 | `essential_none`: normal life | 16 | `resource_none` |
| 1 | `essential_birth`: under construction | 17 | `resource_food` |
| 2 | `essential_death`: dead | 18 | `resource_wood` |
| 3 | `move_idle` | 19 | `resource_stone` |
| 4 | `move_walk` | 20 | `visual_none` |
| 5 | `move_turn` | 21 | `visual_stage_0` |
| 6 | `action_none` | 22 | `visual_stage_1` |
| 7 | `action_attack` (target = victim) | 23 | `visual_stage_2` |
| 8 | `action_build` | 24 | `visual_stage_3` |
| 9 | `action_extract` | 25 | `visual_hide` |
| 10 | `execute_none` | 29 | `sync_stp` |
| 11 | `execute_move` | 30 | `sync_endpoint` |
| 12 | `weapon_none` | | |
| 13–15 | `weapon_0` … `weapon_2` | | |

### 6.8 Decoding robustly

A reader that meets a section it cannot decode (`ReadSync`, or a future
one) can resynchronise:

1. Look for the next `0x01` after the header.
2. If it is followed by the end of the payload, by a record header
   (`00 03 xx xx 00`), or by a sync block that decodes completely, the
   unknown record ends at that `0x01`.
3. Otherwise try the next `0x01`.

Stop at a byte that is neither `0x00` nor `0x09`.

---

## 7. What the data means

### 7.1 Objects and their owners

| Object | Created | Owner known from |
|---|---|---|
| Hired unit | `ReadNew` | the record's owner |
| Starting unit (18 peasants by default…) | at the map start, never announced | the nearest town centre; they move from the first seconds |
| Building | `ReadConstruct` (placement) | find the uid that first appears in a sync block within ~1 unit of (x, z), at or after the placement |
| Field | `ReadNewP` | the record's owner (the uid's owner is the environment) |
| Tree, stone, map object | map | never moves |

- Unit and building **codes** are the members of the nation in
  `country.script` (`_country_AddMember`).
- The game's statistics index them as `[nation][member id]`, member id =
  the order of `_country_AddMember` calls for that nation. Index 0 is
  `null`.

### 7.2 Life cycle in sync blocks

- **Construction.** While a building is being built, its tag has
  `essential_birth` and one of `visual_stage_0` … `visual_stage_3`. When it is
  finished, `essential_birth` gives way to `essential_none`. A mill placed at
  0:02 might be finished at 0:31.
- **Death.** `essential_death` appears; the object then disappears.
- **Attack.** `action_attack` with a target uid: the attacker's latest
  victim. Credit a death to the last attacker of the victim.
- **End of match.** After the results (5.5), the game removes the losers'
  units: a burst of deaths that are not combat losses.

### 7.3 Resources on hand

- Use `ReadRes` (6.5.1), and remember the inversion.
- Starting resources appear as the first absolute values (for example 4300
  of each with "thousands").

### 7.4 The game's statistics (`ReadStats`)

These are the numbers of the game's end screen. Some behave in ways that
matter:

- **`total`** is everything gathered.
  `total − spent + bought − sold + starting resources` equals what is on hand
  (`ReadRes`).
- **`units`** (the end screen's unit spending) is **not** the price of the
  units made:
  1. **Sown fields are in it**: 5 gold each. A field is not a building, so
     `_unit_ApplyCostByID` books it as a unit.
  2. **A cancelled hire counts twice.** `_unit_CancelUnitProduction` refunds
     the player but *adds* the refund to `resonunits` (for upgrades it
     subtracts). This is a game bug.
  3. **A unit is paid when its production starts**, so units being produced
     at the end are in it.
- **`units lost`** on the end screen (`stat.killed[nation][member]`) are the
  **owner's losses**. The game counts a death for the object's owner, and
  does not record who killed it.
- **`produced`** counts a building only once it is **finished**.

### 7.5 Upgrades

`ReadUpgrade` sends an **index** into the nation's upgrade list.
`country.script` builds the list when the game starts (`_country_Init`): an
upgrade id takes the first free slot when it is first added. So:

- index 0 is `null`;
- index 1 is `<nation>cen.1`, the 18th century, for nations that have it
  (not Ukraine, Turkey, Algeria, Scotland);
- the rest follows the script: building upgrades, then unit trainings.

The order depends on the script version. Rebuild it from the installed game.
The reference implementation runs the game's own `_country_InitAll` with a
small interpreter (`c3net.upgrades`).

Upgrade ids:

| Form | Meaning |
|---|---|
| `<nation><place>.<n>` | upgrade *n* of a building: `aca` academy, `bla` blacksmith, `mil` mill, `cen` town centre, `tow` towers, `gol` / `iro` / `coa` mines, `por` port… (`eur` / `rus` / `tur` replace the nation for shared ones) |
| `<nation><place>.<unit>.1.<level>` | the unit's attack training, level *level* |
| `<nation><place>.<unit>.2.<level>` | its defense training; for artillery, build time |

- Mine upgrades are researched per mine, so the same index can come several
  times.
- A few upgrades change prices (`gc_upg_type_priceperc`): the fishing boat,
  one academy upgrade, and artillery.

### 7.6 Score

The game's score (`counter.scores`) is not sent. It can be rebuilt from its
rules:

| Event | Change |
|---|---|
| a unit appears | + its `score` (unit data) |
| a building is **finished** | + its score |
| an object dies | − 2 × its score for the owner (a building only if it was finished), floored at 0 |
| a kill | + 2 × the victim's score for the killer |

The end screen shows the score divided by 100.

---

## 8. QLREC1 recordings

The format written by the QLadder recorder (a Sich module) for every room
that started a match. It is not part of the game. The reference
implementation reads it.

```
"QLREC1\n"
repeat:
    u32  ms since the room was created
    u32  payload length
    u16  code
    u32  id from
    u32  id to
    ...  payload
```

- The frames are those room members sent, as the server received them.
- Code `0xFFFF` is a marker of the recorder: a JSON object with `ev` =
  `create`, `join`, `leave`, `lock`, `master` or `close`.
- Private messages (`SERVER_MESSAGE`) are never recorded.
- Files may be gzip-compressed.

---

## 9. Constants

### 9.1 Nations

| Id | Nation | Id | Nation | Id | Nation |
|---|---|---|---|---|---|
| 0 | Austria | 8 | Prussia | 16 | Piedmont |
| 1 | France | 9 | Venice | 17 | Saxony |
| 2 | England | 10 | Turkey | 18 | Bavaria |
| 3 | Spain | 11 | Algeria | 19 | Hungary |
| 4 | Russia | 12 | *(missions)* | 20 | Switzerland |
| 5 | Ukraine | 13 | Netherlands | 21 | Scotland |
| 6 | Poland | 14 | Denmark | 22 | *(Tatars, not playable)* |
| 7 | Sweden | 15 | Portugal | 23 | *(Lithuania, not playable)* |

- In the datasync, 24 and more mean "random".
- -2 is a spectator.

### 9.2 Resources

`gc_resource_type_*`: 0 none, 1 food, 2 wood, 3 stone, 4 gold, 5 iron,
6 coal.

### 9.3 Order types

`gc_obj_order_type_*`:

| Id | Order | Id | Order |
|---|---|---|---|
| 0 | none | 11 | build wall (continue) |
| 1 | move | 12 | build wall |
| 2 | attack an object | 13 | go into a mine |
| 3 | gather | 14 | board a transport |
| 4 | produce | 15 | leave a transport |
| 5 | patrol | 16 | leave a building |
| 6 | attack a point | 17 | build |
| 7 | keep attacking a point | 18 | guard |
| 8 | upgrade | 19 | repair |
| 9 | fish | 20 | unload units |
| 10 | create gates | | |

### 9.4 Other

| Name | Value |
|---|---|
| `gc_MaxPlayerCount` | 12 |
| `gc_ResCount` | 7 |
| `gc_MaxCountryCount` | 24 |
| `gc_country_maxmembers` | 80 |
| `gc_country_maxupgradecount` | 320 |
| `gc_spectator_countryid` | -2 |

---

## Appendix A. Annotated examples

All from a recorded 2.2.3 match.

**`ReadRes`**: both players' food goes down by 1 (their units eat).

```
00 03 0e 08 00       record, owner 14 (progress), section 8 (ReadRes)
03 00                players 0 and 1
02 02 81             player 0: changed = food (bit 1), sent as one byte: 0x81 = -1
02 02 81             player 1: the same
01                   end
```

**`ReadStats`**, early in the match:

```
00 03 0e 0a 00                   ReadStats
03 00 00 00                      mask1: players 0, 1
02 23 00 00                      mask2: units food (bit 1), buildings wood (8), stone (9), life food (13)
00 00 00 00                      player 0 units: food 0
3e 03 00 00 b6 03 00 00          player 0 buildings: wood 830, stone 950
32 00 00 00                      player 0 life: food 50
c8 00 00 00                      player 1 units: food 200
0e 0b 00 00 86 0b 00 00          player 1 buildings: wood 2830, stone 2950
32 00 00 00                      player 1 life: food 50
01
```

**`ReadConstruct`**: the computer (slot 1, Saxony) places a town centre.

```
00 03 01 21 00           owner 1, section 33
01                       server
11 00 00 00              nation 17 (Saxony)
06 00 73 61 78 63 65 6e  "saxcen"
00 80 c8 42 00 80 cc 42  x 100.25, z 102.25
00                       keep orders
00 00 00 00              no builders
01
```

**`ReadNew`**: a peasant leaves the town centre (uid 9824).

```
00 03 01 0d 00                       owner 1, section 13
01                                   server
05 00 75 6e 69 74 73                 "units"
06 00 70 65 61 61 75 73              "peaaus"
52 b8 cb 42 f6 a8 d0 42              x 101.86, z 104.33
60 26 00 00                          produced by uid 9824
b5 26 00 00                          new uid 9909
18 00 00 00                          24 objects
01
```

**`ReadUpgrade`**: the computer starts upgrade 2 (a mill upgrade) in uid 9825.

```
00 03 01 19 00  01  02 00 00 00  01  01 00 00 00  61 26 00 00  01
```

**`ReadOrder`**: player 0 sends 37 units to attack uid 9949.

```
00 03 00 17 00
02 00 00 00      type 2: attack an object
dd 26 00 00      target 9949
01 00            clear orders, no lock
25 00 00 00      37 units
31 27 00 00 ...  their uids
01
```

**Sync block**: one unit, all fields.

```
09 6b 78 01              sync, key 0x01786b
01 00 00 00              1 entry
5f 26 00                 uid 9823
1e                       flags: tag, position, direction
61 14 11 60              tag: none, turn, action_none, execute_none, weapon_none,
                              resource_none, visual_none, sync_stp, sync_endpoint
16 ab c6 42 23 e5 c6 42  x 99.33, z 99.45
00 ce 8f c2              direction -71.9
```

**Sync block**: an object dies.

```
09 55 b6 01  01 00 00 00  89 00 00  18  04 00 00 00     uid 137: essential_death
```

---

## Appendix B. Reference implementation

[`c3net`](c3net/) is a dependency-free Python 3.9+ package:

| Module | Covers |
|---|---|
| `c3net.lobby` | frames (4.1), parser trees (3.3), codes and parser ids, results (5.5) |
| `c3net.room` | game name (5.1), lobby status (5.2), datasync (5.3) |
| `c3net.stream` | the match stream (section 6) |
| `c3net.recording` | QLREC1 files (section 8) |
| `c3net.upgrades` | upgrade lists from an installed game (7.5) |
| `c3net.dmscript` | an interpreter for the game's script language, enough for `country.script` |

```
python -m c3net dump match.rec.gz          # every frame, decoded
python -m c3net summary match.rec.gz       # per player: units, buildings, upgrades, losses, statistics
python -m c3net upgrades "C:/Games/Cossacks 3"
```

## Appendix C. Revision history

| Revision | Date | Changes |
|---|---|---|
| 0.1 | 2026-09-26 | First version. |
