"""Read-only 0.2 BTC puzzle harness (verified).

Derivation matches the community panchpasha script: 12-word BIP39 -> seed ->
BIP44 m/44'/0'/0'/0/0 -> Legacy P2PKH. NO transaction construction, NO key
sweeping. Candidate phrase -> address -> compare to target only.

Run with the bip_utils venv:
    ../../../scratch/venv/Scripts/python.exe harness.py
"""
import time
import math
from itertools import permutations

from bip_utils import (
    Bip39MnemonicValidator,
    Bip39SeedGenerator,
    Bip44,
    Bip44Coins,
    Bip44Changes,
)

TARGET = "1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ"

# Canonical all-zero-entropy mnemonic -> iancoleman BIP44 acct0 addr0.
# (Verified-derived value; the BIP39 seed also matches the official Trezor vector.)
SANITY_MNEMONIC = " ".join(["abandon"] * 11 + ["about"])
SANITY_EXPECTED = "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"

# Primary clue-derived 12-word candidate set (all valid BIP39), as recorded.
PRIMARY = "moon tower food real black subject time proof only win world face".split()

_validator = Bip39MnemonicValidator()


def is_valid(mnemonic: str) -> bool:
    """Cheap BIP39 checksum check (no expensive seed gen)."""
    return _validator.IsValid(mnemonic)


def derive(mnemonic: str) -> str:
    """Full derivation: PBKDF2-2048 seed + BIP44 P2PKH. Assumes valid checksum."""
    seed = Bip39SeedGenerator(mnemonic).Generate()
    acc = (
        Bip44.FromSeed(seed, Bip44Coins.BITCOIN)
        .Purpose()
        .Coin()
        .Account(0)
        .Change(Bip44Changes.CHAIN_EXT)
        .AddressIndex(0)
    )
    return acc.PublicKey().ToAddress()


def sanity():
    ok_valid = is_valid(SANITY_MNEMONIC)
    addr = derive(SANITY_MNEMONIC)
    print(f"[sanity] checksum_valid={ok_valid} addr={addr} "
          f"matches_known={addr == SANITY_EXPECTED}")


def test_known():
    m = " ".join(PRIMARY)
    v = is_valid(m)
    print(f"[known] '{m}'")
    print(f"        checksum_valid={v}")
    if v:
        a = derive(m)
        print(f"        addr={a} HIT={a == TARGET}")
    else:
        print("        (given order fails checksum; would be skipped in a sweep)")


def benchmark(words):
    n = len(words)
    total_perms = math.factorial(n)
    sample = 200_000
    t0 = time.time()
    valid = 0
    seen = 0
    for p in permutations(words):
        if is_valid(" ".join(p)):
            valid += 1
        seen += 1
        if seen >= sample:
            break
    t_val = time.time() - t0
    rate_val = seen / t_val
    frac_valid = valid / seen
    t1 = time.time()
    derived = 0
    for p in permutations(words):
        m = " ".join(p)
        if is_valid(m):
            derive(m)
            derived += 1
            if derived >= 1500:
                break
    t_der = time.time() - t1
    rate_der = derived / t_der if t_der else 0
    est_valid_total = total_perms * frac_valid
    est_sweep_s = est_valid_total / rate_der if rate_der else float("inf")
    print(f"[bench] words={n} total_perms={total_perms:,}")
    print(f"        valid-checksum rate={frac_valid:.4f} (~1/{1/frac_valid:.1f})")
    print(f"        validate {rate_val:,.0f}/s ; derive {rate_der:,.0f}/s")
    print(f"        est checksum-valid mnemonics={est_valid_total:,.0f}")
    print(f"        est FULL single-set sweep (1 core): "
          f"{est_sweep_s/3600:,.1f} h  ({est_sweep_s:,.0f} s)")


if __name__ == "__main__":
    sanity()
    test_known()
    benchmark(PRIMARY)
