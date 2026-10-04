#!/usr/bin/env python3
"""Decode the prize address (base58check) and show the one-byte shift that voided a community scan.

    python3 tools/check_target.py [ADDRESS]

Stdlib only. For the target this prints version 00, HASH160 ccbd031e54cde2a3189fd59bc49f731367a1779e and
checksum OK. The "shifted" line is bytes [2:22] of the 25-byte payload: the value a community CUDA scan printed
as its "Target Hash160" (bd031e54...), which no 12!-order search could ever match.
"""
import hashlib
import sys

TARGET = "1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ"
ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def decode(addr):
    """(version, hash160, checksum, checksum_ok) of a 25-byte base58check payload. ValueError on a bad shape."""
    n = 0
    for c in addr:
        n = n * 58 + ALPHABET.index(c)
    try:
        raw = n.to_bytes(25, "big")
    except OverflowError:
        raise ValueError("not a 25-byte base58check payload")
    body, ck = raw[:21], raw[21:]
    ok = hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4] == ck
    return body[0], body[1:], ck, ok


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    addr = args[0] if args else TARGET
    try:
        version, h160, ck, ok = decode(addr)
    except ValueError as e:
        print(f"{addr}: {e}", file=sys.stderr)
        return 1
    shifted = (bytes([version]) + h160 + ck)[2:22]
    print(f"address   {addr}")
    print(f"version   {version:02x}")
    print(f"hash160   {h160.hex()}")
    print(f"checksum  {ck.hex()} {'OK' if ok else 'BAD'}")
    print(f"shifted   {shifted.hex()}  (payload bytes [2:22]; the void community scan's 'Target Hash160')")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
