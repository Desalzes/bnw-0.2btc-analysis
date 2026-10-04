"""Shared helpers for the sp_test_* modules."""
import os
import random
import tempfile
import unittest

import setperm_solve as sps
from template_solve import WORDS


def make_text(scheme, n, fix, require, pool, passes=(), paths=(), distinct=False, freewords=False):
    """A .conf text. `fix` is a list of (slot, 'w' or 'a|b') in FILE order."""
    lines = [f"SCHEME {scheme}", f"WORDS {n}"]
    if freewords:
        lines.append("FREEWORDS")
    lines += [f"FIX {s} {w}" for s, w in fix]
    if require:
        lines.append("REQUIRE " + " ".join(require))
    lines += [f"POOL {p}" for p in pool]
    lines += [f"PASS {p}" for p in passes]
    lines += [f"PATH {p}" for p in paths]
    if distinct:
        lines.append("DISTINCT")
    return "\n".join(lines) + "\n"


def cfg_from_text(text):
    """Parse through the real setperm_solve.load_config."""
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False, encoding="utf-8") as f:
        f.write(text)
        path = f.name
    try:
        return sps.load_config(path)
    finally:
        os.remove(path)


def small_config_texts():
    """Fifteen tiny configs covering FIX alternatives (file order != slot order),
    REQUIRE placements, POOL, DISTINCT, FREEWORDS, k=0, r=0, duplicates,
    bip39 12/15, electrum 12/13 and bip39raw 7/12/13/24 (no filter: every
    candidate survives; one written as SCHEME bip39 + NOCHECKSUM). Each has
    <= ~100k raw candidates; each bip39raw one fewer than 10,000."""
    rng = random.Random(2026)

    def ws(k):
        return rng.sample(WORDS, k)

    def fixes(n, free, alts=None, custom=None):
        """FIX lines for every slot not in `free`; slots in `alts` get that many random
        alternatives, slots in `custom` get the given text. Emitted in a SHUFFLED file
        order so file order != slot order."""
        alts, custom = alts or {}, custom or {}
        pairs = []
        for s in range(1, n + 1):
            if s in free:
                continue
            if s in custom:
                pairs.append((s, custom[s]))
            else:
                pairs.append((s, "|".join(ws(alts[s])) if s in alts else ws(1)[0]))
        rng.shuffle(pairs)
        return pairs

    out = []
    # A bip39-12: 4 free slots, REQUIRE 3, k=1 over 40 pool words, 2 passphrases, 2 paths
    out.append(("A_bip39_12_r3k1", make_text(
        "bip39", 12, fixes(12, {4, 7, 9, 12}), ws(3), [" ".join(ws(40))],
        passes=("moon", "MOON"), paths=("m/44'/0'/0'/0/0", "m/0/0"))))
    # B bip39-12 DISTINCT: 3 free slots, REQUIRE 1, k=2 over 30 words, two FIX slots with alternatives
    out.append(("B_bip39_12_distinct_alts", make_text(
        "bip39", 12, fixes(12, {2, 6, 10}, alts={3: 2, 8: 3}), ws(1), [" ".join(ws(30))], distinct=True)))
    # C bip39-15: 3 free slots, REQUIRE 2, k=1 over the whole wordlist
    out.append(("C_bip39_15_wordlist", make_text(
        "bip39", 15, fixes(15, {3, 8, 14}), ws(2), ["@WORDLIST"])))
    # D electrum-12: 4 free slots, REQUIRE 3, k=1 over the whole wordlist
    out.append(("D_electrum_12_wordlist", make_text(
        "electrum", 12, fixes(12, {1, 5, 7, 12}), ws(3), ["@WORDLIST"])))
    # E electrum-13: 4 free slots, REQUIRE 2, k=2 over 45 words, alternatives at 2 slots
    out.append(("E_electrum_13_alts", make_text(
        "electrum", 13, fixes(13, {2, 4, 9, 13}, alts={1: 2, 6: 2}), ws(2), [" ".join(ws(45))],
        paths=("m/0/0", "m/0/1", "m/1/0"))))
    # F electrum-12 FREEWORDS with non-BIP39 tokens in a FIX alternative, REQUIRE and POOL
    #   (3 free slots, REQUIRE 2, k=1 over the wordlist + 'Quixotic'; ~49k raw)
    out.append(("F_electrum_12_freewords", make_text(
        "electrum", 12, fixes(12, {2, 5, 9}, alts={4: 2}, custom={6: "breathe|zzyzx"}),
        ["BREATHE"] + ws(1), ["@WORDLIST", "Quixotic"], freewords=True)))
    # G bip39-12, k=0 (no pool), REQUIRE fills all 4 free slots, one 3-way alternative
    out.append(("G_bip39_12_k0", make_text(
        "bip39", 12, fixes(12, {1, 4, 8, 11}, alts={2: 3}), ws(4), [])))
    # H bip39-12, r=0 (no REQUIRE), k=2 over 25 words
    out.append(("H_bip39_12_r0k2", make_text(
        "bip39", 12, fixes(12, {5, 9}), [], [" ".join(ws(25))])))
    # I electrum-13, r=0, k=1 over the whole wordlist (one unit)
    out.append(("I_electrum_13_r0k1", make_text(
        "electrum", 13, fixes(13, {7}), [], ["@WORDLIST"])))
    # J bip39-12: a REQUIRE word twice (every candidate has a twin), DISTINCT off.
    #   4 free slots, REQUIRE 3, k=1 over 12 words: 288 raw, ~18 survivors
    dup = ws(1)[0]
    out.append(("J_bip39_12_dups", make_text(
        "bip39", 12, fixes(12, {3, 6, 9, 11}), [dup, dup, ws(1)[0]], [" ".join(ws(12))])))
    # K bip39-12 DISTINCT on, pool words that also appear in FIX slots (some candidates repeat a word)
    fx = fixes(12, {3, 6, 9, 11})
    out.append(("K_bip39_12_distinct_overlap", make_text(
        "bip39", 12, fx, ws(3), [" ".join([w for _, w in fx][:4] + ws(10))], distinct=True)))
    # ---- bip39raw: no filter, so every (DISTINCT-passing) candidate survives and goes through K2. Appended after K
    # (the draws above are unchanged) and each kept under 10,000 tested: the tests that judge the binomial check on
    # blocks of >= 10,000 tested presume a filter and see none of these (the exact check has tests of its own)
    # L bip39raw-13: case kept and free tokens in a FIX alternative (TOWER|tower|Tower are three tokens), REQUIRE
    #   (MOON, quixotic) and POOL (Zoo, BREATHE next to a FIXed 'breathe'); a passphrase and two paths.
    #   4 free slots, REQUIRE 2, k=2 over 10: 3,600 raw
    out.append(("L_raw_13_case_free", make_text(
        "bip39raw", 13, fixes(13, {2, 6, 9, 13}, custom={4: "TOWER|tower|Tower", 11: "breathe"}),
        ["MOON", "quixotic"], [" ".join(ws(8) + ["Zoo", "BREATHE"])], passes=("MOON",),
        paths=("m/44'/0'/0'/0/0", "m/0/0"))))
    # M SCHEME bip39 + NOCHECKSUM (the alias of bip39raw), 12 words, DISTINCT: two pool words also FIXed, the REQUIRE
    #   word also in the pool, one pool word written twice (load_config keeps one). 3 free, REQUIRE 1, k=2 over 17: 867
    fx = fixes(12, {3, 7, 10})
    req = ws(1)
    pool = [w for _, w in fx][:2] + req + ws(14)
    out.append(("M_raw_12_nochecksum_distinct", make_text(
        "bip39", 12, fx, req, [" ".join(pool + pool[3:4])], distinct=True) + "NOCHECKSUM\n"))
    # N bip39raw-7, r=0: k=2 over 40 tokens, six of them changed by NFKD (NFC cafe/naive, 'чёрный' at the 16-byte
    #   limit, 'день' (unchanged), fullwidth MOON -> MOON, the 'fi' ligature -> fix). 1,600 raw
    out.append(("N_raw_7_r0_unicode", make_text(
        "bip39raw", 7, fixes(7, {2, 5}), [],
        [" ".join(ws(34) + ["caf\u00e9", "na\u00efve", "\u0447\u0451\u0440\u043d\u044b\u0439", "\u0434\u0435\u043d\u044c",
                           "\uff2d\uff2f\uff2f\uff2e", "\ufb01x"])])))
    # O bip39raw-24 (the longest): an upper-case REQUIRE word over 2 free slots, k=1 over the whole wordlist, a
    #   passphrase and two paths. 4,096 raw
    out.append(("O_raw_24_wordlist", make_text(
        "bip39raw", 24, fixes(24, {1, 24}), ["ABANDON"], ["@WORDLIST"], passes=("TREZOR",),
        paths=("m/44'/0'/0'/0/0", "m/0'/0/0"))))
    return out


def _gpu_ok():
    if os.environ.get("SP_NO_GPU") == "1":
        return False
    try:
        import pyopencl as cl
        return any("NVIDIA" in p.name and p.get_devices() for p in cl.get_platforms())
    except Exception:
        return False


HAVE_GPU = _gpu_ok()
require_gpu = unittest.skipUnless(HAVE_GPU, "no NVIDIA OpenCL device (or SP_NO_GPU=1)")
