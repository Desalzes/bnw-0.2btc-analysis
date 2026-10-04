"""Derive explicit strings exactly as written and compare with the prize address (READ-ONLY, CPU only, compare-only).

  PY exact_strings.py --signoff "who approved what, and when" [--passfile FILE] [--only S05,S06]

For a handful of fixed texts the GPU engine is the wrong tool (it wants a search space with survivors to plant in): this
derives each text on the CPU with the engine's own reference code (setperm_solve.seed_of, sp_ref.hash160_forms_seed:
both key forms) and prints what it derived. It never builds, signs or broadcasts anything. On a match it prints,
appends to FOUND_exact_strings.txt and exits 2: record it in STATE.md under '## !!! CANDIDATE KEY' and STOP.

Inputs (artifacts/external-reports-2026-10-01/finders/L5-files/):
  community_strings.txt            S01-S18, E01, T01: id TAB word count TAB text (the bitcointalk digest's sequences)
  electrum_valid_image_texts.txt   21 art texts that pass Electrum's '01' version test (one per line)
Each text is taken as written and, where that differs, in lower case. Two readings of every text:
  electrum   native Electrum seed: text normalised as Electrum does (NFKD, lower case, combining marks dropped, spaces
             collapsed); only if HMAC-SHA512("Seed version", text) starts with '01' (standard: m/0/0-19, m/1/0-5) or
             '100' (segwit: m/0'/0/0-19, m/0'/1/0-5; the hash160 is compared all the same); salt 'electrum' + extension,
             extensions '' and 'moon'
  bip39raw   Electrum's "BIP39 seed" import / python-mnemonic to_seed: PBKDF2 of NFKD(' '.join(text.split())), case
             kept, salt 'mnemonic' + NFKD(passphrase), no checksum or wordlist test; passphrases '', MOON, moon, Moon
             (+ --passfile lines); paths: BIP44 account 0 receive 0-19 and change 0-5, m/0'/0/0, m/0/0 and every
             batch of paths_L5.txt
Exit codes: 0 no match, 2 FOUND, 3 refused (no sign-off).
"""
import argparse
import hashlib
import hmac
import os
import sys
import time
import unicodedata

import setperm_solve as sps
import sp_ref
from template_solve import TARGET, address_of_hash160, hash160_of_address, parse_path

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "..", "artifacts", "external-reports-2026-10-01", "finders", "L5-files")
FOUND_PATH = os.path.join(HERE, "FOUND_exact_strings.txt")
E_STD = [f"m/0/{i}" for i in range(20)] + [f"m/1/{i}" for i in range(6)]
E_SEGWIT = [f"m/0'/0/{i}" for i in range(20)] + [f"m/0'/1/{i}" for i in range(6)]
B_BASE = [f"m/44'/0'/0'/0/{i}" for i in range(20)] + [f"m/44'/0'/0'/1/{i}" for i in range(6)] + ["m/0'/0/0", "m/0/0"]


def l5_paths():
    out = []
    for line in open(os.path.join(ART, "paths_L5.txt"), encoding="utf-8"):
        if line.startswith("m"):
            out.append(line.strip())
    return out


def load_texts(only):
    texts = []
    for line in open(os.path.join(ART, "community_strings.txt"), encoding="utf-8"):
        sid, _n, text = line.rstrip("\n").split("\t")
        texts.append((sid, text))
    for i, line in enumerate(open(os.path.join(ART, "electrum_valid_image_texts.txt"), encoding="utf-8"), 1):
        if line.strip():
            texts.append((f"IMG{i:02d}", line.rstrip("\n")))
    return [(s, t) for s, t in texts if not only or s in only]


def version_prefix(norm_text):
    return hmac.new(b"Seed version", norm_text.encode("utf-8"), hashlib.sha512).hexdigest()


def selfcheck():
    """The same calls the run makes, on four of the known-answer vectors (L5-files/known_answer_vectors.txt; every
    address there was re-derived by kav_independent.py with code that shares nothing with this). False = do not run."""
    zw = "zoo wrong zoo wrong zoo wrong zoo wrong zoo wrong zoo"
    cases = [("bip39raw", zw + " wrong zoo", "", "m/44'/0'/0'/0/0", "155JeWn6UiXMftaD2nHzPsYHA1rnZJMhjx", "1aLPxUUYfJfQPw6UfZXnPzGyKveFuMsvW"),
             ("bip39raw", (zw + " wrong zoo").upper(), "", "m/44'/0'/0'/0/0", "1AXkr6uEyJSTu8DTQJGvFaowrs8ismdZ87", "1B1BEAXW3jGZdmrwGsTLyowuphi7wAV4zG"),
             ("bip39raw", zw + " breathe", "", "m/44'/0'/0'/0/0", "16NbGKRBg85nfq5WmziBD5Nr7ZLrTrmFaU", "19NmzDdAQ4nBys48CW96GCWD1vxwW3s6Uo"),
             ("bip39raw", zw + " wrong zoo", "MOON", "m/44'/0'/0'/0/0", "1CddC4oefuiErwFM1FYPobsgwEXzds9nb", "1PU4VLtfVuZusPjxwKb3rv6UuhSAevBFSs"),
             ("electrum", zw + " action", "MOON", "m/0/0", "1BaMtToafc7yT7yQfj5ELfyRFV2BdazHZi", "1H2JeMSNcpHFEjGjprVwXQv21wKD16KJzR"),
             ("electrum", zw + " action", "", "m/1/0", "1GuzH2zpiVNcpmeVXQihEuVuoV7HRXM2Gz", "13mhwTCdh6fxUg4NojwCJXqBkA9eaZymxK")]
    for scheme, text, pw, path, want_c, want_u in cases:
        if scheme == "electrum":
            text = sps.normalize_pass(text, "electrum")
            if not version_prefix(text).startswith("01"):
                return False
        else:
            text = unicodedata.normalize("NFKD", " ".join(text.split()))
        got = {f: address_of_hash160(h) for _, f, h in sp_ref.hash160_forms_seed(
            sps.seed_of(text, sps.normalize_pass(pw, scheme), scheme), [parse_path(path)])}
        if got != {0: want_c, 1: want_u}:
            return False
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--signoff", help="the operator's sign-off in words; the run is refused without it")
    ap.add_argument("--passfile", help="extra BIP39 passphrases, one per line, verbatim")
    ap.add_argument("--only", help="comma-separated ids (S05,S06,IMG03)")
    a = ap.parse_args()
    if not (a.signoff or "").strip():
        print("REFUSED: a derivation against the prize address needs the operator's sign-off (ADR-0001 rail 4): --signoff TEXT")
        return 3
    if not selfcheck():
        print("cannot run: the known-answer self-check failed (the derivation code is broken)")
        return 1
    print("known-answer self-check passed (6 vectors, both key forms: raw 13 words, upper case, a non-BIP39 token, a passphrase; Electrum native)")
    target = hash160_of_address(TARGET)
    passes = ["", "MOON", "moon", "Moon"]
    if a.passfile:
        for line in open(a.passfile, encoding="utf-8-sig"):
            p = line.rstrip("\r\n")
            if p and p not in passes:
                passes.append(p)
    passes = list(dict.fromkeys(sps.normalize_pass(p, "bip39raw") for p in passes))
    b_paths = list(dict.fromkeys(B_BASE + l5_paths()))
    pp = {k: [parse_path(p) for p in v] for k, v in (("std", E_STD), ("segwit", E_SEGWIT), ("raw", b_paths))}
    print(f"operator sign-off: {a.signoff.strip()}")
    print(f"target {TARGET} | bip39raw: {len(passes)} passphrase(s) x {len(b_paths)} paths x 2 key forms | electrum: "
          f"extensions '' and 'moon' x {len(E_STD)} paths x 2 key forms")
    seeds = addrs = 0
    t0 = time.time()

    def check(seed, paths, paths_txt, what):
        nonlocal seeds, addrs
        seeds += 1
        for path_i, form, h in sp_ref.hash160_forms_seed(seed, paths):
            addrs += 1
            if h == target:
                msg = (f"{what}\npath: {paths_txt[path_i]}\nform: {'compressed' if form == 0 else 'uncompressed'}\n"
                       f"target: {TARGET}\n")
                print("\n*** MATCH ***\n" + msg + "READ-ONLY: record in STATE.md under '## !!! CANDIDATE KEY' and STOP. "
                      "Claiming is the operator's decision.")
                with open(FOUND_PATH, "a", encoding="utf-8") as f:
                    f.write(f"# FOUND {time.strftime('%Y-%m-%dT%H:%M:%S')}\n{msg}\n")
                    f.flush()
                    os.fsync(f.fileno())
                sys.exit(2)

    for sid, text in load_texts(set(a.only.split(",")) if a.only else None):
        variants = list(dict.fromkeys([" ".join(text.split()), " ".join(text.split()).lower()]))
        enorm = sps.normalize_pass(text, "electrum")              # Electrum folds case: one native text per string
        ver = version_prefix(enorm)
        kind = "std" if ver.startswith("01") else "segwit" if ver.startswith("100") else None
        n_e = 0
        if kind:
            for ext in ("", "moon"):
                check(sps.seed_of(enorm, sps.normalize_pass(ext, "electrum"), "electrum"), pp[kind],
                      E_STD if kind == "std" else E_SEGWIT, f"scheme: electrum native ({kind})\ntext: {enorm!r}\nextension: {ext!r}")
                n_e += 1
        for v in variants:
            raw = unicodedata.normalize("NFKD", v)
            for p in passes:
                check(sps.seed_of(raw, p, "bip39raw"), pp["raw"], b_paths, f"scheme: bip39raw\ntext: {raw!r}\npassphrase: {p!r}")
        print(f"  {sid:6s} {len(text.split()):2d} words | Seed version {ver[:8]} -> "
              f"{'electrum ' + kind + ', ' + str(n_e) + ' seeds' if kind else 'not an Electrum seed'} | bip39raw "
              f"{len(variants)} variant(s) x {len(passes)} passphrase(s) | {text if len(text) <= 60 else text[:57] + '...'}")
    print(f"NO MATCH: {seeds:,} seeds, {addrs:,} addresses (both key forms) derived on the CPU in {time.time() - t0:.0f} s; "
          f"each text exactly as listed above, nothing wider")
    return 0


if __name__ == "__main__":
    sys.exit(main())
