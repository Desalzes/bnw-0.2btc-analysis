"""GPU BIP39 permutation solver (OpenCL) -- READ-ONLY.

Searches orderings of candidate 12-word set(s), optionally with passphrase
candidates, for one whose BIP44 m/44'/0'/0'/0/0 Legacy-P2PKH address matches a
target. SAFE: on a hit it prints + writes FOUND.txt and STOPS -- it never
builds, signs, or broadcasts a transaction.

  python gpu_solve.py --selftest
  python gpu_solve.py --words words.txt [--passphrases pp.txt] --target 1KfZ...
  python gpu_solve.py --pool pool.txt  [--passphrases pp.txt] --target 1KfZ...
        (--pool iterates every choose-12 combination of the pool words)
"""
import argparse
import itertools
import os
import sys
import time

import numpy as np
import pyopencl as cl

HERE = os.path.dirname(os.path.abspath(__file__))
KDIR = os.path.join(HERE, "kernels")
ORDER = ["common", "ripemd", "sha2", "secp256k1_common", "secp256k1_scalar",
         "secp256k1_field", "secp256k1_group", "secp256k1_prec", "secp256k1",
         "address", "mnemonic_constants", "solve_perm"]
WORDS = [w.strip() for w in open(os.path.join(HERE, "english.txt")) if w.strip()]
WORD_IDX = {w: i for i, w in enumerate(WORDS)}
FACT12 = 479001600
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s):
    n = 0
    for c in s:
        n = n * 58 + _B58.index(c)
    pad = len(s) - len(s.lstrip("1"))
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * pad + body


def build_program():
    src = ""
    for f in ORDER:
        src += open(os.path.join(KDIR, f + ".cl")).read() + "\n"
    plat = next(p for p in cl.get_platforms() if "NVIDIA" in p.name)
    dev = plat.get_devices()[0]
    ctx = cl.Context([dev])
    prog = cl.Program(ctx, src).build(options="-cl-std=CL1.2")
    return ctx, dev, prog


def indices_of(words):
    bad = [w for w in words if w not in WORD_IDX]
    if bad:
        sys.exit(f"not BIP39 words: {bad}")
    return np.array([WORD_IDX[w] for w in words], dtype=np.uint16)


def search_one(ctx, q, prog, word_set_np, target25, pp_bytes,
               batch=30_000_000, verbose=False):
    mf = cl.mem_flags
    ws_buf = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR, hostbuf=word_set_np)
    tg_buf = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR,
                       hostbuf=np.frombuffer(target25, dtype=np.uint8))
    ppb = pp_bytes if len(pp_bytes) > 0 else b"\x00"   # dummy 1 byte when empty
    pp_buf = cl.Buffer(ctx, mf.READ_ONLY | mf.COPY_HOST_PTR,
                       hostbuf=np.frombuffer(ppb, dtype=np.uint8))
    found = np.zeros(1, np.uint8)
    fmnem = np.zeros(180, np.uint8)
    fbuf = cl.Buffer(ctx, mf.READ_WRITE | mf.COPY_HOST_PTR, hostbuf=found)
    fmbuf = cl.Buffer(ctx, mf.READ_WRITE | mf.COPY_HOST_PTR, hostbuf=fmnem)
    knl = prog.solve_perm
    start, t0 = 0, time.time()
    while start < FACT12:
        n = min(batch, FACT12 - start)
        knl(q, (n,), None, np.uint64(start), np.uint64(FACT12),
            ws_buf, tg_buf, pp_buf, np.uint32(len(pp_bytes)), fbuf, fmbuf)
        cl.enqueue_copy(q, found, fbuf)
        q.finish()
        if found[0] == 1:
            cl.enqueue_copy(q, fmnem, fmbuf)
            q.finish()
            return bytes(fmnem).split(b"\x00")[0].decode()
        start += n
        if verbose:
            el = max(time.time() - t0, 1e-9)
            print(f"    {start/FACT12*100:5.1f}%  {start/el/1e6:5.1f}M perm/s", flush=True)
    return None


def load_lines(path, allow_empty=True):
    out = []
    for ln in open(path, encoding="utf-8"):
        if ln.lstrip().startswith("#"):
            continue
        out.append(ln.rstrip("\n"))
    return out


def selftest():
    from bip_utils import (Bip39MnemonicGenerator, Bip39WordsNum,
                           Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes)

    def addr_of(m, pp):
        seed = Bip39SeedGenerator(m).Generate(pp)
        return (Bip44.FromSeed(seed, Bip44Coins.BITCOIN).Purpose().Coin().Account(0)
                .Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PublicKey().ToAddress())

    ctx, dev, prog = build_program()
    q = cl.CommandQueue(ctx)
    ok = True
    for pp in ("", "rainyday1865"):
        m = None
        for _ in range(50):
            c = str(Bip39MnemonicGenerator().FromWordsNumber(Bip39WordsNum.WORDS_NUM_12))
            if len(set(c.split())) == 12:
                m = c
                break
        ws = m.split()
        target = b58decode(addr_of(m, pp))
        got = search_one(ctx, q, prog, indices_of(ws), target, pp.encode(), batch=1)
        good = got is not None and got.split() == ws
        ok = ok and good
        print(f"  passphrase={pp!r:>14}: {'PASS' if good else 'FAIL ('+repr(got)+')'}")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--words")
    ap.add_argument("--pool")
    ap.add_argument("--passphrases")
    ap.add_argument("--target", default="1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)

    target25 = b58decode(a.target)
    if len(target25) != 25 or target25[0] != 0x00:
        sys.exit(f"target must be Legacy P2PKH (1...); decoded {len(target25)}B")

    if a.words:
        txt = open(a.words).read() if os.path.exists(a.words) else a.words
        txt = "\n".join(l for l in txt.splitlines() if not l.strip().startswith("#"))
        sets = [[w.strip().lower() for w in txt.replace(",", " ").split() if w.strip()]]
    elif a.pool:
        txt = open(a.pool).read()
        txt = "\n".join(l for l in txt.splitlines() if not l.strip().startswith("#"))
        pool = [w.strip().lower() for w in txt.replace(",", " ").split() if w.strip()]
        sets = [list(c) for c in itertools.combinations(pool, 12)]
        print(f"pool of {len(pool)} -> {len(sets)} candidate 12-word sets")
    else:
        sys.exit("provide --words, --pool, or --selftest")

    passphrases = load_lines(a.passphrases) if a.passphrases else [""]
    # ensure empty passphrase is tried
    if "" not in passphrases:
        passphrases = [""] + passphrases

    total = len(sets) * len(passphrases)
    print(f"{len(sets)} set(s) x {len(passphrases)} passphrase(s) = {total} full sweeps")
    ctx, dev, prog = build_program()
    q = cl.CommandQueue(ctx)
    done, t0 = 0, time.time()
    for ws in sets:
        wsi = indices_of(ws)
        for pp in passphrases:
            res = search_one(ctx, q, prog, wsi, target25, pp.encode())
            done += 1
            if res:
                print(f"\n*** MATCH ***\n  mnemonic: {res}\n  passphrase: {pp!r}")
                open(os.path.join(HERE, "FOUND.txt"), "w").write(
                    f"mnemonic: {res}\npassphrase: {pp!r}\ntarget: {a.target}\n")
                print("  recorded to FOUND.txt -- READ-ONLY; verify + claim manually.")
                return
            el = time.time() - t0
            print(f"  [{done}/{total}] no match  set={' '.join(ws[:3])}... "
                  f"pp={pp!r}  ({el/done:.0f}s/sweep)", flush=True)
    print("\nno match across all sets x passphrases.")


if __name__ == "__main__":
    main()
