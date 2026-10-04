"""CPU reference for the GPU set+permutation engine. READ-ONLY, no OpenCL.

Everything the GPU engine is checked against lives here:
  * a pure-Python SHA-512 compression (HMAC midstates for the Electrum filter)
  * the PBKDF2 first-iteration salt block, one padded block per passphrase
  * hash160s for BOTH key forms (form 0 = compressed, 1 = uncompressed)
  * confirm(): the CPU re-check of a GPU hit
  * ref_walk()/ref_survivors(): a literal transcription of setperm_solve._work
"""
import hashlib
import hmac
import itertools
import math
import struct

from coincurve import PrivateKey

import setperm_solve as sps
from template_solve import IDX, _ckd, checksum_ok, parse_path

M64 = (1 << 64) - 1
MAX_PASS_BYTES = 99   # 8 (salt prefix) + pp + 4 (INT) + 1 (0x80) + 16 (length) <= 128


def _primes(count):
    out, c = [], 2
    while len(out) < count:
        if all(c % p for p in out if p * p <= c):
            out.append(c)
        c += 1
    return out


def _icbrt(x):
    lo, hi = 0, 1 << (x.bit_length() // 3 + 2)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if mid ** 3 <= x:
            lo = mid
        else:
            hi = mid - 1
    return lo


# FIPS 180-4: fractional parts of cube roots (K) and square roots (IV) of the first primes
SHA512_K = [_icbrt(p << 192) & M64 for p in _primes(80)]
SHA512_IV = [math.isqrt(p << 128) & M64 for p in _primes(8)]


def _rr(x, n):
    return ((x >> n) | (x << (64 - n))) & M64


def sha512_compress(state, block):
    w = list(struct.unpack(">16Q", block))
    for i in range(16, 80):
        s0 = _rr(w[i - 15], 1) ^ _rr(w[i - 15], 8) ^ (w[i - 15] >> 7)
        s1 = _rr(w[i - 2], 19) ^ _rr(w[i - 2], 61) ^ (w[i - 2] >> 6)
        w.append((w[i - 16] + s0 + w[i - 7] + s1) & M64)
    a, b, c, d, e, f, g, h = state
    for i in range(80):
        t1 = (h + (_rr(e, 14) ^ _rr(e, 18) ^ _rr(e, 41)) + ((e & f) ^ (~e & g)) + SHA512_K[i] + w[i]) & M64
        t2 = ((_rr(a, 28) ^ _rr(a, 34) ^ _rr(a, 39)) + ((a & b) ^ (a & c) ^ (b & c))) & M64
        h, g, f, e, d, c, b, a = g, f, e, (d + t1) & M64, c, b, a, (t1 + t2) & M64
    return [(x + y) & M64 for x, y in zip(state, [a, b, c, d, e, f, g, h])]


def sha512_finish_py(state, tail, prefix_len):
    """Digest of a message whose first `prefix_len` bytes are already compressed
    into `state`; `tail` is the rest."""
    total = prefix_len + len(tail)
    buf = bytes(tail) + b"\x80"
    buf += b"\x00" * ((-(len(buf) + 16)) % 128)
    buf += (total * 8).to_bytes(16, "big")
    st = list(state)
    for i in range(0, len(buf), 128):
        st = sha512_compress(st, buf[i:i + 128])
    return b"".join(x.to_bytes(8, "big") for x in st)


def hmac_midstates(key):
    """(ipad state, opad state) after absorbing the padded key block."""
    if len(key) > 128:
        key = hashlib.sha512(key).digest()
    key = key.ljust(128, b"\x00")
    return (sha512_compress(SHA512_IV, bytes(b ^ 0x36 for b in key)),
            sha512_compress(SHA512_IV, bytes(b ^ 0x5C for b in key)))


def hmac_via_midstates(mids, msg):
    return sha512_finish_py(mids[1], sha512_finish_py(mids[0], msg, 128), 128)


def salt_block_words(scheme, pp):
    """PBKDF2 first-iteration message as one padded 128-byte block, 16 big-endian
    uint64: prefix || pp || INT(1) || 0x80 || zeros || 128-bit length of (128 + salt)."""
    prefix = b"electrum" if scheme == "electrum" else b"mnemonic"
    salt = prefix + pp + b"\x00\x00\x00\x01"
    if len(pp) > MAX_PASS_BYTES:
        raise ValueError(f"passphrase is {len(pp)} bytes; the GPU salt block holds at most {MAX_PASS_BYTES}")
    blk = (salt + b"\x80").ljust(112, b"\x00") + ((128 + len(salt)) * 8).to_bytes(16, "big")
    return list(struct.unpack(">16Q", blk))


def hash160_forms_seed(seed, paths):
    """[(path_i, form, hash160)] for each parsed path; form 0 compressed, 1 uncompressed."""
    I = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    cache = {(): (I[:32], I[32:])}
    out = []
    for pi, path in enumerate(paths):
        for d in range(1, len(path) + 1):
            if path[:d] not in cache:
                cache[path[:d]] = _ckd(*cache[path[:d - 1]], path[d - 1])
        pk = PrivateKey(cache[path][0]).public_key
        for form in (0, 1):
            pub = pk.format(compressed=(form == 0))
            out.append((pi, form, hashlib.new("ripemd160", hashlib.sha256(pub).digest()).digest()))
    return out


def hash160_forms(mnemonic, pp, paths, scheme):
    return hash160_forms_seed(sps.seed_of(mnemonic, pp, scheme), paths)


def confirm(mnemonic, pp, path_txt, form, scheme, target_h160):
    """CPU re-check of a GPU hit, for either key form (setperm's own
    hash160s_scheme only makes compressed keys)."""
    got = {(p, f): h for p, f, h in hash160_forms(mnemonic, pp, [parse_path(path_txt)], scheme)}
    return got[(0, form)] == target_h160


def ref_walk(cfg):
    """Yield (index, slots) for EVERY raw candidate, in setperm_solve._work order:
    placements (itertools.permutations of the free slots) outermost, then the FIX
    alternatives in FIX-file order, then the pool digits, last slot fastest."""
    n, fix, req, pool = cfg["words"], cfg["fix"], cfg["require"], cfg["pool"]
    free = [i for i in range(n) if (i + 1) not in fix]
    alts = {s - 1: w.split("|") for s, w in fix.items()}
    idx = 0
    for placement in itertools.permutations(free, len(req)):
        slots = [None] * n
        for pos, w in zip(placement, req):
            slots[pos] = w
        rest = [i for i in range(n) if slots[i] is None and i not in alts]
        for afill in itertools.product(*alts.values()):
            for fill in itertools.product(pool, repeat=len(rest)):
                for i, w in zip(alts, afill):
                    slots[i] = w
                for i, w in zip(rest, fill):
                    slots[i] = w
                yield idx, list(slots)
                idx += 1


def ref_survivors(cfg):
    """(tested, valid, [idx...]) exactly as setperm_solve._work counts them (bip39raw has no filter: valid == tested)."""
    tested = valid = 0
    surv = []
    for idx, slots in ref_walk(cfg):
        if cfg["distinct"] and len(set(slots)) != len(slots):
            continue
        tested += 1
        if cfg["scheme"] == "electrum":
            if not sps.electrum_standard(" ".join(slots)):
                continue
        elif cfg["scheme"] == "bip39raw":
            pass                                   # no filter: every tested candidate is valid
        elif not checksum_ok([IDX[w] for w in slots]):
            continue
        valid += 1
        surv.append(idx)
    return tested, valid, surv
