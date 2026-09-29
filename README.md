# cossacks_3_net_spec

What travels between **Cossacks 3** and its lobby server during an online game,
documented byte by byte — and a Python parser for it.

The main find: Cossacks 3 is **host-authoritative**. The host sends the whole
state of a match to the other players through the lobby server:

- units trained, with their types and owners;
- buildings placed and when each is finished;
- upgrades researched;
- resources on hand, gathered and spent;
- positions, attacks and deaths;
- the result.

A lobby server can therefore follow every match in full, with no mod on the
players' side.

- **[cossacks3-net.md](cossacks3-net.md)** — the specification: frames, room
  data, every record of the match stream, what the values mean (and where the
  game's own statistics mislead), annotated real examples.
- **[c3net/](c3net/)** — the reference parser: Python 3.9+, no dependencies.

## Quick start

```
git clone https://github.com/kewl-ua/cossacks_3_net_spec
cd cossacks_3_net_spec
python -m c3net dump match.rec.gz        # every frame of a recording, decoded
python -m c3net summary match.rec.gz     # per player: units, buildings, upgrades, losses, statistics
```

```python
from c3net import stream

for block in stream.parse(payload):            # a LAN_RECORD (0x04B0) payload
    if isinstance(block, stream.Record) and block.name == "ReadNew":
        print(block.owner, block.fields["base"], block.fields["uid"])   # slot, unit code, uid
    elif isinstance(block, stream.Sync):
        for unit in block.units:
            if unit.get("tag", 0) & stream.TAG_DEATH:
                print("died", unit["uid"])
```

Recordings are in the QLREC1 format of the [QLadder](https://qladder.com)
recorder (spec, section 10). A packet capture of the lobby port works too:
split the TCP stream into frames with `c3net.lobby.read_frames`.

Upgrade numbers depend on the game's scripts. `python -m c3net upgrades
<game folder>` rebuilds the list from your installation (9.5).

## Status

- Revision 0.4 describes version 2.2.3 with unmodded scripts.
- Every record layout comes from the game's own script handlers.
- Checked on recorded matches: every block decodes, and the statistics
  balance with the game's end screen.
- Checked on a match between two humans too (spec 1.2). The random map,
  rebuilt from its seed with the game's own generator scripts, matches the
  host's snapshot (9.10); `c3net.randomext` has the engine's random numbers.
- Tests: `python -m pytest tests`.

## Credits

- [Sich](https://github.com/3skcassoc/sich) by 3skcassoc: the open lobby
  server this work runs on. Its message codes and names are used here.
- Written for [QLadder](https://qladder.com), a ladder for Cossacks 3.

Not affiliated with GSC Game World. No game files are included. `c3net.upgrades`
reads the scripts of your own installation.

## License

MIT, see [LICENSE](LICENSE).
