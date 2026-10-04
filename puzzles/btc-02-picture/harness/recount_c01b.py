"""Recount the BIP39-checksum survivors of broad-2026-09-30/c01b_moonword_breathe_blackfree.conf
on the CPU, sharing no code with the engine (read-only; no key derivation, no target, no GPU).

usage: python recount_c01b.py [PROCS=8]

c01b: BIP39-12; FIX 1 subject, 3 tower, 9 eye, 11 pyramid; REQUIRE food this real one order moon
black (7 words) in the 8 free slots; the 1 leftover slot over the full list. The engine reported
tested 82,575,360 and valid 5,167,745 (z +3.08 against 1/16). This counts every candidate the
config describes (8P7 placements x 2048; a phrase that two placements both produce is counted
twice, as the engine counts candidates) and prints the total, so the engine's count can be checked
exactly. A candidate survives if its 4 checksum bits equal the first 4 bits of SHA-256 of its
128-bit entropy (BIP39). The passphrase does not enter the checksum.
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
FIX = {0: "subject", 2: "tower", 8: "eye", 10: "pyramid"}  # 0-based slots
REQ = ["food", "this", "real", "one", "order", "moon", "black"]
FREE = [i for i in range(N) if i not in FIX]
PLACEMENTS = list(itertools.permutations(FREE, len(REQ)))
assert len(FREE) == 8 and len(PLACEMENTS) == 40320


def shift(slot):
    return 11 * (N - 1 - slot)


def checksum_ok(v):
    return hashlib.sha256((v >> 4).to_bytes(16, "big")).digest()[0] >> 4 == (v & 15)


def value(words):
    v = 0
    for s, w in enumerate(words):
        v |= IDX[w] << shift(s)
    return v


def count_placement(p):
    tok = [None] * N
    for s, w in FIX.items():
        tok[s] = w
    for s, w in zip(p, REQ):
        tok[s] = w
    (a,) = [i for i in range(N) if tok[i] is None]
    base = 0
    for s, w in enumerate(tok):
        if w is not None:
            base |= IDX[w] << shift(s)
    sa = shift(a)
    return sum(1 for ia in range(2048) if checksum_ok(base | (ia << sa)))


def selftest():
    good = ["abandon " * 11 + "about",
            "legal winner thank year wave sausage worth useful legal winner thank yellow"]
    for m in good:
        assert checksum_ok(value(m.split())), m
    assert not checksum_ok(value(("abandon " * 12).split())), "abandon x12 must fail the checksum"


def main():
    procs = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    selftest()
    t0 = time.time()
    with Pool(procs) as pool:
        counts = pool.map(count_placement, PLACEMENTS, chunksize=256)
    tested = len(PLACEMENTS) * 2048
    print(f"c01b: tested {tested:,}, CPU survivors {sum(counts):,} "
          f"({time.time() - t0:.0f} s, {procs} processes)")


if __name__ == "__main__":
    main()
