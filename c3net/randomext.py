"""RandomExt, the engine's random numbers, and VectorRotateY. See cossacks3-net.md, 9.10.

A random map is generated from its seed, `settings.gen.randkey0` and `randkey1` of the
`gMap` tree that parser 1 (`LAN_GENERATE`) carries. The generator scripts draw every random
number from RandomExt:

    >>> r = RandomExt(763381496)          # SetRandomKey(randkey1)
    >>> r.next()                          # the first draw picks the terrain mask
    0.8195599317550659
    >>> r.skip(1_000_000)                 # a million draws later, in O(log n)
    >>> round(r.next(), 6)
    0.175943

Nothing here ships game data. The formulas were read from cossacks.exe (2.2.3) and checked
by generating recorded matches' maps.
"""

import math
import struct

A = 64525            # the multiplier (0xFC0D)
C = 1013904223       # the increment (0x3C6EF35F)
MOD = 1 << 32

# gMap.settings.gen.mapsize -> the map's width in cells
MAP_WIDTH = {0: 320, 1: 480, 2: 640, 3: 256}


def f32(value):
    """Round to a 32-bit float, as the engine's Single."""
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def _signed64(value):
    value &= (1 << 64) - 1
    return value - (1 << 64) if value & (1 << 63) else value


class RandomExt:
    """The engine's RandomExt, SetRandomKey and SetRandomExtKey64.

    The state is a signed 64-bit integer. A draw is S = (S * 64525 + 1013904223) mod 2^32,
    with Delphi's mod (the result takes the sign of the dividend), and returns
    Single(S / 2^32). For a state in 0 .. 2^32 - 1 this is a plain linear congruential
    generator mod 2^32 and the draws lie in [0, 1]: a state of 2^32 - 128 or more rounds to
    exactly 1.0 in Single precision. A key of 2^31 or more is negative after the sign extension;
    except for the last 15 713 keys, which the first draw makes positive, it gives negative first
    draws.
    """

    def __init__(self, key=0):
        self.state = 0
        self.set_key(key)

    def set_key(self, key):
        """SetRandomKey: the 32-bit key, sign-extended."""
        key &= MOD - 1
        self.state = key - MOD if key & 0x80000000 else key

    def set_key64(self, key0, key1):
        """SetRandomExtKey64: key0 << 32 | key1, as a signed 64-bit integer."""
        self.state = _signed64(((key0 & (MOD - 1)) << 32) | (key1 & (MOD - 1)))

    def next(self):
        """RandomExt: the next draw."""
        v = _signed64(self.state * A + C)
        r = abs(v) % MOD
        self.state = -r if v < 0 else r
        return f32(self.state / MOD)

    def skip(self, n):
        """Advance n draws at once. Only for a state in 0 .. 2^32 - 1, where a draw is affine mod 2^32."""
        if not 0 <= self.state < MOD:
            raise ValueError("skip() needs a state in 0 .. 2^32 - 1")
        a, c = jump(n)
        self.state = (a * self.state + c) % MOD


def jump(n):
    """(a, c) with S_n = a * S_0 + c mod 2^32: n draws of a non-negative state as one affine map."""
    ra, rc, ba, bc = 1, 0, A, C
    while n:
        if n & 1:
            ra, rc = (ba * ra) % MOD, (ba * rc + bc) % MOD
        ba, bc = (ba * ba) % MOD, (ba * bc + bc) % MOD
        n >>= 1
    return ra, rc


def rotate_y(x, z, degrees):
    """VectorRotateY: (x', z') = (cos*x + sin*z, cos*z - sin*x), degrees, in Single precision."""
    x, z = f32(x), f32(z)
    angle = f32(f32(degrees) * f32(math.pi / 180))
    s, c = math.sin(angle), math.cos(angle)
    return f32(c * x + s * z), f32(c * z - s * x)
