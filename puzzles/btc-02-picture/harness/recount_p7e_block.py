"""Recount one block's filter survivors on the CPU, sharing no code with the engine (read-only;
no key derivation, no target, no GPU).

usage: python recount_p7e_block.py BLOCK [UNITS_PER_BLOCK=122] [PROCS=10]

Built for p7e_moonpass_pins_only.conf (Electrum-12; FIX 1 subject, 3 tower, 9 eye, 11 pyramid;
REQUIRE food this real one order black; the 2 leftover slots over the full list). A unit is one
REQUIRE placement: unit u is the u-th tuple of itertools.permutations(free_slots, 6), tuple[j] =
the slot of REQUIRE word j (gpu_setperm's documented order); inside a unit the 2 leftover slots run
over all 2048^2 words, so the unit's candidate SET does not depend on the order inside it. A
candidate survives if HMAC-SHA512(key "Seed version", mnemonic) starts with 0x01 (Electrum's
is_new_seed for standard seeds; lowercase ASCII words joined by single spaces are already
normalised).
"""
import hashlib
import hmac
import itertools
import os
import sys
import time
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
WORDS = open(os.path.join(HERE, "english.txt")).read().split()
assert len(WORDS) == 2048 and WORDS[0] == "abandon" and WORDS[-1] == "zoo"
N = 12
FIX = {0: "subject", 2: "tower", 8: "eye", 10: "pyramid"}     # 0-based slots
REQ = ["food", "this", "real", "one", "order", "black"]
FREE = [i for i in range(N) if i not in FIX]
PLACEMENTS = list(itertools.permutations(FREE, len(REQ)))
assert len(PLACEMENTS) == 20160
BASE = hmac.new(b"Seed version", digestmod=hashlib.sha512)


def count_unit(u):
    slots = PLACEMENTS[u]
    tok = [None] * N
    for s, w in FIX.items():
        tok[s] = w
    for s, w in zip(slots, REQ):
        tok[s] = w
    a, b = [i for i in range(N) if tok[i] is None]           # the two leftover slots, a < b
    pre = " ".join(tok[:a])
    pre = (pre + " ") if pre else ""
    mid = " " + " ".join(tok[a + 1:b]) + " " if b > a + 1 else " "
    post = " ".join(tok[b + 1:])
    post = (" " + post) if post else ""
    tails = [(w + post).encode() for w in WORDS]
    n = 0
    for wa in WORDS:
        h1 = BASE.copy()
        h1.update((pre + wa + mid).encode())
        for t in tails:
            h = h1.copy()
            h.update(t)
            if h.digest()[0] == 1:
                n += 1
    return n


def selftest():
    # Electrum's published standard-seed vector (the one setperm_solve.py's selftest verifies against
    # its address 1NNkttn1...) must pass; the BIP39 test phrase must not; plus the unit layout check
    m = "cycle rocket west magnet parrot shuffle foot correct salt library feed song"
    h = BASE.copy(); h.update(m.encode())
    assert h.hexdigest().startswith("01"), "Electrum vector should be a standard seed"
    h = BASE.copy(); h.update(("abandon " * 11 + "about").encode())
    assert not h.hexdigest().startswith("01"), "control phrase unexpectedly passes"
    tok = [None] * N
    for s, w in FIX.items():
        tok[s] = w
    for s, w in zip(PLACEMENTS[0], REQ):
        tok[s] = w
    assert tok == ["subject", "food", "tower", "this", "real", "one", "order", "black", "eye", None, "pyramid", None], tok


def main():
    block = int(sys.argv[1])
    ub = int(sys.argv[2]) if len(sys.argv) > 2 else 122
    procs = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    selftest()
    units = range(block * ub, min((block + 1) * ub, len(PLACEMENTS)))
    t0 = time.time()
    with Pool(procs) as pool:
        counts = pool.map(count_unit, units, chunksize=1)
    tested = len(units) * 2048 * 2048
    print(f"block {block}: units {units.start}..{units.stop - 1}, tested {tested:,}, "
          f"CPU survivors {sum(counts):,} ({time.time() - t0:.0f} s, {procs} processes)")


if __name__ == "__main__":
    main()
