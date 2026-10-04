"""Positional-template BIP39 solver (CPU, multiprocessing) -- READ-ONLY.

A template fixes some slots and lets others range over candidate lists, for any
BIP39 length (12/15/18/21/24). Each checksum-valid candidate is derived
(PBKDF2-HMAC-SHA512 x2048 -> BIP32 -> compressed P2PKH) on every listed path
and passphrase and compared with the target hash160.

SAFE: compare-only. On a hit it prints, writes FOUND_template.txt next to this
file and stops. It never builds, signs or broadcasts anything.

Config format (one directive per line, '#' comments):
    WORDS 21
    SLOT 1 subject            # fixed
    SLOT 6 @POOL              # the shared POOL list
    SLOT 7 real|liberty       # explicit alternatives
    POOL able accuse ...
    PASS <passphrase>         # repeatable; the empty passphrase is always tried
    PATH m/44'/0'/0'/0/0      # repeatable; default is that path alone
    DISTINCT                  # optional: reject candidates that repeat a word

  python template_solve.py --selftest
  python template_solve.py CONFIG [--procs N]
"""
import argparse
import hashlib
import hmac
import itertools
import os
import random
import sys
import time
from multiprocessing import Pool

from coincurve import PrivateKey

HERE = os.path.dirname(os.path.abspath(__file__))
WORDS = [w.strip() for w in open(os.path.join(HERE, "english.txt")) if w.strip()]
IDX = {w: i for i, w in enumerate(WORDS)}
N_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
TARGET = "1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def hash160_of_address(addr):
    n = 0
    for c in addr:
        n = n * 58 + _B58.index(c)
    raw = n.to_bytes(25, "big")
    assert raw[-4:] == hashlib.sha256(hashlib.sha256(raw[:-4]).digest()).digest()[:4]
    assert raw[0] == 0
    return raw[1:21]


def address_of_hash160(h):
    raw = b"\x00" + h
    raw += hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    n = int.from_bytes(raw, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return "1" * (len(raw) - len(raw.lstrip(b"\x00"))) + s


def parse_path(p):
    out = []
    for part in p.strip().split("/")[1:]:
        out.append(int(part[:-1]) | 0x80000000 if part.endswith("'") else int(part))
    return tuple(out)


def _ckd(k, c, i):
    if i & 0x80000000:
        data = b"\x00" + k + i.to_bytes(4, "big")
    else:
        data = PrivateKey(k).public_key.format(True) + i.to_bytes(4, "big")
    I = hmac.new(c, data, hashlib.sha512).digest()
    return ((int.from_bytes(I[:32], "big") + int.from_bytes(k, "big")) % N_ORDER).to_bytes(32, "big"), I[32:]


def hash160s(mnemonic, passphrase, paths):
    """hash160 of the compressed P2PKH key at each path (shared-prefix cached)."""
    seed = hashlib.pbkdf2_hmac("sha512", mnemonic.encode(), ("mnemonic" + passphrase).encode(), 2048)
    I = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    cache = {(): (I[:32], I[32:])}
    out = []
    for path in paths:
        for d in range(1, len(path) + 1):
            if path[:d] not in cache:
                cache[path[:d]] = _ckd(*cache[path[:d - 1]], path[d - 1])
        k = cache[path][0]
        pub = PrivateKey(k).public_key.format(True)
        out.append(hashlib.new("ripemd160", hashlib.sha256(pub).digest()).digest())
    return out


def checksum_ok(idxs):
    n = len(idxs)
    cs = n // 3
    v = 0
    for i in idxs:
        v = (v << 11) | i
    ent_bits = n * 11 - cs
    ent = (v >> cs).to_bytes(ent_bits // 8, "big")
    return (v & ((1 << cs) - 1)) == hashlib.sha256(ent).digest()[0] >> (8 - cs)


def group_last(last_cands, n):
    cs = n // 3
    groups = {}
    for w in last_cands:
        groups.setdefault(w >> cs, []).append(w)
    return groups


def last_word_options(prefix, last_cands, n, groups=None):
    """Checksum-valid choices for the final slot given the first n-1 indices."""
    cs = n // 3
    hi_bits = 11 - cs
    v = 0
    for i in prefix:
        v = (v << 11) | i
    groups = groups or group_last(last_cands, n)
    ok = []
    for hi, ws in groups.items():
        ent = ((v << hi_bits) | hi).to_bytes((n * 11 - cs) // 8, "big")
        want = hashlib.sha256(ent).digest()[0] >> (8 - cs)
        ok.extend(w for w in ws if (w & ((1 << cs) - 1)) == want)
    return ok


def load_config(path):
    cfg = {"words": None, "slots": {}, "pool": [], "pass": [""], "paths": [], "distinct": False}
    for raw in open(path, encoding="utf-8"):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, rest = line.partition(" ")
        key = key.upper()
        if key == "WORDS":
            cfg["words"] = int(rest)
        elif key == "SLOT":
            n, spec = rest.split(None, 1)
            cfg["slots"][int(n)] = spec.strip()
        elif key == "POOL":
            cfg["pool"] += rest.split()
        elif key == "PASS":
            if rest not in cfg["pass"]:
                cfg["pass"].append(rest)
        elif key == "PATH":
            cfg["paths"].append(rest.strip())
        elif key == "DISTINCT":
            cfg["distinct"] = True
        else:
            sys.exit(f"unknown directive {key!r}")
    return cfg


def build_candidates(cfg):
    n = cfg["words"]
    assert n in (12, 15, 18, 21, 24), n
    pool = list(dict.fromkeys(cfg["pool"]))
    cands = []
    for s in range(1, n + 1):
        spec = cfg["slots"].get(s)
        if spec is None:
            sys.exit(f"slot {s} unspecified")
        words = pool if spec == "@POOL" else (WORDS if spec == "@FULL" else spec.split("|"))
        bad = [w for w in words if w not in IDX]
        if bad:
            sys.exit(f"slot {s}: not BIP39: {bad}")
        cands.append(sorted({IDX[w] for w in words}))
    return cands


# ---- worker ----
_G = {}


def _init(cands, paths, passes, target, distinct, split, count_only=False):
    _G.update(cands=cands, paths=paths, passes=passes, target=target, distinct=distinct,
              split=split, groups=group_last(cands[-1], len(cands)), count_only=count_only)


def _work(unit):
    """unit = one choice for each slot in _G['split']; enumerate the rest."""
    cands, n = _G["cands"], len(_G["cands"])
    heads = [list(c) for c in cands[:n - 1]]
    for s, choice in zip(_G["split"], unit):
        heads[s] = [choice]
    derived = 0
    for prefix in itertools.product(*heads):
        if _G["distinct"] and len(set(prefix)) != len(prefix):
            continue
        for last in last_word_options(prefix, cands[n - 1], n, _G["groups"]):
            idxs = prefix + (last,)
            if _G["distinct"] and last in prefix:
                continue
            if _G.get("count_only"):
                derived += len(_G["passes"])
                continue
            m = " ".join(WORDS[i] for i in idxs)
            for pp in _G["passes"]:
                derived += 1
                for path_i, h in enumerate(hash160s(m, pp, _G["paths"])):
                    if h == _G["target"]:
                        return derived, (m, pp, path_i)
    return derived, None


def run(cands, paths_txt, passes, target_addr, procs, distinct=False, quiet=False,
        count_only=False):
    paths = [parse_path(p) for p in paths_txt]
    target = hash160_of_address(target_addr)
    n = len(cands)
    raw = 1
    for c in cands:
        raw *= len(c)
    exp = raw / (1 << (n // 3)) * len(passes)
    if not quiet:
        print(f"{n} words | raw {raw:,} | ~{exp:,.0f} derivations x {len(paths)} path(s) "
              f"| passphrases {passes!r}", flush=True)
    # split work over the first multi-candidate slots (never the checksum slot)
    split, units = [], 1
    for s in range(n - 1):
        if units >= 8 * procs:
            break
        if len(cands[s]) > 1:
            split.append(s)
            units *= len(cands[s])
    work = list(itertools.product(*[cands[s] for s in split])) if split else [()]
    t0, total = time.time(), 0
    with Pool(procs, initializer=_init,
              initargs=(cands, paths, passes, target, distinct, split, count_only)) as pool:
        for derived, hit in pool.imap_unordered(_work, work):
            total += derived
            if hit:
                pool.terminate()
                m, pp, pi = hit
                return {"mnemonic": m, "passphrase": pp, "path": paths_txt[pi], "derived": total}
    if count_only:
        return total
    if not quiet:
        el = time.time() - t0
        print(f"no match | {total:,} derivations in {el:.0f}s ({total / max(el, 1e-9):,.0f}/s)", flush=True)
    return None


def report_hit(hit, target_addr):
    msg = (f"mnemonic: {hit['mnemonic']}\npassphrase: {hit['passphrase']!r}\n"
           f"path: {hit['path']}\ntarget: {target_addr}\n")
    print("\n*** MATCH ***\n" + msg + "READ-ONLY: record in STATE.md under CANDIDATE KEY and STOP.")
    with open(os.path.join(HERE, "FOUND_template.txt"), "w", encoding="utf-8") as f:
        f.write(msg)


def selftest(procs):
    """Cross-check derivation against bip_utils, then require the solver to FIND
    blanked words of random 12/21/24-word mnemonics (path + passphrase varied)."""
    from bip_utils import (Bip39MnemonicGenerator, Bip39WordsNum, Bip39SeedGenerator,
                           Bip44, Bip44Coins, Bip44Changes)
    ok = True
    rng = random.Random(20260922)
    for wn, en in ((12, Bip39WordsNum.WORDS_NUM_12), (21, Bip39WordsNum.WORDS_NUM_21),
                   (24, Bip39WordsNum.WORDS_NUM_24)):
        m = str(Bip39MnemonicGenerator().FromWordsNumber(en))
        for pp in ("", "BREATHE"):
            seed = Bip39SeedGenerator(m).Generate(pp)
            ref = (Bip44.FromSeed(seed, Bip44Coins.BITCOIN).Purpose().Coin().Account(0)
                   .Change(Bip44Changes.CHAIN_EXT).AddressIndex(0).PublicKey().ToAddress())
            mine = address_of_hash160(hash160s(m, pp, [parse_path("m/44'/0'/0'/0/0")])[0])
            good = ref == mine
            ok &= good
            print(f"  derive {wn:2}w pp={pp!r:10} bip_utils={ref} mine={mine} {'OK' if good else 'FAIL'}")
        # find-test: blank three slots (incl. the last) over decoy pools, target on path #2
        ws = m.split()
        idxs = [IDX[w] for w in ws]
        blanks = sorted(rng.sample(range(wn - 1), 2)) + [wn - 1]
        cands = []
        for s in range(wn):
            if s in blanks:
                decoys = rng.sample(range(2048), 24 if s != wn - 1 else 200)
                cands.append(sorted(set(decoys) | {idxs[s]}))
            else:
                cands.append([idxs[s]])
        paths_txt = ["m/44'/0'/0'/0/0", "m/44'/0'/0'/0/1"]
        tgt = address_of_hash160(hash160s(m, "rainyday", [parse_path(paths_txt[1])])[0])
        hit = run(cands, paths_txt, ["", "rainyday"], tgt, procs, quiet=True)
        good = bool(hit) and hit["mnemonic"] == m and hit["passphrase"] == "rainyday" \
            and hit["path"] == paths_txt[1]
        ok &= good
        print(f"  find   {wn:2}w blanks={[b + 1 for b in blanks]} -> {'FOUND' if good else 'MISSED ' + repr(hit)}")
    # exact-count invariant: with the checksum slot free over all 2048 words,
    # precisely 2048 / 2^cs survive per prefix -- no sampling variance, so any
    # candidate dropped by the work split shows up as a shortfall.
    for wn in (12, 21, 24):
        cs = wn // 3
        cands = [[IDX["abandon"]]] * (wn - 1) + [list(range(2048))]
        cands[1] = list(range(0, 2048, 97))       # 22 choices
        cands[wn - 3] = list(range(5, 2048, 331))  # 7 choices
        got = run(cands, ["m/0/0"], ["", "x"], TARGET, procs, quiet=True, count_only=True)
        want = 22 * 7 * (2048 >> cs) * 2
        ok &= got == want
        print(f"  count  {wn:2}w survivors={got} expected={want} {'OK' if got == want else 'MISMATCH'}")
    # negative control: a wrong target must not hit
    miss = run([[IDX['abandon']]] * 11 + [list(range(2048))], ["m/44'/0'/0'/0/0"], [""],
               "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2", procs, quiet=True)
    ok &= miss is None
    print(f"  negative control -> {'no hit (OK)' if miss is None else 'FALSE HIT'}")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--procs", type=int, default=os.cpu_count())
    ap.add_argument("--target", default=TARGET)
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest(a.procs) else 1)
    cfg = load_config(a.config)
    cands = build_candidates(cfg)
    paths = cfg["paths"] or ["m/44'/0'/0'/0/0"]
    print(f"config {a.config}")
    hit = run(cands, paths, cfg["pass"], a.target, a.procs, cfg["distinct"])
    if hit:
        report_hit(hit, a.target)
        sys.exit(2)


if __name__ == "__main__":
    main()
