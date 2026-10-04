"""Totals and binomial z of a gpu_setperm checkpoint (read-only; no GPU, no target).

usage: python ck_stats.py CK RATE NPASS [RAW]
  RATE  = filter pass rate as a fraction, e.g. 1/256 (Electrum) or 1/16 (BIP39-12)
  NPASS = passphrases per valid seed (setperm always adds the empty one)
  RAW   = the config's candidate count; if given, the blocks' tested must sum to it
"""
import math
import sys
from fractions import Fraction


def main():
    ck, rate, npass = sys.argv[1], Fraction(sys.argv[2]), int(sys.argv[3])
    raw = int(sys.argv[4]) if len(sys.argv) > 4 else None
    sig = plant = None
    blocks = {}
    for line in open(ck):
        f = line.split()
        if not f:
            continue
        if f[0] == "SIG":
            sig = f[1]
        elif f[0] == "PLANT":
            plant = f[1]
        elif f[0] == "done" and len(f) == 5 and f[4] == "ok":
            b, tested, derived = int(f[1]), int(f[2]), int(f[3])
            assert b not in blocks, f"block {b} twice"
            assert derived % npass == 0, f"block {b}: derived {derived} not a multiple of {npass}"
            blocks[b] = (tested, derived // npass)
    n = len(blocks)
    assert sorted(blocks) == list(range(n)), "blocks are not 0..n-1"
    p = float(rate)
    tested = sum(t for t, _ in blocks.values())
    valid = sum(v for _, v in blocks.values())
    exp = tested * p
    z = (valid - exp) / math.sqrt(exp * (1 - p))
    zs = [(v - t * p) / math.sqrt(t * p * (1 - p)) for t, v in blocks.values() if t * p > 0]
    print(f"{ck}: SIG {sig} PLANT {plant} blocks {n}")
    print(f"  tested {tested:,}" + ("" if raw is None else f" (config raw {raw:,}: {'EQUAL' if tested == raw else 'DIFFERENT'})"))
    print(f"  valid {valid:,} expected {exp:,.0f} z {z:+.2f}; derivations {valid * npass:,}")
    print(f"  per-block z {min(zs):+.2f} .. {max(zs):+.2f}")


if __name__ == "__main__":
    main()
