"""Upgrade numbers: ReadUpgrade sends an index into the nation's upgrade list. See cossacks3-net.md, 6.6.

The list is what data/scripts/lib/country.script builds when the game starts:
an upgrade id takes the first free slot when it is first added. Nothing here
ships game data: `upgrade_ids` runs the game's own script from your
installation with the interpreter in dmscript.py.

    >>> ids = upgrade_ids("C:/Games/Cossacks 3")    # or a Linux copy of the folder
    >>> ids[2][40]                                  # England, index 40
    'engbla.1'
    >>> meaning("engbar.musketeer.2.3")
    ('training', 'musketeer', 'defense', 3)
"""

import os
import re
from typing import Optional

from . import dmscript

PLAYABLE = [c for c in range(24) if c not in (12, 22, 23)]  # 12, 22, 23 are not playable nations

_BUILDING_RE = re.compile(r"^([a-z]*?)([a-z]{3})\.(\d+)$")        # "ukraca.12": Ukraine, academy, 12
_TRAINING_RE = re.compile(r"^([a-z0-9]+?)\.([a-z0-9]+)\.([12])\.(\d+)$")  # "ukrbar.pikeman.1.3"


def _script(path: str) -> str:
    with open(path, encoding="cp1251", errors="replace") as f:
        return f.read().replace("\r", "")


def upgrade_ids(game_dir: str, nations=PLAYABLE) -> dict:
    """{nation id: [upgrade id, ...]} in the game's order; index 0 is "null".

    Takes ~45 s: it runs _country_InitAll for all nations.
    """
    scripts = os.path.join(game_dir, "data", "scripts")
    order = {}

    def index_of(interp, args):  # _country_GetUpgradeIndexByUpgradeID without scanning 320 slots
        cid, upgid, add = int(dmscript.num(args[0].value)), dmscript.text(args[1].value), args[2].value
        ids = order.setdefault(cid, [])
        if upgid in ids:
            return ids.index(upgid)
        if add:
            ids.append(upgid)
            return len(ids) - 1
        return -1

    consts = dmscript.read_constants(_script(os.path.join(scripts, "dmscript.global")))
    interp = dmscript.Interpreter([_script(os.path.join(scripts, "lib", "country.script"))], consts,
                                  {"_country_getupgradeindexbyupgradeid": index_of})
    interp.globals.vars.pop("_country_getupgradeindexbyupgradeid", None)
    interp.call("_country_InitAll", [])
    return {cid: ids for cid, ids in order.items() if cid in nations}


def meaning(upgrade_id: str) -> Optional[tuple]:
    """("building", place, n) | ("training", unit, "attack" | "defense", level) | None.

    place: the building's three letters (aca academy, bla blacksmith, mil mill, cen town centre
    (cen.1 = the 18th century), gol/iro/coa mines, bar/ba2/sta barracks and stables, art artillery depot...).
    A ".2." upgrade of artillery changes build time, not armour.
    """
    m = _TRAINING_RE.match(upgrade_id or "")
    if m:
        return ("training", m.group(2), "attack" if m.group(3) == "1" else "defense", int(m.group(4)))
    m = _BUILDING_RE.match(upgrade_id or "")
    if m:
        return ("building", m.group(2), int(m.group(3)))
    return None
