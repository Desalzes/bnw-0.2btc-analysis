"""Electrum-NEW seed test for the 0.2 BTC puzzle (READ-ONLY, compare-only).

C1 ruled out Electrum-OLD (no complete candidate set is all-old-wordlist-valid).
Electrum-NEW (BIP39 wordlist + version-prefix HMAC) is viable only for the
wordlist-valid sets (1, 5, 7). This tests them under Electrum-new STANDARD
(legacy P2PKH at m/0/i) for a handful of plausible orders + passphrases.

Electrum-new: valid seed iff HMAC-SHA512(b"Seed version", norm_mnemonic) hex
starts with '01' (standard) or '100' (segwit). Root seed =
PBKDF2-HMAC-SHA512(mnemonic, "electrum"+passphrase, 2048, 64). Standard wallet
legacy receive addrs = P2PKH at m/0/i.
"""
import hashlib, hmac, unicodedata
from bip_utils import Bip32Slip10Secp256k1

PRIZE = "1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ"
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

SETS = {
    "set1": "moon tower food this real subject black weapon pyramid world brave face".split(),
    "set5": "power mask order black tower trumpet major benefit time proof receive pay liberty".split(),
    "set7": "subject camera food mask police life liberty world moon black tower real".split(),
}
PPS = ["", "11", "10", "ten", "X", "x"]


def b58c(b):
    n = int.from_bytes(b, "big"); s = ""
    while n: n, r = divmod(n, 58); s = _B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\x00"))) + s


def p2pkh(pub):
    h160 = hashlib.new("ripemd160", hashlib.sha256(pub).digest()).digest()
    payload = b"\x00" + h160
    return b58c(payload + hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4])


def norm(s):
    return unicodedata.normalize("NFKD", " ".join(s.split()).lower())


def electrum_new_addrs(words, pp, n=10):
    m = norm(" ".join(words))
    ver = hmac.new(b"Seed version", m.encode(), hashlib.sha512).hexdigest()
    stype = "standard" if ver.startswith("01") else ("segwit" if ver.startswith("100") else None)
    seed = hashlib.pbkdf2_hmac("sha512", m.encode(), ("electrum" + pp).encode(), 2048, 64)
    node = Bip32Slip10Secp256k1.FromSeed(seed)
    addrs = []
    for chain in (0, 1):                       # receive + change
        for i in range(n):
            pub = node.DerivePath(f"{chain}/{i}").PublicKey().RawCompressed().ToBytes()
            addrs.append(p2pkh(pub))
            ub = node.DerivePath(f"{chain}/{i}").PublicKey().RawUncompressed().ToBytes()
            addrs.append(p2pkh(ub))
    return stype, ver[:4], addrs


def orders(words):
    return {"as-given": words, "reversed": words[::-1], "sorted": sorted(words)}


def main():
    print(f"Electrum-NEW test vs {PRIZE}\n")
    valid_seen, hit = 0, None
    for sname, words in SETS.items():
        for oname, ws in orders(words).items():
            for pp in PPS:
                stype, vpfx, addrs = electrum_new_addrs(ws, pp)
                if stype:
                    valid_seen += 1
                    print(f"  {sname}/{oname}/pp={pp!r}: VALID Electrum-{stype} (ver {vpfx}) — {len(addrs)} addrs derived")
                if PRIZE in addrs:
                    hit = (sname, oname, pp, stype)
                    break
            if hit: break
        if hit: break
    if hit:
        print(f"\n*** MATCH *** {hit}")
        open("ELECTRUM_FOUND.txt", "w").write(f"{hit}\ntarget {PRIZE}\n")
    else:
        print(f"\nno match. ({valid_seen} of {len(SETS)*3*len(PPS)} (set,order,pp) combos were even VALID Electrum-new seeds)")
        print("note: full coverage needs a 12!-ordering sweep filtered by version prefix (GPU-scale).")


if __name__ == "__main__":
    main()
