"""Recount one p6 block's BIP39-checksum survivors on the CPU, sharing no code with the engine
(read-only; no key derivation, no target, no GPU).

usage: python recount_p6_block.py BLOCK [UNITS_PER_BLOCK=3] [PROCS=3]

Built for p6_moonpass_slot12_open.conf (BIP39-12; FIX 1 subject, 3 tower, 9 eye, 10 black,
11 pyramid; REQUIRE food this real one order; the 2 leftover slots over the full list). A unit is
one REQUIRE placement: unit u is the u-th tuple of itertools.permutations(free_slots, 5), tuple[j]
= the slot of REQUIRE word j (gpu_setperm's documented order); inside a unit the 2 leftover slots
run over all 2048^2 words, so the unit's candidate SET does not depend on the order inside it. A
candidate survives if its 4 checksum bits equal the first 4 bits of SHA-256 of its 128-bit
entropy (BIP39). The passphrase does not enter the checksum, so survivors = valid seeds.
"""
import hashlib
import itertools
import os
import sys
import time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
WORDS = open(os.path.join(HERE, "english.txt")).read().split()
assert len(WORDS) == 2048 and WORDS[0] == "abandon" and WORDS[-1] == "zoo"
IDX = {w: i for i, w in enumerate(WORDS)}
N = 12
FIX = {0: "subject", 2: "tower", 8: "eye", 9: "black", 10: "pyramid"}   # 0-based slots
REQ = ["food", "this", "real", "one", "order"]
FREE = [i for i in range(N) if i not in FIX]
PLACEMENTS = list(itertools.permutations(FREE, len(REQ)))
assert len(PLACEMENTS) == 2520


def shift(slot):
    return 11 * (N - 1 - slot)


def checksum_ok(v):
    return hashlib.sha256((v >> 4).to_bytes(16, "big")).digest()[0] >> 4 == (v & 15)


def value(words):
    v = 0
    for s, w in enumerate(words):
        v |= IDX[w] << shift(s)
    return v


def count_unit(u):
    tok = [None] * N
    for s, w in FIX.items():
        tok[s] = w
    for s, w in zip(PLACEMENTS[u], REQ):
        tok[s] = w
    a, b = [i for i in range(N) if tok[i] is None]
    base = 0
    for s, w in enumerate(tok):
        if w is not None:
            base |= IDX[w] << shift(s)
    sa, sb = shift(a), shift(b)
    n = 0
    for ia in range(2048):
        va = base | (ia << sa)
        for ib in range(2048):
            if checksum_ok(va | (ib << sb)):
                n += 1
    return n


def selftest():
    good = ["abandon " * 11 + "about",
            "legal winner thank year wave sausage worth useful legal winner thank yellow"]
    for m in good:
        assert checksum_ok(value(m.split())), m
    assert not checksum_ok(value(("abandon " * 12).split())), "abandon x12 must fail the checksum"
    tok = [None] * N
    for s, w in FIX.items():
        tok[s] = w
    for s, w in zip(PLACEMENTS[0], REQ):
        tok[s] = w
    assert tok == ["subject", "food", "tower", "this", "real", "one", "order", None, "eye", "black", "pyramid", None], tok


def main():
    block = int(sys.argv[1])
    ub = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    procs = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    selftest()
    units = range(block * ub, min((block + 1) * ub, len(PLACEMENTS)))
    t0 = time.time()
    with Pool(procs) as pool:
        counts = pool.map(count_unit, units, chunksize=1)
    print(f"block {block}: units {units.start}..{units.stop - 1}, tested {len(units) * 2048 * 2048:,}, "
          f"CPU survivors {sum(counts):,} ({time.time() - t0:.0f} s, {procs} processes)")


if __name__ == "__main__":
    main()
