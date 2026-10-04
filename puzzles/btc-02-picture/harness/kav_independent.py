"""Independent re-derivation of the raw-text known-answer vectors (READ-ONLY, synthetic phrases only).

  uv run --offline --no-project --python 3.12 --with ecdsa python kav_independent.py

Re-derives every line of artifacts/external-reports-2026-10-01/finders/L5-files/known_answer_vectors.txt with code
that shares NOTHING with the engine or the CPU solvers (no harness import): a hand-rolled PBKDF2 over hashlib.sha512,
BIP32 on the `ecdsa` package, its own Base58Check. The semantics are the ones read from the Electrum 3.3.8 source
(keystore.bip39_to_seed): text = NFKD(' '.join(text.split())), salt = 'mnemonic' + NFKD(passphrase), 2048 rounds,
no word-count, wordlist or checksum test. The native Electrum vector (V4) uses salt 'electrum' + the normalised
extension (NFKD, lowercased, combining marks dropped, whitespace collapsed) and must carry the '01' version prefix.
Exit 0 only if every address in the file is reproduced.
"""
import hashlib
import os
import re
import sys
import unicodedata

from ecdsa import SECP256k1, SigningKey

HERE = os.path.dirname(os.path.abspath(__file__))
VECTORS = os.path.join(HERE, "..", "artifacts", "external-reports-2026-10-01", "finders", "L5-files",
                       "known_answer_vectors.txt")
N = SECP256k1.order
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
ZW = "zoo wrong zoo wrong zoo wrong zoo wrong zoo wrong zoo"          # 11 words
TEXT = {"V0": " ".join(["abandon"] * 11 + ["about"]),
        "V1": ZW + " wrong zoo",                                       # 13 words: BIP39 forbids the length
        "V2": (ZW + " wrong zoo").upper(),                             # the raw text keeps its case
        "V3": ZW + " breathe",                                         # 12 words, bad checksum, a non-BIP39 token
        "V4": ZW + " action",                                          # Electrum-native, '01' prefix
        "V5": " ".join(["abandon"] * 11 + ["about"])}


def hmac_sha512(key, msg):
    if len(key) > 128:
        key = hashlib.sha512(key).digest()
    key = key.ljust(128, b"\0")
    inner = hashlib.sha512(bytes(b ^ 0x36 for b in key) + msg).digest()
    return hashlib.sha512(bytes(b ^ 0x5C for b in key) + inner).digest()


def pbkdf2_sha512(password, salt, rounds=2048):
    u = hmac_sha512(password, salt + b"\0\0\0\1")
    t = int.from_bytes(u, "big")
    for _ in range(rounds - 1):
        u = hmac_sha512(password, u)
        t ^= int.from_bytes(u, "big")
    return t.to_bytes(64, "big")


def pub(k, compressed):
    p = SigningKey.from_secret_exponent(k, curve=SECP256k1).verifying_key.pubkey.point
    x, y = p.x().to_bytes(32, "big"), p.y().to_bytes(32, "big")
    return (b"\x03" if p.y() & 1 else b"\x02") + x if compressed else b"\x04" + x + y


def derive(seed, path):
    i64 = hmac_sha512(b"Bitcoin seed", seed)
    k, c = int.from_bytes(i64[:32], "big"), i64[32:]
    for part in [p for p in path.split("/")[1:] if p]:
        hard = part.endswith("'")
        i = int(part.rstrip("'")) + (0x80000000 if hard else 0)
        data = (b"\0" + k.to_bytes(32, "big") if hard else pub(k, True)) + i.to_bytes(4, "big")
        i64 = hmac_sha512(c, data)
        k, c = (int.from_bytes(i64[:32], "big") + k) % N, i64[32:]
    return k


def address(pubkey):
    raw = b"\0" + hashlib.new("ripemd160", hashlib.sha256(pubkey).digest()).digest()
    raw += hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    n, s = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + s


def nfkd(s):
    return unicodedata.normalize("NFKD", s)


def electrum_norm(s):
    return " ".join("".join(ch for ch in nfkd(s).lower() if not unicodedata.combining(ch)).split())


def seed_for(vid, note):
    """(seed, what) for one vector line; `note` is the middle column of the file."""
    text = nfkd(" ".join(TEXT[vid].split()))
    if vid == "V4":
        ext = re.search(r"ext='([^']*)'", note).group(1)
        assert hmac_sha512(b"Seed version", text.encode()).hex().startswith("01"), "V4 lacks the 01 prefix"
        return pbkdf2_sha512(text.encode(), b"electrum" + electrum_norm(ext).encode())
    if vid == "V5":
        pp = "чёрный день"
        pp = nfkd(pp) if "NFKD" in note else unicodedata.normalize("NFC", pp)
        return pbkdf2_sha512(text.encode(), b"mnemonic" + pp.encode())
    m = re.search(r"pass='([^']*)'", note)
    return pbkdf2_sha512(text.encode(), b"mnemonic" + nfkd(m.group(1) if m else "").encode())


def main():
    line_re = re.compile(r"^(V\d)\s+(.*?)\s+(m(?:/\d+'?)*)\s+C:(\S+)(?:\s+U:(\S+))?\s*$")
    checked = bad = 0
    for raw in open(VECTORS, encoding="utf-8"):
        m = line_re.match(raw.rstrip("\n"))
        if not m:
            continue
        vid, note, path, want_c, want_u = m.groups()
        k = derive(seed_for(vid, note), path)
        for form, want in (("C", want_c), ("U", want_u)):
            if want is None:
                continue
            got = address(pub(k, form == "C"))
            checked += 1
            if got != want:
                bad += 1
                print(f"MISMATCH {vid} [{note}] {path} {form}: file {want}, independent {got}")
    print(f"known-answer vectors: {checked} addresses re-derived independently, {bad} mismatch(es)")
    return 0 if checked == 30 and not bad else 1          # 30 = every address in the pinned file (SHA256SUMS)


if __name__ == "__main__":
    sys.exit(main())
