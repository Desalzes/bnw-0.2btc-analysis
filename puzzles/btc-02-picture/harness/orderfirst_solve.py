"""Vector D - constrain-and-verify BIP39 solver + search-space quantifier.

0.2 BTC "Brave New World" puzzle. READ-ONLY: derive -> compare to TARGET -> STOP.
No transaction, no sweep, no key broadcast (puzzlebox ADR-0001).

The reframe: stop blind 12!-permutation sweeps. Instead, take a per-slot candidate
vocabulary (constraints from the clock reads + thematic pool) and an ORDER (from the
clock-hand angles), then let the BIP39 checksum + address oracle finish it.

Checks BOTH compressed and uncompressed P2PKH (the puzzle's pubkey form is unknown).
Run with the SYSTEM python (has bip_utils + coincurve):
    python solve.py
"""
import math
import hashlib
import itertools

from bip_utils import (
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip44,
    Bip44Coins,
    Bip44Changes,
)
import coincurve

TARGET = "1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ"

# A safety cap: refuse to auto-run a sweep bigger than this many derivations
# without an explicit force=True (puzzlebox "no large brute-force" rail).
DERIVE_CAP = 2_000_000

_validator = Bip39MnemonicValidator()

# ---------------------------------------------------------------- base58check
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58check(payload: bytes) -> str:
    chk = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    n = int.from_bytes(payload + chk, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    for b in payload + chk:
        if b == 0:
            out = "1" + out
        else:
            break
    return out


def h160(b: bytes) -> bytes:
    return hashlib.new("ripemd160", hashlib.sha256(b).digest()).digest()


# ------------------------------------------------------------------- oracle
def is_valid(mnemonic: str) -> bool:
    """Cheap BIP39 checksum gate (no PBKDF2)."""
    return _validator.IsValid(mnemonic)


def addresses(mnemonic: str, passphrase: str = ""):
    """(compressed_p2pkh, uncompressed_p2pkh) at m/44'/0'/0'/0/0. Assumes valid checksum."""
    seed = Bip39SeedGenerator(mnemonic).Generate(passphrase)
    acc = (
        Bip44.FromSeed(seed, Bip44Coins.BITCOIN)
        .Purpose()
        .Coin()
        .Account(0)
        .Change(Bip44Changes.CHAIN_EXT)
        .AddressIndex(0)
    )
    comp = acc.PublicKey().ToAddress()
    priv = acc.PrivateKey().Raw().ToBytes()
    unc_pub = coincurve.PublicKey.from_valid_secret(priv).format(compressed=False)
    unc = b58check(b"\x00" + h160(unc_pub))
    return comp, unc


# ------------------------------------------------------------------- sanity
def sanity():
    # 1) compressed path vs harness.py's recorded value
    m = " ".join(["abandon"] * 11 + ["about"])
    comp, unc = addresses(m)
    assert comp == "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA", comp
    # 2) uncompressed path + base58check vs the canonical priv=1 vector
    one = (1).to_bytes(32, "big")
    unc_pub1 = coincurve.PublicKey.from_valid_secret(one).format(compressed=False)
    addr1 = b58check(b"\x00" + h160(unc_pub1))
    assert addr1 == "1EHNa6Q4Jz2uvNExL497mE43ikXhwF6kZm", addr1
    print(f"[sanity] OK  compressed={comp}  uncompressed(priv=1)={addr1}")


# --------------------------------------------------------------- quantifier
def quantify_ordered(slots, n_passphrases):
    """slots: list of candidate-lists, ORDER pinned. Returns (combos, est_valid, est_derives)."""
    combos = math.prod(len(s) for s in slots)
    est_valid = combos / 16.0          # ~1/16 of 12-word combos pass the BIP39 checksum
    est_derives = est_valid * n_passphrases
    return combos, est_valid, est_derives


def quantify_set_permute(n_sets, n_words, n_passphrases):
    combos = n_sets * math.factorial(n_words)
    est_valid = combos / 16.0
    est_derives = est_valid * n_passphrases
    return combos, est_valid, est_derives


def _fmt(x):
    return f"{x:,.0f}"


# ------------------------------------------------------------------- solver
def solve_ordered(slots, passphrases=("",), force=False, label=""):
    """Enumerate the Cartesian product of per-slot candidates (ORDER fixed),
    checksum-filter, derive (comp+unc) x passphrases, compare to TARGET. STOP on hit."""
    combos, est_valid, est_derives = quantify_ordered(slots, len(passphrases))
    print(f"[solve:{label}] slots sizes={[len(s) for s in slots]}")
    print(f"            combos={_fmt(combos)}  est_checksum_valid={_fmt(est_valid)}"
          f"  est_derives(x{len(passphrases)} pp)={_fmt(est_derives)}")
    if est_derives > DERIVE_CAP and not force:
        print(f"            >>> ABOVE DERIVE_CAP ({_fmt(DERIVE_CAP)}); NOT running."
              f" Pass force=True to override (operator sign-off).")
        return None
    checked = derived = 0
    for combo in itertools.product(*slots):
        m = " ".join(combo)
        checked += 1
        if not is_valid(m):
            continue
        for pp in passphrases:
            comp, unc = addresses(m, pp)
            derived += 1
            if comp == TARGET or unc == TARGET:
                print("\n!!!!!!!!!!!!!!!!!!!! TARGET MATCH !!!!!!!!!!!!!!!!!!!!")
                print(f"  mnemonic   = {m}")
                print(f"  passphrase = {pp!r}")
                print(f"  comp={comp}  unc={unc}")
                print("  STOP - recording candidate; operator decides. No tx.")
                return (m, pp, comp, unc)
    print(f"            done: checked={_fmt(checked)} derived={_fmt(derived)} -> NO MATCH")
    return None


if __name__ == "__main__":
    sanity()
    print("solver ready.")
