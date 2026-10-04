"""Set+position solver (CPU, multiprocessing) for BIP39, BIP39-raw-text or Electrum-v2 seeds.
READ-ONLY: compare-only; on a hit it prints, writes FOUND_setperm.txt, stops.

Model: some slots are FIXED; every REQUIRE word appears exactly once in one of
the remaining slots (all placements enumerated); the leftover slots are filled
from POOL (with order). Validity filter per scheme:
  bip39    : BIP39 checksum; seed salt "mnemonic"+passphrase
  electrum : HMAC-SHA512("Seed version", mnemonic) hex prefix "01" (standard,
             legacy P2PKH); seed salt "electrum"+passphrase; any word count
  bip39raw : NO filter - Electrum's "BIP39 seed" import of ANY text (Electrum
             3.3.8 keystore.bip39_to_seed: PBKDF2-HMAC-SHA512 of
             NFKD(' '.join(text.split())), salt "mnemonic"+NFKD(passphrase),
             2048 rounds; no word-count, wordlist or checksum test, no
             lowercasing). Any WORDS 1..24; tokens are free text, NFKD and
             nothing else (MOON and moon are two tokens); a token that is empty
             or holds whitespace after NFKD is refused. Every candidate that
             passes DISTINCT is derived.
Derivation: BIP32 from the seed, compressed P2PKH, every PATH listed.

Config ('#' starts a comment; on a FIX / REQUIRE / POOL / PASS / PASSFILE line a
'#' is REFUSED, because it would cut a token, a passphrase or a file name off
silently - comments there go on a line of their own, as below):
    SCHEME electrum
    WORDS 13
    FIX 3 tower
    # alternatives: every listed word is tried
    FIX 9 eye|sight|view
    FIX 13 moon
    REQUIRE subject food this real black one order
    POOL vote trust sing ...
    # the whole 2048-word list
    POOL @WORDLIST
    # PASS: repeatable; the empty passphrase is always tried
    PASS <passphrase>
    # PASSFILE: repeatable; one passphrase per line, added as PASS lines are;
    # lines VERBATIM (no '#' comments, spaces kept), blank lines skipped, a
    # leading UTF-8 BOM dropped; <file> is in the config's directory unless
    # absolute
    PASSFILE <file>
    PATH m/0/0                # repeatable
    DISTINCT                  # optional: no repeated words
    FREEWORDS                 # electrum only: allow words outside the BIP39
                              # list (Electrum checks a hash prefix, not a
                              # dictionary); tokens are Electrum-normalised,
                              # so BREATHE and breathe are one token
    NOCHECKSUM                # with SCHEME bip39 (either order) = SCHEME
                              # bip39raw; refused with SCHEME electrum

  python setperm_solve.py --selftest
  python setperm_solve.py CONFIG [--procs N] [--checkpoint FILE]
      --checkpoint: each finished unit (one REQUIRE placement) is appended and
      fsynced; rerunning the same command resumes from it.
"""
import argparse
import hashlib
import hmac
import itertools
import math
import os
import random
import sys
import time
import unicodedata
from multiprocessing import Pool

from template_solve import (IDX, TARGET, WORDS, address_of_hash160, checksum_ok,
                            hash160_of_address, parse_path, _ckd)
from coincurve import PrivateKey

HERE = os.path.dirname(os.path.abspath(__file__))
_VER = hmac.new(b"Seed version", digestmod=hashlib.sha512)
# bip39raw known answers for the selftest: the compressed P2PKH addresses of the L5 vectors V0-V3 and V5 (the file
# artifacts/external-reports-2026-10-01/finders/L5-files/known_answer_vectors.txt, re-derived with independent code by
# kav_independent.py; sp_test_raw holds these literals to that file). (vector, text, passphrase as typed, path,
# address): 13 words, upper case, a non-BIP39 token, no checksum, passphrase case kept, V5's passphrase typed in NFC
_ZW = "zoo wrong zoo wrong zoo wrong zoo wrong zoo wrong zoo"
_AB = " ".join(["abandon"] * 11 + ["about"])
RAW_KNOWN_ANSWERS = (
    ("V0", _AB, "", "m/44'/0'/0'/0/0", "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"),
    ("V1", _ZW + " wrong zoo", "", "m/44'/0'/0'/0/0", "155JeWn6UiXMftaD2nHzPsYHA1rnZJMhjx"),
    ("V1", _ZW + " wrong zoo", "", "m/44'/0'/0'/0/10", "18TjqBVpjqgyAx9dP1TyXvVXPtUSa13aCz"),
    ("V1", _ZW + " wrong zoo", "", "m/0'/0/0", "1B5YHBpJCmNiei1JWbDAK7NfVBy2Ao8vzU"),
    ("V1", _ZW + " wrong zoo", "", "m/0/0", "13ePFtqF7HKvcCGfrZFJpsp2fxMCDdcrnP"),
    ("V1", _ZW + " wrong zoo", "moon", "m/44'/0'/0'/0/0", "1Gz25UhhoN3gGxCMPZjQksQS4Gekxmi5R7"),
    ("V1", _ZW + " wrong zoo", "MOON", "m/44'/0'/0'/0/0", "1CddC4oefuiErwFM1FYPobsgwEXzds9nb"),
    ("V2", (_ZW + " wrong zoo").upper(), "", "m/44'/0'/0'/0/0", "1AXkr6uEyJSTu8DTQJGvFaowrs8ismdZ87"),
    ("V3", _ZW + " breathe", "", "m/44'/0'/0'/0/0", "16NbGKRBg85nfq5WmziBD5Nr7ZLrTrmFaU"),
    ("V5", _AB, "\u0447\u0451\u0440\u043d\u044b\u0439 \u0434\u0435\u043d\u044c", "m/44'/0'/0'/0/0",
     "1BGKQaECAZnFHfKUXmD4cdDVswyfS7G1p5"),
)


def electrum_standard(m):
    h = _VER.copy()
    h.update(m.encode())
    return h.digest()[0] == 0x01


def normalize_pass(pp, scheme):
    """The passphrase as the wallet uses it. BIP39 and bip39raw: NFKD, case
    kept. Electrum (mnemonic.normalize_text): NFKD, lowercased, accents dropped,
    whitespace collapsed - so 'BREATHE' and 'breathe' are ONE Electrum seed
    extension."""
    pp = unicodedata.normalize("NFKD", pp)
    if scheme == "electrum":
        pp = " ".join("".join(c for c in pp.lower() if not unicodedata.combining(c)).split())
    return pp


def seed_of(m, pp, scheme):
    salt = ("electrum" if scheme == "electrum" else "mnemonic") + pp
    return hashlib.pbkdf2_hmac("sha512", m.encode(), salt.encode(), 2048)


def hash160s_scheme(m, pp, paths, scheme):
    seed = seed_of(m, pp, scheme)
    I = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    cache = {(): (I[:32], I[32:])}
    out = []
    for path in paths:
        for d in range(1, len(path) + 1):
            if path[:d] not in cache:
                cache[path[:d]] = _ckd(*cache[path[:d - 1]], path[d - 1])
        pub = PrivateKey(cache[path][0]).public_key.format(True)
        out.append(hashlib.new("ripemd160", hashlib.sha256(pub).digest()).digest())
    return out


def raw_token_problem(w):
    """Why `w` cannot stand as a bip39raw token, or None. Electrum's BIP39 import derives
    NFKD(' '.join(text.split())) and nothing else, so a candidate's text is its tokens joined by single spaces
    exactly when every token is NFKD already and holds no whitespace (as str.split sees it): the joining space is a
    starter, so canonical reordering never crosses it and NFKD of the joined text is the join of the NFKD tokens."""
    if not w or w.split() != [w]:
        return "it is empty or holds whitespace (Electrum would split the text there)"
    if unicodedata.normalize("NFKD", w) != w:
        return "it is not NFKD-normalised (Electrum derives the NFKD text)"
    return None


def read_passfile(path):
    """The passphrases of a PASSFILE: one per line, VERBATIM except the line terminator (\\n or \\r\\n) - no '#'
    comments, leading, trailing and inner spaces kept; empty lines skipped; a UTF-8 BOM at the very start dropped.
    A file that cannot be read as UTF-8 is refused (sys.exit)."""
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        sys.exit(f"PASSFILE {path}: cannot read it as UTF-8 text ({e})")
    if text.startswith("\ufeff"):
        text = text[1:]
    out = []
    for line in text.split("\n"):         # not splitlines(): a lone \r, \x0c, U+2028 ... are part of a passphrase
        if line.endswith("\r"):
            line = line[:-1]
        if line:
            out.append(line)
    return out


def load_config(path):
    cfg = {"scheme": "bip39", "words": None, "fix": {}, "require": [], "pool": [],
           "pass": [""], "paths": [], "distinct": False}
    nochecksum = False
    with open(path, encoding="utf-8") as f:
        lines = list(f)
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, rest = line.partition(" ")
        key = key.upper()
        if "#" in raw and key in ("FIX", "REQUIRE", "POOL", "PASS", "PASSFILE"):
            # the comment split above would cut a token, a passphrase or a file name holding '#' off silently, and the
            # run would search a smaller space than the config states: nothing downstream can see that
            sys.exit(f"a '#' on a {key} line would cut the rest of the line off as a comment: put the comment on a "
                     f"line of its own (a passphrase holding '#' goes in a PASSFILE; a token cannot hold one): "
                     f"{raw.strip()!r}")
        if key == "SCHEME":
            cfg["scheme"] = rest.strip().lower()
        elif key == "WORDS":
            cfg["words"] = int(rest)
        elif key == "FIX":
            s, w = rest.split()
            cfg["fix"][int(s)] = w
        elif key == "REQUIRE":
            cfg["require"] += rest.split()
        elif key == "POOL":
            for w in rest.split():
                cfg["pool"] += WORDS if w == "@WORDLIST" else [w]
        elif key == "PASS":
            if rest not in cfg["pass"]:
                cfg["pass"].append(rest)
        elif key == "PASSFILE":
            name = rest.strip()
            if not name:
                sys.exit("PASSFILE needs a file name")
            for pp in read_passfile(os.path.join(os.path.dirname(os.path.abspath(path)), name)):
                if pp not in cfg["pass"]:              # exactly as a PASS line is added
                    cfg["pass"].append(pp)
        elif key == "PATH":
            cfg["paths"].append(rest.strip())
        elif key == "DISTINCT":
            cfg["distinct"] = True
        elif key == "FREEWORDS":
            cfg["freewords"] = True
        elif key == "NOCHECKSUM":
            nochecksum = True
        else:
            sys.exit(f"unknown directive {key!r}")
    if nochecksum:
        # SCHEME bip39 + NOCHECKSUM, in either order, IS SCHEME bip39raw (no flag is kept: the two configs are equal)
        if cfg["scheme"] == "electrum":
            sys.exit("NOCHECKSUM needs SCHEME bip39 (the two together are SCHEME bip39raw); SCHEME electrum has no "
                     "checksum to drop - its version-prefix test is what makes a text an Electrum seed")
        if cfg["scheme"] == "bip39":
            cfg["scheme"] = "bip39raw"
    assert cfg["scheme"] in ("bip39", "electrum", "bip39raw")
    if cfg.get("freewords"):
        if cfg["scheme"] == "bip39raw":
            sys.exit("FREEWORDS is for SCHEME electrum only: bip39raw needs no FREEWORDS (it takes every token as "
                     "written, NFKD-normalised, case kept)")
        if cfg["scheme"] != "electrum":
            sys.exit("FREEWORDS needs SCHEME electrum (the BIP39 checksum needs wordlist indices)")
        # Electrum normalises the whole seed text (mnemonic.normalize_text);
        # for whitespace-free, non-CJK tokens that equals normalising each
        # token, so it is done once here instead of per candidate
        def norm(w):
            if any(unicodedata.east_asian_width(c) in "WF" for c in w):
                sys.exit(f"FREEWORDS does not support CJK tokens: {w}")
            n = normalize_pass(w, "electrum")
            if not n or " " in n:
                sys.exit(f"token normalises to nothing or to several words: {w!r}")
            return n
        cfg["fix"] = {s: "|".join(norm(x) for x in v.split("|")) for s, v in cfg["fix"].items()}
        cfg["require"] = [norm(w) for w in cfg["require"]]
        cfg["pool"] = [norm(w) for w in cfg["pool"]]
    elif cfg["scheme"] == "bip39raw":
        # Electrum's BIP39 import: NFKD and nothing else - no wordlist test, no lowercasing (MOON != moon). Done once
        # here per token (see raw_token_problem for why that equals normalising the whole text)
        def token(w, alt=False):
            n = unicodedata.normalize("NFKD", w)
            why = raw_token_problem(n)
            if why:
                sys.exit(f"bip39raw token {w!r} (NFKD {n!r}): {why}")
            if alt and "|" in n:      # e.g. U+FF5C: re-joined, the alternative would silently split in two
                sys.exit(f"bip39raw FIX alternative {w!r} holds '|' after NFKD ({n!r}), the alternative separator")
            return n
        cfg["fix"] = {s: "|".join(token(x, alt=True) for x in v.split("|")) for s, v in cfg["fix"].items()}
        cfg["require"] = [token(w) for w in cfg["require"]]
        cfg["pool"] = [token(w) for w in cfg["pool"]]
    else:
        alts = [w for v in cfg["fix"].values() for w in v.split("|")]
        for w in alts + cfg["require"] + cfg["pool"]:
            if w not in IDX:
                sys.exit(f"not in wordlist: {w}")
    cfg["pool"] = list(dict.fromkeys(cfg["pool"]))
    return cfg


_G = {}


def _init(g):
    _G.update(g)


def _work(placement):
    """placement = tuple of free-slot positions receiving REQUIRE words in order."""
    n, fix, req, pool = _G["n"], _G["fix"], _G["require"], _G["pool"]
    slots = [None] * n
    alts = {s - 1: w.split("|") for s, w in fix.items()}
    for pos, w in zip(placement, req):
        slots[pos] = w
    rest = [i for i in range(n) if slots[i] is None and i not in alts]
    tested = derived = 0
    # nested generators, not product(product(...)), which would materialise
    # the whole pool product (4M tuples per worker at 2 unknowns over 2048)
    fills = ((afill, fill) for afill in itertools.product(*alts.values())
             for fill in itertools.product(pool, repeat=len(rest)))
    for afill, fill in fills:
        for i, w in zip(alts, afill):
            slots[i] = w
        for i, w in zip(rest, fill):
            slots[i] = w
        if _G["distinct"] and len(set(slots)) != n:
            continue
        tested += 1
        if _G["scheme"] == "electrum":
            m = " ".join(slots)
            if not electrum_standard(m):
                continue
        elif _G["scheme"] == "bip39raw":
            m = " ".join(slots)                    # no filter (and no wordlist index: IDX[w] would KeyError)
        else:
            if not checksum_ok([IDX[w] for w in slots]):
                continue
            m = " ".join(slots)
        if _G.get("count_only"):
            derived += len(_G["passes"])
            continue
        for pp in _G["passes"]:
            derived += 1
            for pi, h in enumerate(hash160s_scheme(m, pp, _G["paths"], _G["scheme"])):
                if h == _G["target"]:
                    return tested, derived, (m, pp, pi)
    return tested, derived, None


def _work_indexed(item):
    i, placement = item
    return (i, *_work(placement))


def run_signature(cfg, target_addr, paths_txt, passes):
    """Identifies a run, so a checkpoint is never resumed into a different one."""
    # FREEWORDS only extends the key when set, so older checkpoints still match
    key = repr((cfg["scheme"], cfg["words"], sorted(cfg["fix"].items()), cfg["require"],
                cfg["pool"], passes, paths_txt, target_addr, cfg["distinct"])
               + (("freewords",) if cfg.get("freewords") else ()))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def load_checkpoint(path, sig):
    """Unit index -> (tested, derived) for units already finished. Lines are
    'done <i> <tested> <derived> ok', appended + fsynced per unit; a line torn
    by a power cut lacks the trailing 'ok' and that unit simply runs again."""
    done = {}
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"SIG {sig}\n")
        return done
    with open(path, encoding="utf-8") as f:
        if f.readline().strip() != f"SIG {sig}":
            raise SystemExit(f"checkpoint {path} belongs to a different run - refusing to resume")
        for line in f:
            parts = line.split()
            if len(parts) == 5 and parts[0] == "done" and parts[4] == "ok":
                done[int(parts[1])] = (int(parts[2]), int(parts[3]))
    return done


def run(cfg, target_addr, procs, quiet=False, count_only=False, checkpoint=None):
    n = cfg["words"]
    fix = {s: w for s, w in cfg["fix"].items()}
    free = [i for i in range(n) if (i + 1) not in fix]
    req = cfg["require"]
    assert len(req) <= len(free), "more REQUIRE words than free slots"
    placements = list(itertools.permutations(free, len(req)))
    k = len(free) - len(req)
    nalt = math.prod(len(w.split("|")) for w in fix.values())
    raw = len(placements) * nalt * len(cfg["pool"]) ** k
    paths_txt = cfg["paths"] or (["m/0/0"] if cfg["scheme"] == "electrum" else ["m/44'/0'/0'/0/0"])
    rate = 1 / 256 if cfg["scheme"] == "electrum" else 1.0 if cfg["scheme"] == "bip39raw" else 1 / (1 << (n // 3))
    passes = list(dict.fromkeys(normalize_pass(p, cfg["scheme"]) for p in cfg["pass"]))
    if not quiet:
        print(f"{cfg['scheme']} {n}w | placements {len(placements):,} x FIX alternatives {nalt:,} x pool^{k} "
              f"({len(cfg['pool'])}) = raw {raw:,} | ~{raw * rate * len(passes):,.0f} "
              f"derivations x {len(paths_txt)} path(s) | pp {passes!r}", flush=True)
    g = dict(n=n, fix=fix, require=req, pool=cfg["pool"], scheme=cfg["scheme"],
             passes=passes, paths=[parse_path(p) for p in paths_txt],
             target=hash160_of_address(target_addr), distinct=cfg["distinct"],
             count_only=count_only)
    todo = list(enumerate(placements))
    done, ck = {}, None
    if checkpoint:
        done = load_checkpoint(checkpoint, run_signature(cfg, target_addr, paths_txt, passes))
        todo = [(i, p) for i, p in todo if i not in done]
        ck = open(checkpoint, "a", encoding="utf-8")
        if not quiet:
            print(f"checkpoint {checkpoint}: {len(done):,} of {len(placements):,} units already done, "
                  f"{len(todo):,} to run", flush=True)
    tested = sum(t for t, _ in done.values())
    derived = sum(d for _, d in done.values())
    t0, last, n_done = time.time(), time.time(), 0
    try:
        with Pool(procs, initializer=_init, initargs=(g,)) as pool:
            for i, t, d, hit in pool.imap_unordered(_work_indexed, todo, chunksize=1):
                tested += t
                derived += d
                if hit:
                    pool.terminate()
                    m, pp, pi = hit
                    return {"mnemonic": m, "passphrase": pp, "path": paths_txt[pi],
                            "scheme": cfg["scheme"]}
                n_done += 1
                if ck:
                    ck.write(f"done {i} {t} {d} ok\n")
                    ck.flush()
                    os.fsync(ck.fileno())
                if not quiet and time.time() - last > 300:
                    last = time.time()
                    left = (last - t0) / n_done * (len(todo) - n_done)
                    print(f"  progress {len(done) + n_done:,}/{len(placements):,} units | "
                          f"derived {derived:,} | ~{left / 60:.0f} min left", flush=True)
    finally:
        if ck:
            ck.close()
    if count_only:
        return tested, derived
    if not quiet:
        el = time.time() - t0
        print(f"no match | tested {tested:,} | derived {derived:,} in {el:.0f}s"
              f"{' (this session; totals include resumed units)' if done else ''}", flush=True)
    return None


def selftest(procs):
    rng = random.Random(922)
    ok = True
    for scheme, n in (("electrum", 13), ("bip39", 12), ("electrum", 12)):
        while True:
            ws = [rng.choice(WORDS) for _ in range(n)]
            m = " ".join(ws)
            if (electrum_standard(m) if scheme == "electrum"
                    else checksum_ok([IDX[w] for w in ws])) and len(set(ws)) == n:
                break
        paths = ["m/0/0", "m/44'/0'/0'/0/0"]
        tgt = address_of_hash160(hash160s_scheme(m, "", [parse_path(paths[0])], scheme)[0])
        # 7 free slots: 4 REQUIRE words placed anywhere among them, 3 from a pool
        free = [0, 4, 6, 8, 9, 10, n - 2]
        req = [0, 4, 8, 10]
        decoys = rng.sample([w for w in WORDS if w not in ws], 9)
        cfg = {"scheme": scheme, "words": n,
               "fix": {i + 1: ws[i] for i in range(n) if i not in free},
               "require": [ws[i] for i in req], "pass": [""], "paths": paths,
               "pool": decoys + [ws[i] for i in free if i not in req],
               "distinct": True}
        hit = run(cfg, tgt, procs, quiet=True)
        good = bool(hit) and hit["mnemonic"] == m and hit["path"] == paths[0]
        ok &= good
        print(f"  find {scheme:8} {n}w -> {'FOUND' if good else 'MISSED ' + repr(hit)}")
    # Electrum published standard-seed vector
    v = "cycle rocket west magnet parrot shuffle foot correct salt library feed song"
    a = address_of_hash160(hash160s_scheme(v, "", [parse_path("m/0/0")], "electrum")[0])
    good = electrum_standard(v) and a == "1NNkttn1YvVGdqBW4PR6zvc3Zx3H5owKRf"
    ok &= good
    print(f"  electrum vector -> {a} {'OK' if good else 'FAIL'}")
    # exact count: bip39 12w, 11 fixed, last slot over all 2048 -> exactly 128 valid
    cfg = {"scheme": "bip39", "words": 12, "fix": {i: "abandon" for i in range(1, 12)},
           "require": [], "pool": WORDS, "pass": [""], "paths": ["m/0/0"], "distinct": False}
    tested, derived = run(cfg, TARGET, procs, quiet=True, count_only=True)
    good = tested == 2048 and derived == 128
    ok &= good
    print(f"  count  tested={tested} valid={derived} (expect 2048/128) {'OK' if good else 'FAIL'}")
    # E1-shaped, through the config parser: electrum 13w, 7 FIX, 5 REQUIRE over
    # the 6 free slots, the last slot from POOL @WORDLIST
    while True:
        ws = [rng.choice(WORDS) for _ in range(13)]
        m = " ".join(ws)
        if electrum_standard(m) and len(set(ws)) == 13:
            break
    tgt = address_of_hash160(hash160s_scheme(m, "", [parse_path("m/0/1")], "electrum")[0])
    free = [1, 3, 4, 5, 6, 7]
    tmp = os.path.join(HERE, "_selftest_wordlist.conf")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("SCHEME electrum\nWORDS 13\nPOOL @WORDLIST\nPATH m/0/0\nPATH m/0/1\n")
        f.writelines(f"FIX {i + 1} {ws[i]}\n" for i in range(13) if i not in free)
        f.write("REQUIRE " + " ".join(ws[i] for i in free if i != 5) + "\n")
    try:
        c = load_config(tmp)
    finally:
        os.remove(tmp)
    hit = run(c, tgt, procs, quiet=True)
    good = len(c["pool"]) == 2048 and bool(hit) and hit["mnemonic"] == m and hit["path"] == "m/0/1"
    ok &= good
    print(f"  find electrum 13w, POOL @WORDLIST -> {'FOUND' if good else 'MISSED ' + repr(hit)}")
    # FIX alternatives: the true words hidden among decoys at two fixed slots
    decoys = [w for w in WORDS if w not in ws]
    fix = {i + 1: ws[i] for i in range(13) if i not in (1, 3)}
    fix[9] = "|".join(rng.sample(decoys, 6) + [ws[8]])
    fix[11] = "|".join([ws[10]] + rng.sample(decoys, 6))
    cfg = {"scheme": "electrum", "words": 13, "fix": fix, "require": [ws[1]],
           "pool": rng.sample(decoys, 5) + [ws[3]], "pass": [""], "paths": ["m/0/1"],
           "distinct": False}
    hit = run(cfg, tgt, procs, quiet=True)
    good = bool(hit) and hit["mnemonic"] == m
    ok &= good
    print(f"  find electrum 13w, FIX alternatives -> {'FOUND' if good else 'MISSED ' + repr(hit)}")
    # checkpoint: 2 units; unit 0 = REQUIRE at slot index 2 holds the seed
    cfg = {"scheme": "electrum", "words": 13, "fix": {i + 1: ws[i] for i in range(13) if i not in (2, 5)},
           "require": [ws[2]], "pool": rng.sample(decoys, 5) + [ws[5]], "pass": [""],
           "paths": ["m/0/1"], "distinct": False}
    sig = run_signature(cfg, tgt, ["m/0/1"], [""])
    ckp = os.path.join(HERE, "_selftest_checkpoint.txt")
    results = []
    for body in (f"SIG {sig}\ndone 0 6 0 ok\n",            # unit 0 done -> skipped, no hit
                 f"SIG {sig}\ndone 1 6 0 ok\ndone 0 6",    # torn line -> unit 0 re-runs, hit
                 "SIG 0000000000000000\n"):                # other run -> refused
        with open(ckp, "w", encoding="utf-8") as f:
            f.write(body)
        try:
            results.append(run(cfg, tgt, procs, quiet=True, checkpoint=ckp))
        except SystemExit:
            results.append("refused")
        finally:
            os.remove(ckp)
    good = results[0] is None and bool(results[1]) and results[1]["mnemonic"] == m and results[2] == "refused"
    ok &= good
    print(f"  checkpoint skip / torn-line resume / wrong-run refusal {'OK' if good else 'FAIL ' + repr(results)}")
    good = (normalize_pass("BREATHE", "electrum") == "breathe"
            and normalize_pass(" Café  X ", "electrum") == "cafe x"
            and normalize_pass("BREATHE", "bip39") == "BREATHE")
    ok &= good
    print(f"  passphrase normalisation (electrum lowercases, bip39 keeps case) {'OK' if good else 'FAIL'}")
    # BIP39 known answers: the official vector (passphrase TREZOR) and the
    # standard BIP44 first address of the same phrase with no passphrase
    ab = " ".join(["abandon"] * 11 + ["about"])
    good = (seed_of(ab, "TREZOR", "bip39").hex() ==
            "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e53495531f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04"
            and address_of_hash160(hash160s_scheme(ab, "", [parse_path("m/44'/0'/0'/0/0")], "bip39")[0])
            == "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA")
    ok &= good
    print(f"  bip39 known answers (TREZOR seed vector, 1LqBG... address) {'OK' if good else 'FAIL'}")
    # P1-shaped (clock_models.py): bip39 12w, passphrase MOON, target on the
    # third of five paths; 3 REQUIRE words over 4 free slots, 1 from @WORDLIST.
    # BIP39 keeps case, so the same config without 'MOON' must miss.
    while True:
        ws = [rng.choice(WORDS) for _ in range(12)]
        if checksum_ok([IDX[w] for w in ws]) and len(set(ws)) == 12:
            break
    m = " ".join(ws)
    paths = ["m/44'/0'/0'/0/0", "m/44'/0'/0'/0/1", "m/44'/0'/0'/0/10", "m/0'/0/0", "m/0/0"]
    tgt = address_of_hash160(hash160s_scheme(m, "MOON", [parse_path(paths[2])], "bip39")[0])
    free = [1, 3, 5, 7]
    body = ("SCHEME bip39\nWORDS 12\nPOOL @WORDLIST\n" + "".join(f"PATH {p}\n" for p in paths)
            + "".join(f"FIX {i + 1} {ws[i]}\n" for i in range(12) if i not in free)
            + "REQUIRE " + " ".join(ws[i] for i in free[:3]) + "\n")
    hits = []
    for passes in ("PASS moon\nPASS MOON\nPASS Moon\n", "PASS moon\nPASS Moon\n"):
        tmp = os.path.join(HERE, "_selftest_pass.conf")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body + passes)
        try:
            c = load_config(tmp)
        finally:
            os.remove(tmp)
        hits.append(run(c, tgt, procs, quiet=True))
    good = (bool(hits[0]) and hits[0]["mnemonic"] == m and hits[0]["passphrase"] == "MOON"
            and hits[0]["path"] == paths[2] and hits[1] is None)
    ok &= good
    print(f"  find bip39 12w, passphrase MOON on path 3 of 5 / miss without it "
          f"{'OK' if good else 'FAIL ' + repr(hits)}")
    # FREEWORDS: an Electrum-12 standard seed holding a non-BIP39 token. The
    # config writes it as BREATHE; Electrum normalises the seed text, so the
    # seed that must be found contains 'breathe'. Without FREEWORDS, or with
    # SCHEME bip39, the same config must be refused.
    assert "breathe" not in IDX
    while True:
        ws = rng.sample(WORDS, 11)
        ws.insert(5, "breathe")
        m = " ".join(ws)
        if electrum_standard(m):
            break
    tgt = address_of_hash160(hash160s_scheme(m, "", [parse_path("m/0/1")], "electrum")[0])
    free = [2, 7, 10]
    body = ("WORDS 12\nPOOL @WORDLIST\nPATH m/0/0\nPATH m/0/1\n"
            + "".join(f"FIX {i + 1} {ws[i].upper() if i == 5 else ws[i]}\n" for i in range(12) if i not in free)
            + "REQUIRE " + " ".join(ws[i] for i in free[:2]) + "\n")
    outcomes = []
    for head in ("SCHEME electrum\nFREEWORDS\n", "SCHEME electrum\n", "SCHEME bip39\nFREEWORDS\n"):
        tmp = os.path.join(HERE, "_selftest_free.conf")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(head + body)
        try:
            c = load_config(tmp)
            outcomes.append(run(c, tgt, procs, quiet=True))
        except SystemExit:
            outcomes.append("refused")
        finally:
            os.remove(tmp)
    good = (bool(outcomes[0]) and outcomes[0] != "refused" and outcomes[0]["mnemonic"] == m
            and outcomes[0]["path"] == "m/0/1" and outcomes[1] == "refused" and outcomes[2] == "refused")
    ok &= good
    print(f"  FREEWORDS: electrum seed with BREATHE found / refused without FREEWORDS / refused for bip39 "
          f"{'OK' if good else 'FAIL ' + repr(outcomes)}")
    # bip39raw known answers (RAW_KNOWN_ANSWERS), the passphrase normalised as a run does it
    bad = []
    for vid, m, pp, p, a in RAW_KNOWN_ANSWERS:
        h = hash160s_scheme(m, normalize_pass(pp, "bip39raw"), [parse_path(p)], "bip39raw")[0]
        if address_of_hash160(h) != a:
            bad.append((vid, pp, p))
    good = not bad
    ok &= good
    print(f"  bip39raw known answers ({len(RAW_KNOWN_ANSWERS)} L5 vector addresses: 13 words, upper case, a non-BIP39 "
          f"token, no checksum, passphrase case kept, an NFC passphrase NFKD-normalised) "
          f"{'OK' if good else 'FAIL ' + repr(bad)}")
    # bip39raw find, through the config parser: a 13-word text holding an upper-case token (MOON) and a non-BIP39 one
    # (breathe) - no checksum, no wordlist - found on the second of two paths; the same config with REQUIRE Moon
    # (written as SCHEME bip39 + NOCHECKSUM) must miss: bip39raw keeps case
    ws = rng.sample(WORDS, 11)
    ws.insert(3, "MOON")
    ws.insert(8, "breathe")
    m = " ".join(ws)
    tgt = address_of_hash160(hash160s_scheme(m, "", [parse_path("m/0/0")], "bip39raw")[0])
    free = [1, 3, 8, 11]
    body = ("WORDS 13\nPATH m/44'/0'/0'/0/0\nPATH m/0/0\n"
            + "".join(f"FIX {i + 1} {ws[i]}\n" for i in range(13) if i not in free)
            + "POOL " + " ".join(rng.sample([w for w in WORDS if w not in ws], 4) + [ws[1], ws[11]]) + "\n")
    hits = []
    for head, req in (("SCHEME bip39raw\n", "REQUIRE MOON breathe\n"),
                      ("SCHEME bip39\nNOCHECKSUM\n", "REQUIRE Moon breathe\n")):
        tmp = os.path.join(HERE, "_selftest_raw.conf")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(head + body + req)
        try:
            c = load_config(tmp)
        finally:
            os.remove(tmp)
        hits.append(run(c, tgt, procs, quiet=True))
    good = (bool(hits[0]) and hits[0]["mnemonic"] == m and hits[0]["path"] == "m/0/0"
            and hits[0]["scheme"] == "bip39raw" and hits[1] is None)
    ok &= good
    print(f"  find bip39raw 13w (MOON + breathe, no checksum) on path 2 of 2 / miss with Moon "
          f"{'OK' if good else 'FAIL ' + repr(hits)}")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--procs", type=int, default=os.cpu_count())
    ap.add_argument("--target", default=TARGET)
    ap.add_argument("--checkpoint", help="progress file; rerun the same command to resume")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest(a.procs) else 1)
    cfg = load_config(a.config)
    print(f"config {a.config}")
    hit = run(cfg, a.target, a.procs, checkpoint=a.checkpoint)
    if hit:
        msg = "".join(f"{k}: {v!r}\n" for k, v in hit.items()) + f"target: {a.target}\n"
        print("\n*** MATCH ***\n" + msg + "READ-ONLY: record in STATE.md under CANDIDATE KEY and STOP.")
        with open(os.path.join(HERE, "FOUND_setperm.txt"), "w", encoding="utf-8") as f:
            f.write(msg)
        sys.exit(2)


if __name__ == "__main__":
    main()
