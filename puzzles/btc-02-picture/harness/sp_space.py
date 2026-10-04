"""The GPU engine's view of a setperm .conf: one uint64 index space.

index = unit * per_unit + within
  unit   = one REQUIRE placement (itertools.permutations(free, r) order),
           decoded from the unit number with a partial-Lehmer decode (no table)
  within = alt * span + fill
  alt    = FIX alternatives in FIX-FILE order, last one fastest
  fill   = POOL digits over the unplaced free slots ascending, last one fastest
Identical to the iteration order of setperm_solve._work; sp_ref.ref_walk is the
literal transcription the tests diff against.
"""
import math

import numpy as np

import setperm_solve as sps
import sp_ref
from template_solve import IDX, WORDS, checksum_ok, parse_path

SP_TOKW = 16          # bytes per token row
SP_MAXD = 8           # BIP32 path depth
SP_MAXP = 8           # paths per run
SP_NMAX = 24          # words
SP_MAXMSG = 239       # joined mnemonic bytes (two SHA-512 blocks after a 128-byte prefix)
SP_MAXPASS = sp_ref.MAX_PASS_BYTES
SP_ALTTOK_MAX = 512
SP_POOL_MAX = 4096

# descriptor layout: uint64 words shared by the host and every kernel
LAYOUT = dict(
    SP_D_N=0, SP_D_R=1, SP_D_M=2, SP_D_K=3, SP_D_NALT=4, SP_D_NPOOL=5,
    SP_D_PERUNIT=6, SP_D_TOTAL=7, SP_D_DISTINCT=8, SP_D_SPAN=9, SP_D_MODE=10,
    SP_O_MID=16, SP_O_FREE=32, SP_O_PW=56, SP_O_REQ=80, SP_O_ALTSLOT=104,
    SP_O_ALTRAD=128, SP_O_ALTOFF=152, SP_O_ALTTOK=176, SP_O_POOL=176 + SP_ALTTOK_MAX,
)
SP_D_WORDS = LAYOUT["SP_O_POOL"] + SP_POOL_MAX
MODE_NONE, MODE_BIP39, MODE_ELECTRUM = 0, 1, 2


class SpaceError(ValueError):
    """The config is valid for setperm_solve but outside what the GPU engine supports."""


def cl_defines():
    d = dict(LAYOUT, SP_TOKW=SP_TOKW, SP_MAXD=SP_MAXD, SP_MAXP=SP_MAXP, SP_NMAX=SP_NMAX,
             SP_PSTR=2 + SP_MAXD, SP_MAXMSG=SP_MAXMSG)
    return "\n".join(f"#define {k} {v}" for k, v in d.items()) + "\n"


def path_table(paths):
    """uint32 rows [len, common-prefix-with-previous-path, idx0..idx7]."""
    rows, prev = [], ()
    for p in paths:
        common = 0
        while common < min(len(p), len(prev)) and p[common] == prev[common]:
            common += 1
        rows.append([len(p), common] + list(p) + [0] * (SP_MAXD - len(p)))
        prev = p
    return np.array(rows, dtype=np.uint32)


class Space:
    def __init__(self, cfg):
        self.cfg = cfg
        self.scheme = cfg["scheme"]
        if self.scheme not in ("bip39", "electrum", "bip39raw"):   # a hand-built cfg: never a silent MODE_BIP39
            raise SpaceError(f"unknown scheme {self.scheme!r}")
        n = self.n = cfg["words"]
        if not n or n > SP_NMAX:
            raise SpaceError(f"WORDS must be 1..{SP_NMAX}, got {n}")
        if self.scheme == "bip39" and n not in (12, 15, 18, 21, 24):
            raise SpaceError(f"bip39 needs 12/15/18/21/24 words, got {n}")
        fix = cfg["fix"]
        if any(not 1 <= s <= n for s in fix):
            raise SpaceError("FIX slot out of range")
        self.tokens = list(WORDS)
        self.tok_id = dict(IDX)
        self.free = [i for i in range(n) if (i + 1) not in fix]
        self.m = len(self.free)
        self.r = len(cfg["require"])
        if self.r > self.m:
            raise SpaceError("more REQUIRE words than free slots")
        self.k = self.m - self.r
        self.req_ids = [self._tid(w) for w in cfg["require"]]
        self.alt_slot = [s - 1 for s in fix]                  # FIX-FILE order
        alts = [w.split("|") for w in fix.values()]
        self.alt_rad = [len(a) for a in alts]
        self.alt_off, self.alt_tok = [], []
        for a in alts:
            self.alt_off.append(len(self.alt_tok))
            self.alt_tok += [self._tid(w) for w in a]
        self.pool_ids = [self._tid(w) for w in cfg["pool"]]
        self.npool = len(self.pool_ids)
        if len(self.alt_tok) > SP_ALTTOK_MAX or self.npool > SP_POOL_MAX:
            raise SpaceError("too many FIX alternatives or POOL words for the descriptor")
        self.units = math.prod(self.m - i for i in range(self.r))
        self.place_w = [math.prod(self.m - j - t for t in range(1, self.r - j)) for j in range(self.r)]
        self.nalt_prod = math.prod(self.alt_rad)
        self.span = self.npool ** self.k
        self.per_unit = self.nalt_prod * self.span
        self.total = self.units * self.per_unit
        if self.total < 1:
            raise SpaceError("empty search space (an empty POOL with unknown slots?)")
        if self.total >= 1 << 63:
            raise SpaceError("search space does not fit 63 bits")
        self.tok_bytes = [t.encode("utf-8") for t in self.tokens]
        used = set(self.req_ids + self.alt_tok + self.pool_ids)
        if any(len(self.tok_bytes[i]) > SP_TOKW for i in used):
            raise SpaceError(f"token longer than {SP_TOKW} bytes")
        if self.scheme == "bip39raw":     # load_config normalised and checked them; a cfg built by hand may not be
            for t in sorted({self.tokens[i] for i in used}):
                why = sps.raw_token_problem(t)
                if why:
                    raise SpaceError(f"bip39raw token {t!r}: {why}")
        fixed = sum(max(len(self.tok_bytes[t]) for t in self.alt_tok[o:o + r])
                    for o, r in zip(self.alt_off, self.alt_rad))
        free_max = max([len(self.tok_bytes[t]) for t in self.req_ids + self.pool_ids] or [0])
        self.max_mnemonic = fixed + free_max * self.m + (n - 1)
        if self.max_mnemonic > SP_MAXMSG:
            raise SpaceError(f"mnemonic can reach {self.max_mnemonic} bytes; the GPU supports {SP_MAXMSG}")
        self.passes = list(dict.fromkeys(sps.normalize_pass(p, self.scheme) for p in cfg["pass"]))
        for p in self.passes:
            if len(p.encode("utf-8")) > SP_MAXPASS:
                raise SpaceError(f"passphrase over {SP_MAXPASS} bytes: {p[:20]!r}...")
        self.paths_txt = cfg["paths"] or (["m/0/0"] if self.scheme == "electrum" else ["m/44'/0'/0'/0/0"])
        self.paths = [parse_path(p) for p in self.paths_txt]
        if len(self.paths) > SP_MAXP or any(len(p) > SP_MAXD for p in self.paths):
            raise SpaceError(f"at most {SP_MAXP} paths of depth <= {SP_MAXD}")
        self.mode = MODE_ELECTRUM if self.scheme == "electrum" else MODE_NONE if self.scheme == "bip39raw" else MODE_BIP39

    @classmethod
    def load(cls, path):
        return cls(sps.load_config(path))

    def _tid(self, w):
        if w not in self.tok_id:
            self.tok_id[w] = len(self.tokens)
            self.tokens.append(w)
        return self.tok_id[w]

    # ---- reference decode (independent of ref_walk; mirrors sp_decode.cl) ----
    def decode_ids(self, idx):
        if not 0 <= idx < self.total:
            raise IndexError(idx)
        unit, within = divmod(idx, self.per_unit)
        a, f = divmod(within, self.span)
        tok = [None] * self.n
        for i in range(len(self.alt_slot) - 1, -1, -1):
            a, dig = divmod(a, self.alt_rad[i])
            tok[self.alt_slot[i]] = self.alt_tok[self.alt_off[i] + dig]
        avail, rem = list(self.free), unit
        for j in range(self.r):
            sel, rem = divmod(rem, self.place_w[j])
            tok[avail.pop(sel)] = self.req_ids[j]
        for t in range(len(avail) - 1, -1, -1):
            f, dig = divmod(f, self.npool)
            tok[avail[t]] = self.pool_ids[dig]
        return tok

    def decode(self, idx):
        return [self.tokens[i] for i in self.decode_ids(idx)]

    def candidate_ok(self, idx):
        """DISTINCT + scheme filter, exactly as _work applies them (bip39raw: DISTINCT alone)."""
        ids = self.decode_ids(idx)
        words = [self.tokens[i] for i in ids]
        if self.cfg["distinct"] and len(set(words)) != self.n:
            return False
        if self.scheme == "electrum":
            return sps.electrum_standard(" ".join(words))
        if self.scheme == "bip39raw":
            return True
        return checksum_ok(ids)

    # ---- device tables ----
    def descriptor(self, mode=None):
        d = np.zeros(SP_D_WORDS, dtype=np.uint64)
        L = LAYOUT
        d[L["SP_D_N"]], d[L["SP_D_R"]], d[L["SP_D_M"]], d[L["SP_D_K"]] = self.n, self.r, self.m, self.k
        d[L["SP_D_NALT"]], d[L["SP_D_NPOOL"]] = len(self.alt_slot), self.npool
        d[L["SP_D_PERUNIT"]], d[L["SP_D_TOTAL"]] = self.per_unit, self.total
        d[L["SP_D_DISTINCT"]] = 1 if self.cfg["distinct"] else 0
        d[L["SP_D_SPAN"]] = self.span
        d[L["SP_D_MODE"]] = self.mode if mode is None else mode
        mids = sp_ref.hmac_midstates(b"Seed version")
        d[L["SP_O_MID"]:L["SP_O_MID"] + 16] = np.array(mids[0] + mids[1], dtype=np.uint64)
        for name, vals in (("SP_O_FREE", self.free), ("SP_O_PW", self.place_w), ("SP_O_REQ", self.req_ids),
                           ("SP_O_ALTSLOT", self.alt_slot), ("SP_O_ALTRAD", self.alt_rad),
                           ("SP_O_ALTOFF", self.alt_off), ("SP_O_ALTTOK", self.alt_tok),
                           ("SP_O_POOL", self.pool_ids)):
            if vals:
                d[L[name]:L[name] + len(vals)] = np.array(vals, dtype=np.uint64)
        return d

    def token_tables(self):
        nt = len(self.tokens)
        tokb = np.zeros(nt * SP_TOKW, dtype=np.uint8)
        tokl = np.zeros(nt, dtype=np.uint8)
        for i, b in enumerate(self.tok_bytes):
            if len(b) <= SP_TOKW:
                tokb[i * SP_TOKW:i * SP_TOKW + len(b)] = np.frombuffer(b, dtype=np.uint8)
                tokl[i] = len(b)
        return tokb, tokl

    def salt_words(self):
        return np.array([sp_ref.salt_block_words(self.scheme, p.encode("utf-8")) for p in self.passes],
                        dtype=np.uint64)

    def path_table(self):
        return path_table(self.paths)

    def estimate(self):
        vr = 1 / 256 if self.scheme == "electrum" else 1.0 if self.scheme == "bip39raw" else 1 / (1 << (self.n // 3))
        valid = self.total * vr
        return {"raw": self.total, "valid_rate": vr, "valid": valid,
                "derivations": valid * len(self.passes), "paths": len(self.paths)}
