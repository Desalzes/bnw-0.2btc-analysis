"""GPU set+permutation search (READ-ONLY) - the same .conf search as setperm_solve.py.

On a confirmed hit it writes FOUND_gpu.txt, prints the STOP instruction and exits 2.
It never constructs, signs or broadcasts a transaction (ADR-0001). Targets are the
prize address and the run's own planted seeds only; there is no --target.

  gpu_setperm.py CONFIG --dry-run [--assume-rate R]  # estimate + guard, no GPU
  gpu_setperm.py CONFIG --checkpoint FILE --signoff "who approved what, and when"   # a real run
  gpu_setperm.py CONFIG --bench                      # rate with a dummy target + a planted seed

Every real run needs the operator's sign-off in words (--signoff TEXT, run_search(signoff=...); ADR-0001 rail 4):
without it the run is REFUSED before anything touches the GPU or the disk, and the step that searches checks it
again. With it, the rate is measured on the GPU (a dummy target and a planted seed, never the prize) and then the
cost guard decides. --assume-rate is accepted only with --dry-run: a real run measures its own rate. --max-hours
is capped at 168.

Exit codes: 0 NO MATCH (verified), a dry run or a successful --bench (the status line of the first two tells them
apart, the JSON a --bench prints tells that one apart: a script must read them, not just the exit code), 1 cannot
run (config/engine/file refused), 2 FOUND, 3 refused (no sign-off, or the cost guard), 4 VOID (a control failed: the result
cannot be trusted; a --bench whose plant or coverage failed is 4 too), 5 STOPPED (max_blocks reached: an
incomplete run, not a result), 64 usage error.

Trust model: a FOUND rests on the CPU alone (sp_ref.confirm re-derives the key; a confirmed hit is
truth even when a control failed, or an exception cut a block short). A NO MATCH rests on:
  * a combo self-test at the start of every run (also a resume, unless every block is already in the
    checkpoint): two CPU-verified survivors, every (passphrase, path, key form) of each searched as a known
    answer through K1/K2/K3, EXACT hits required; then a slot sweep: one combination per distinct value of
    every dimension (passphrase, path, key form) is searched with the targets [h, h] and [h], so every value
    is seen at K3 target slots 0 and 1 and with a single target; then a slot reach: a known answer at every
    one of the 66 K3 slots a block searches;
  * per block: its own planted seeds (64 plants, fewer only where the block holds fewer seeds), every one found
    at its exact (index, passphrase, path, form); the prize is searched at two slots of the target list, slot
    (block + plant seed) % (plants + 2) and the one half a list further, so one slot failing cannot lose it, it
    takes every slot in turn and the plants hold all the others; every survivor K1 counted
    handed to K2/K3 exactly once, the slices K2 and K3 actually received checked against the tile meant;
    K1's thread count and its EXACT 64-bit index sum matching each launch, the whole block and run_search's
    own [lo, hi); a binomial check of the survivor count (EXACT, survivors == tested, for SCHEME bip39raw, which
    has no filter); tested == scanned where the config has no DISTINCT filter; and the finished blocks covering
    the space;
  * per block, a window audit (Engine.k1_spot_check): on one pseudo-random window of at most 32,768 candidates
    (deterministic from the plant seed and the block) K1 is run again on its own and the survivor SET must equal
    the CPU's, then a random slice of up to 16 of those survivors goes through the real K2 launch and the K3
    dump path and every (passphrase, path, key form) hash160 must equal the CPU's.
A block's 'done' line is written only after all of that held for it. NOT proven at run time (see README): the
K1 survivor set outside the audited windows is SAMPLED by the window audit (one window per block), not proven
(the rest rests on the sp_test_k1 parity tests against the CPU tool); 'done' lines of a checkpoint (a forged or
hand-edited checkpoint is trusted, as the CPU tool trusts its own); plants only sample a partial loss of
survivors or hits (64 per block: a fault losing a fraction q passes a block with probability (1 - q)^64);
a joint (slot, exact combination) K3 fault is seen only by the plants; and a wrapper that lies about the
slice it recorded and faults only for nsurv > 1 is invisible by design.
"""
import argparse
import hashlib
import json
import math
import operator
import os
import random
import secrets
import subprocess
import sys
import time
from collections import namedtuple
from types import SimpleNamespace

import numpy as np

import setperm_solve as sps
import sp_ref
import sp_space
from sp_k1_enum import expected_idx_sum          # numpy only: no OpenCL import
from template_solve import TARGET, address_of_hash160, hash160_of_address

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
LEDGER = os.path.join(REPO, "scratch", "btc02-gpu", "runs.jsonl")
FOUND_PATH = os.path.join(HERE, "FOUND_gpu.txt")
DEFAULT_ASSUME_RATE = 230_000      # derivations/s for --dry-run only: the slowest measured (p1, BIP39, 5 paths x 4 passphrases),
                                   # RTX 5060 Ti, 2026-09-29 (m2 ~250k, e4 ~250k). A real run measures its own rate.
K1_MAX_CHUNK = 1 << 25             # candidates per K1 launch (33M). K1's cost per candidate varies up to 4.7x with the
                                   # position inside a template, so a size learned in a cheap region must stay under
                                   # the watchdog in the slowest one
K2_MAX_THREADS = 1 << 17           # (survivor x passphrase) threads per K2 launch: ~0.35 s at ~380k/s;
                                   # the wrapper's 1<<20 default would be ~2.7 s, over the display watchdog
MAX_PASSES = 4096                  # one survivor = npass K2 threads + npass K3 threads, the smallest launch pair
                                   # the engine can make: it must stay well under the 0.5 s watchdog margin
BEAT_SECONDS = 5                   # heartbeat + thermal check, looked at after EVERY K1 launch and K2/K3 batch
PLANTS_PER_BLOCK = 64              # planted seeds in every block (fewer only where the block holds fewer seeds). One plant
                                   # samples the hit path once per block: an injected K3 fault that lost 1 hit in 64, by
                                   # the thread's place in a launch, passed every control (review of 2026-09-29). The K3
                                   # compare of 66 targets costs nothing measurable (265k/s with 2 targets, 261k/s with 65)
PRIZE_COPIES = 2                   # K3 slots that hold the prize in every block, half a target list apart: a fault confined
                                   # to ONE slot (and to threads no control runs) cannot lose a prize the other slot reports
PLANT_TRIES = 200_000              # random draws for a block's plant ...
PLANT_SCAN_MAX = 1_000_000         # ... then an exhaustive CPU scan, for a block of at most this many candidates
MAX_HOURS = 168                    # --max-hours / run_search(max_hours=...) above one week is refused outright
BINOM_MIN_TESTED = 10_000          # a block with fewer DISTINCT-passing candidates is not judged statistically ...
BINOM_SIGMAS = 8                   # ... a larger one must hold |valid - tested*r| <= 8 sigma (measured max |z| was 3.1)
SELFTEST_MAX_TARGETS = 256         # targets per combo self-test round (K3 compares every target in every thread)
SPOT_WIDTH = 1 << 15               # candidates in a block's window audit (the CPU checks each one: ~0.1 s on e4)
SPOT_SURVIVORS = 16                # survivors of that window that also go through the real K2 launch + K3 dump path
SPOT_MAX_THREADS = 1 << 9          # ... but never more than this many K2/K3 threads: the CPU re-derives each one (~2 ms), so the
                                   # audit's CPU time is bounded by ~1 s for up to 512 passphrases (a config with more still audits
                                   # 1 survivor x its passphrases)

EXIT_NO_MATCH, EXIT_FOUND, EXIT_REFUSED, EXIT_VOID, EXIT_STOPPED, EXIT_USAGE = 0, 2, 3, 4, 5, 64
NO_SIGNOFF = ("a real run needs the operator's explicit sign-off (ADR-0001 rail 4), whatever its size: re-run with "
              "--signoff 'who approved what, and when' (run_search: signoff=...); --dry-run and --bench need none")

# Everything whose change can change what a finished block means: a checkpoint is resumed only into the same
# engine. kernels/sp_*.cl are globbed (a new engine kernel joins automatically); the base kernels are the ones the
# wrappers build (sp_gpu.BASE_CRYPTO + common + sha2), listed here because sp_gpu needs OpenCL to import.
ENGINE_PY = ("gpu_setperm.py", "sp_ref.py", "sp_space.py", "sp_gpu.py", "sp_k1_enum.py", "sp_k2_pbkdf2.py",
             "sp_k3_derive.py", "setperm_solve.py", "template_solve.py")   # the CPU reference and the token table too
ENGINE_DATA = ("english.txt",)     # the wordlist every token id, checksum and decode comes from
ENGINE_BASE_KERNELS = ("common", "sha2", "ripemd", "secp256k1_common", "secp256k1_scalar", "secp256k1_field",
                       "secp256k1_group", "secp256k1_prec", "secp256k1", "address")

Hit = namedtuple("Hit", "idx pass_i path_i form tgt_i at", defaults=(None,))
# at = (chunk_start, chunk_count, batch_off, batch_count): the K1 chunk and the K2/K3 survivor slice that produced it
Plant = namedtuple("Plant", "idx pass_i path_i form h160")


class EngineError(RuntimeError):
    pass


class BlockResult:
    def __init__(self):
        self.scanned = self.tested = self.valid = 0
        self.processed = 0                       # survivors actually handed to K2/K3
        self.idx_sum = 0                         # sum (mod 2**64) of the per-launch K1 index sums of the block
        self.hits = []


def _check_passes(space, err):
    if len(space.passes) > MAX_PASSES:
        raise err(f"{len(space.passes)} passphrases: at most {MAX_PASSES} (one survivor's K2+K3 launch runs all of "
                  f"them at once and must stay well under the 0.5 s display watchdog)")


# ---------------------------------------------------------------- launch sizing / thermal

class Sizer:
    """Adaptive launch size: converges on ~`target` seconds per launch and halves after any
    launch slower than `hard` (this GPU drives the desktop; the kernel watchdog is on).
    Growth is at most 2x per launch: small launches are latency-bound (1k..8k threads all take
    ~21 ms), so a rate extrapolated from them is far too low and must not be trusted."""

    def __init__(self, start, target=0.3, hard=0.5, lo=64, hi=1 << 27):
        self.n, self.target, self.hard, self.lo, self.hi = int(start), target, hard, lo, hi

    def record(self, n, seconds):
        scale = 2.0 if seconds <= 0 else self.target / seconds
        scale = min(2.0, max(0.5, scale))
        new = int(min(self.hi, max(self.lo, n * scale)))
        if seconds > self.hard:
            new = int(max(self.lo, min(new, n // 2)))
        self.n = new

    def shrink(self):
        self.n = max(self.lo, self.n // 2)


def gpu_temp_c():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5).stdout
        return int(out.split()[0])
    except Exception:
        return None


def wait_cool(limit=85, log=print, temp_fn=None, sleep=time.sleep):
    temp_fn = temp_fn or gpu_temp_c
    warned = False
    while True:
        t = temp_fn()
        if t is None or t <= limit:
            return
        if not warned:
            log(f"  GPU {t} C > {limit} C: pausing until it cools")
            warned = True
        sleep(5)


# ---------------------------------------------------------------- the staged pipeline

class Engine:
    def __init__(self, space, targets, *, gpu=None, log=print, surv_cap=1 << 22, hit_cap=256, max_temp=85):
        _check_passes(space, EngineError)
        from sp_gpu import Gpu                     # imported here: dry runs and the guard need no OpenCL
        from sp_k1_enum import EnumKernel
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        from sp_k3_derive import DeriveKernel
        self.space, self.log, self.max_temp, self.hit_cap = space, log, max_temp, hit_cap
        self.partial = None                        # the BlockResult of the scan_block in progress / that raised
        self.gpu = gpu or Gpu.get()
        self.k1 = EnumKernel(self.gpu, space, cap=surv_cap)
        self.k2 = Pbkdf2Kernel(self.gpu, space, max_threads=K2_MAX_THREADS)
        self.k3 = DeriveKernel(self.gpu, space, targets, hit_cap=hit_cap)
        if space.mode == sp_space.MODE_NONE:
            # no filter (bip39raw): every scanned candidate survives, so a K1 launch larger than the survivor buffer
            # would always overflow and be redone smaller (and a buffer under the 64-candidate floor could not be
            # used at all): K1's launches are capped at the buffer instead
            hi = min(K1_MAX_CHUNK, self.k1.cap)
            self.s1 = Sizer(min(1 << 12, hi), lo=min(64, hi), hi=hi)
        else:
            self.s1 = Sizer(1 << 12, hi=K1_MAX_CHUNK)
        self.s2 = Sizer(1 << 10, hi=self.k2.max_threads)
        self._last_beat = None

    def set_targets(self, targets):
        """K3 compares against exactly these from the next launch on (the run: [prize, this block's plant])."""
        self.k3.set_targets(targets)

    def _batches(self, valid, npass):
        """(offset, count, full) slices of this chunk's survivors for K2/K3, each sized by self.s2 when it is
        requested; `full` is False for a slice clipped by the survivors left. scan_block checks what it is handed:
        the slices must tile [0, valid) exactly, in order."""
        off = 0
        while off < valid:
            want = max(1, self.s2.n // npass)
            n = min(valid - off, want)
            yield off, n, n == want
            off += n

    def _tick(self, res, cur, heartbeat):
        """The heartbeat + thermal check, after every launch: a stretch of K1 chunks with no survivor never
        enters the K2 loop and must still pause for heat. The interval runs across blocks."""
        if time.perf_counter() - self._last_beat > BEAT_SECONDS:
            wait_cool(self.max_temp, self.log)
            if heartbeat:
                heartbeat(res, cur)
            self._last_beat = time.perf_counter()

    def scan_block(self, start, end, heartbeat=None):
        """Scan [start, end) through K1 -> K2 -> K3 and return the BlockResult. Every hit K3 returned is appended
        to `self.partial` (the same object) BEFORE the batch is verified, so the caller can still judge the hits
        gathered so far if an exception cuts the block short. Raises EngineError unless: every survivor was handed
        to K2 and K3 exactly once and in order, both wrappers RECORDED receiving exactly that slice, and K1's index
        sums add up to those of [start, end)."""
        npass = len(self.space.passes)
        res = self.partial = BlockResult()
        if getattr(self, "_last_beat", None) is None:
            self._last_beat = time.perf_counter()
        cur = start
        while cur < end:
            want = self.s1.n
            n1 = min(want, end - cur)
            t0 = time.perf_counter()
            scanned, tested, valid = self.k1.run(cur, n1)
            dt = time.perf_counter() - t0
            isum = self.k1.last_idx_sum
            self._tick(res, cur, heartbeat)
            if valid > self.k1.cap:               # survivors were not all stored: redo a smaller chunk
                if n1 <= self.s1.lo:
                    raise EngineError("survivor buffer too small even for the minimum chunk")
                self.s1.n = min(self.s1.n, n1)
                self.s1.shrink()
                continue
            if isum != expected_idx_sum(cur, n1):  # the wrapper checks this too; nothing here trusts that it did
                raise EngineError(f"K1 index checksum {isum!r} != {expected_idx_sum(cur, n1):#x} for the chunk "
                                  f"[{cur:,}, {cur + n1:,})")
            if n1 == want or dt > self.s1.target:  # a clipped tail launch that was fast says nothing about the rate
                self.s1.record(n1, dt)
            res.scanned += scanned
            res.tested += tested
            res.valid += valid
            res.idx_sum = (res.idx_sum + isum) % (1 << 64)
            processed = 0                          # survivors of THIS chunk actually handed to K2/K3
            for off, nsurv, full in self._batches(valid, npass):
                if off != processed or not 0 < nsurv <= valid - off:
                    raise EngineError(f"survivor slice [{off:,}, {off + nsurv:,}) does not continue at {processed:,} "
                                      f"of the {valid:,} survivors of the K1 chunk at index {cur:,}")
                t0 = time.perf_counter()
                self.k2.launch(self.k1.surv, off, nsurv)
                at = (cur, n1, off, nsurv)
                res.hits += [Hit(*h, at=at) for h in self.k3.run(self.k2.seeds, self.k1.surv, off, nsurv, npass)]
                dt2 = time.perf_counter() - t0
                tile = (processed, nsurv)          # the loop's own tile, checked against what each wrapper RECEIVED
                if self.k2.last_slice != tile or self.k3.last_slice != tile:
                    raise EngineError(f"survivor slice mismatch in the K1 chunk at index {cur:,}: the batch is "
                                      f"(offset, count) = {tile}, K2 received {self.k2.last_slice}, K3 received "
                                      f"{self.k3.last_slice}")
                if full or dt2 > self.s2.target:
                    self.s2.record(nsurv * npass, dt2)
                processed += nsurv
                res.processed += nsurv
                self._tick(res, cur, heartbeat)
            if processed != valid:
                raise EngineError(f"K2/K3 were handed {processed:,} of the {valid:,} survivors of the K1 chunk at "
                                  f"index {cur:,}")
            cur += n1
        want_sum = expected_idx_sum(start, end - start)
        if res.idx_sum != want_sum:
            raise EngineError(f"K1 index checksums add up to {res.idx_sum:#x} over the block [{start:,}, {end:,}), "
                              f"expected {want_sum:#x}: the chunks did not cover exactly that range")
        return res

    def _derive_dump(self, off, k):
        """The REAL K2 launch over survivors [off, off + k) of the K1 run whose survivors are in the survivor buffer,
        then K3's dump path over the seeds K2 wrote: an array (k * npass, npaths, 2, 20) of hash160s (thread
        t = survivor t // npass, passphrase t % npass; form 0 compressed, 1 uncompressed). The seam the window audit
        reads K2 and K3 through - stand-in engines in the tests replace the kernels below it, not this."""
        n = k * len(self.space.passes)
        self.k2.launch(self.k1.surv, off, k)
        seeds = self.gpu.read(self.k2.seeds, np.uint64, n * 8).reshape(n, 8)
        return self.k3.dump(seeds)

    def k1_spot_check(self, lo, hi, seed, width=None):
        """The per-block window audit. On a pseudo-random window of min(SPOT_WIDTH, hi - lo) candidates inside
        [lo, hi) - deterministic from the plant `seed` and `lo` - K1 is run again as a launch of its own and:
          1. the survivor SET (as a sorted list: a duplicate or a missing or an extra index each differ) must equal
             the CPU's (`space.candidate_ok` over the window), and K1's `valid` must equal its length;
          2. a random contiguous slice of up to SPOT_SURVIVORS of those survivors (fewer if their K2/K3 threads
             would pass SPOT_MAX_THREADS) goes through the real K2 launch and the K3 dump path, and every
             (passphrase, path, key form) hash160 must equal sp_ref.hash160_forms'.
        Raises EngineError naming what differed. It touches none of a block's own state - no BlockResult, no K3
        targets, no Sizer, no checksum the block keeps - so it can run between any two blocks; the K1/K2/K3 buffers
        it overwrites are rewritten by the next launch of each. A window whose survivors overflow the K1 buffer is
        skipped (logged, `skipped=True`; it cannot happen at the default width and buffer). Returns a namespace
        (window=(start, width), survivors, checked=K2/K3 threads compared, skipped)."""
        if not 0 <= lo < hi:
            raise ValueError(f"block [{lo}, {hi}) is empty")
        space = self.space
        w = min(int(width or SPOT_WIDTH), hi - lo)
        rng = random.Random(f"{seed}:k1-spot:{lo}")
        wlo = lo + rng.randrange(0, hi - lo - w + 1)
        where = f"the window [{wlo:,}, {wlo + w:,}) of the block [{lo:,}, {hi:,})"
        scanned, tested, valid = self.k1.run(wlo, w)
        isum, want_sum = self.k1.last_idx_sum, expected_idx_sum(wlo, w)
        if isum != want_sum:                       # the wrapper checks this too; nothing here trusts that it did
            raise EngineError(f"K1 index checksum {isum!r} != {want_sum:#x} for {where}")
        if scanned != w:
            raise EngineError(f"K1 scanned {scanned:,} of the {w:,} candidates of {where}")
        if valid > w:                              # more survivors than candidates scanned: proof of a K1 fault, not an overflow
            raise EngineError(f"K1 reported {valid:,} survivors among the {w:,} candidates of {where}")
        if valid > self.k1.cap:
            self.log(f"  window audit skipped for {where}: its {valid:,} survivors overflow the survivor buffer "
                     f"({self.k1.cap:,})")
            return SimpleNamespace(window=(wlo, w), survivors=valid, checked=0, skipped=True)
        raw = [int(x) for x in self.k1.survivors(valid)]
        want = [c for c in range(wlo, wlo + w) if space.candidate_ok(c)]
        if valid != len(want) or sorted(raw) != want:
            miss, extra = sorted(set(want) - set(raw)), sorted(set(raw) - set(want))
            raise EngineError(f"K1 survivor set differs from the CPU's on {where}: K1 reported valid={valid:,} and "
                              f"{len(raw):,} survivor(s), the CPU has {len(want):,}; {len(miss)} missing {miss[:4]}, "
                              f"{len(extra)} unexpected {extra[:4]}, {len(raw) - len(set(raw))} duplicated")
        checked = 0
        if raw:
            npass, npaths = len(space.passes), len(space.paths)
            k = max(1, min(len(raw), SPOT_SURVIVORS, SPOT_MAX_THREADS // npass))
            off = rng.randrange(0, len(raw) - k + 1)
            dump = self._derive_dump(off, k)
            n = k * npass
            if self.k2.last_slice != (off, k):
                raise EngineError(f"K2 received the survivor slice {self.k2.last_slice}, not (offset, count) = "
                                  f"{(off, k)}, in the audit of {where}")
            if tuple(np.shape(dump)) != (n, npaths, 2, 20):
                raise EngineError(f"K3 dump has the shape {tuple(np.shape(dump))}, expected {(n, npaths, 2, 20)}, in "
                                  f"the audit of {where}")
            for t in range(n):
                idx, pass_i = raw[off + t // npass], t % npass
                m = " ".join(space.decode(idx))
                for path_i, form, h in sp_ref.hash160_forms(m, space.passes[pass_i], space.paths, space.scheme):
                    got = bytes(int(v) for v in dump[t][path_i][form])
                    if got != h:
                        raise EngineError(f"K2/K3 parity: survivor {idx:,}, passphrase #{pass_i}, path #{path_i}, "
                                          f"form {form}: the GPU derived hash160 {got.hex()}, the CPU {h.hex()} "
                                          f"(the audit of {where})")
                checked += 1
        return SimpleNamespace(window=(wlo, w), survivors=len(want), checked=checked, skipped=False)


# ---------------------------------------------------------------- planted seeds

def plan_block_units(space, target_deriv=4e6):
    """Units per checkpoint block: ~target_deriv expected derivations, at least 1 unit."""
    per_unit = max(1e-9, space.estimate()["derivations"] / space.units)
    return max(1, min(space.units, int(target_deriv / per_unit)))


def make_block_plant(space, seed, b, lo, hi):
    """Block b's own control: a filter-passing candidate in [lo, hi) with a random (passphrase, path, key form),
    deterministic per (seed, b). A block of <= PLANT_TRIES candidates is scanned once in a random order; a larger
    one gets PLANT_TRIES random draws, then an exhaustive scan if it has <= PLANT_SCAN_MAX candidates. None means
    the CPU proved the block holds no filter-passing candidate at all; a larger block that yields none raises."""
    if not 0 <= lo < hi <= space.total:
        raise ValueError(f"block [{lo}, {hi}) is not inside the space of {space.total} candidates")
    rng = random.Random(f"{seed}:{b}")
    n, idx = hi - lo, None
    if n <= PLANT_TRIES:
        idx = next((c for c in rng.sample(range(lo, hi), n) if space.candidate_ok(c)), None)
        if idx is None:
            return None
    else:
        for _ in range(PLANT_TRIES):
            c = rng.randrange(lo, hi)
            if space.candidate_ok(c):
                idx = c
                break
        if idx is None:
            if n > PLANT_SCAN_MAX:
                raise sp_space.SpaceError(f"no filter-passing candidate to plant in block {b} [{lo:,}, {hi:,}) after "
                                          f"{PLANT_TRIES:,} random draws, and it is too large to scan")
            idx = next((c for c in range(lo, hi) if space.candidate_ok(c)), None)
            if idx is None:
                return None
    pass_i, path_i, form = rng.randrange(len(space.passes)), rng.randrange(len(space.paths)), rng.randrange(2)
    m = " ".join(space.decode(idx))
    h = sp_ref.hash160_forms(m, space.passes[pass_i], [space.paths[path_i]], space.scheme)[form][2]
    return Plant(idx, pass_i, path_i, form, h)


def _plant_at(space, rng, idx):
    pass_i, path_i, form = rng.randrange(len(space.passes)), rng.randrange(len(space.paths)), rng.randrange(2)
    m = " ".join(space.decode(idx))
    h = sp_ref.hash160_forms(m, space.passes[pass_i], [space.paths[path_i]], space.scheme)[form][2]
    return Plant(idx, pass_i, path_i, form, h)


def make_block_plants(space, seed, b, lo, hi, count=None):
    """Block b's controls: up to `count` (default PLANTS_PER_BLOCK) plants, deterministic per (seed, b). Plant 0 is
    make_block_plant's; the others are further filter-passing candidates of [lo, hi), each with its own random
    (passphrase, path, key form), drawn the same way from a stream of their own (a block of <= PLANT_TRIES
    candidates in one random order, a larger one by PLANT_TRIES random draws). All have different indices and
    different hash160s: the twin index of a seed already planted with the same combination is not planted again.
    Fewer than `count` only where the draws found no more; [] where the CPU proved the block holds no
    filter-passing candidate. A shorter list is a prefix of a longer one."""
    count = PLANTS_PER_BLOCK if count is None else count
    if isinstance(count, bool) or count != int(count) or count < 1:
        raise ValueError(f"count must be a whole number >= 1, got {count!r}")
    first = make_block_plant(space, seed, b, lo, hi)
    if first is None:
        return []
    plants, tried, hashes = [first], {first.idx}, {first.h160}
    rng = random.Random(f"{seed}:{b}:more")
    n = hi - lo
    draws = rng.sample(range(lo, hi), n) if n <= PLANT_TRIES else (rng.randrange(lo, hi) for _ in range(PLANT_TRIES))
    for c in draws:
        if len(plants) >= count:
            break
        if c in tried or not space.candidate_ok(c):
            continue
        tried.add(c)
        plant = _plant_at(space, rng, c)
        if plant.h160 not in hashes:
            hashes.add(plant.h160)
            plants.append(plant)
    return plants


def block_targets(prize, plant_h160s, b, seed=0, copies=None):
    """(targets, prize_slots) for block b. The list has n = plants + copies slots (copies: PRIZE_COPIES, 2):
    the prize sits at the slots (block + plant seed + k * (n // copies)) % n, k = 0 .. copies - 1, and the block's
    plants, in their order, at all the others. So the prize is searched at two slots half a list apart (one slot
    failing cannot lose it), its slots move on by one from block to block (a fault confined to one K3 slot cannot
    hide behind plants that never sit there), and they start where the plant seed says (in a run of one block the
    prize is not always at slot 0). A block without a plant searches the prize alone, at every slot."""
    copies = PRIZE_COPIES if copies is None else copies
    if isinstance(copies, bool) or copies != int(copies) or copies < 1:
        raise ValueError(f"copies must be a whole number >= 1, got {copies!r}")
    hs = list(plant_h160s)
    n = len(hs) + copies
    slots = tuple((b + seed + k * (n // copies)) % n for k in range(copies))
    plants = iter(hs)
    return [prize if t in slots else next(plants) for t in range(n)], slots


def _logical_tgt(t, prize_slots, nplants):
    """A hit's K3 target slot as this run means it: 0 = the prize (whichever of its slots reported it), 1 + j = the
    block's plant j. Any other value is kept as it is (it names a target that does not exist and is refused where
    it is judged)."""
    try:
        i = _as_index(t)
    except TypeError:
        return t
    if i in prize_slots:
        return 0
    if 0 <= i < nplants + len(prize_slots):
        return 1 + i - sum(1 for s in prize_slots if s < i)
    return t


def binomial_problem(tested, valid, rate):
    """None if `valid` is a plausible number of filter passes among `tested` candidates that each pass with
    probability `rate`; otherwise the reason it is not. A block below BINOM_MIN_TESTED is not judged. This sees
    what one statistical plant cannot: a small systematic survivor loss. It fails safe: a false alarm is a VOID,
    never a false NO MATCH. A scheme without a filter (rate >= 1: bip39raw) is judged EXACTLY instead, at any block
    size: every tested candidate must survive (the binomial sigma is 0 there, so nothing else is plausible)."""
    if rate >= 1:
        if valid == tested:
            return None
        return (f"{valid:,} valid of {tested:,} tested, but the scheme has no filter: every tested candidate must "
                f"survive (exact check, survivors == tested)")
    if tested < BINOM_MIN_TESTED:
        return None
    mean = tested * rate
    sd = math.sqrt(tested * rate * (1 - rate))
    if abs(valid - mean) <= BINOM_SIGMAS * sd:
        return None
    return (f"{valid:,} valid of {tested:,} tested where {mean:,.1f} +- {sd:,.1f} are expected at a pass rate of "
            f"{rate:.6g}: {(valid - mean) / sd:+.1f} sigma (limit {BINOM_SIGMAS})")


# ---------------------------------------------------------------- combo self-test

def selftest_survivors(space, seed):
    """Up to two CPU-verified survivors, deterministic from `seed`, one drawn from each half of the space (so the
    self-test runs in two different regions). Fewer only if a half has no filter-passing candidate that a
    bounded CPU search can find."""
    rng = random.Random(f"{seed}:combo-selftest")
    total = space.total
    halves = [(0, total // 2), (total // 2, total)] if total >= 2 else [(0, total)]
    found = []
    for lo, hi in halves:
        n = hi - lo
        if n <= 0:
            continue
        idx = None
        if n <= PLANT_TRIES:
            idx = next((c for c in rng.sample(range(lo, hi), n) if space.candidate_ok(c)), None)
        else:
            for _ in range(PLANT_TRIES):
                c = rng.randrange(lo, hi)
                if space.candidate_ok(c):
                    idx = c
                    break
            if idx is None and n <= PLANT_SCAN_MAX:
                idx = next((c for c in range(lo, hi) if space.candidate_ok(c)), None)
        if idx is not None:
            found.append(idx)
    return found


def _combo_name(space, pass_i, path_i, form):
    return (f"passphrase #{pass_i} {space.passes[pass_i]!r}, path #{path_i} {space.paths_txt[path_i]}, "
            f"{'compressed' if form == 0 else 'uncompressed'} key")


def _selftest_round(space, engine, idx, chunk, owners, sweep=False, reach=False):
    """One known-answer round: the survivor `idx` scanned alone (a 1-wide window through K1, K2 and K3) against the
    hash160s of `chunk` (a list of (pass_i, path_i, form, hash160); the same combination may appear twice: that puts
    it at two K3 slots). EXACTLY the hits {(idx, p, q, f, t)} for every target slot t and every combination (p, q, f)
    in `owners[hash160]` are required, no duplicate report (a hash160 that `owners` does not know is a filler: no
    hit may name it). Returns their number; raises EngineError naming what was missing or unexpected."""
    tag = " (slot sweep)" if sweep else " (slot reach)" if reach else ""
    engine.set_targets([c[3] for c in chunk])
    res = engine.scan_block(idx, idx + 1)
    if (res.scanned, res.tested, res.valid, res.processed) != (1, 1, 1, 1):
        raise EngineError(f"combo self-test{tag}: the 1-wide window at index {idx:,} reported scanned/tested/"
                          f"valid/processed = {res.scanned}/{res.tested}/{res.valid}/{res.processed}, "
                          f"expected 1/1/1/1")
    want = {(idx, p, q, f, t) for t, c in enumerate(chunk) for p, q, f in owners.get(c[3], ())}
    got, dup = set(), []
    for h in res.hits:
        key = tuple(_plain(v) for v in (h.idx, h.pass_i, h.path_i, h.form, h.tgt_i))
        if key in got:
            dup.append(key)
        got.add(key)
    if got != want or dup:
        miss = sorted(want - got, key=lambda k: (k[4], k[1], k[2], k[3]))
        extra = sorted(got - want, key=repr)
        lines = [f"target #{k[4]}: {_combo_name(space, k[1], k[2], k[3])}" for k in miss[:8]]
        raise EngineError(
            f"combo self-test{tag} failed at survivor index {idx:,}: {len(miss)} of {len(want)} expected hit(s) "
            f"missing" + (f" with {len(chunk)} target(s)" if sweep or reach else "")
            + (" - " + "; ".join(lines) if lines else "")
            + (f" - and {len(extra)} unexpected hit(s) {extra[:4]}" if extra else "")
            + (f" - and {len(dup)} duplicated report(s) {dup[:4]}" if dup else ""))
    return len(want)


def _slot_sweep(space, engine, idx, combos, owners):
    """The slot sweep for the survivor `idx`: the main rounds put every combination at ONE K3 slot (its position in the
    round) and always with many targets, but a real block searches [prize, plant] or [plant, prize] - slots 0 and 1 -
    and a block without a plant [prize] alone. So one combination per distinct value of every dimension (passphrase,
    path, key form; the first combination that shows a value not yet covered) is searched with the targets [h, h]
    (the value at slots 0 and 1) and [h] (alone): a fault keyed on (slot or target count, one value of one dimension)
    cannot hide. Returns (known answers, rounds). A combination whose [h, h] hits would not fit the hit buffer is a
    refusal, not an overflow."""
    cap = getattr(engine, "hit_cap", 256)
    cover, seen = [], set()
    for c in combos:
        keys = (("pass", c[0]), ("path", c[1]), ("form", c[2]))
        if any(k not in seen for k in keys):
            seen.update(keys)
            cover.append(c)
    answers = rounds = 0
    for c in cover:
        for chunk in ([c, c], [c]):
            if len(chunk) * len(owners[c[3]]) > cap:
                raise EngineError(f"combo self-test (slot sweep): {len(chunk)} target(s) x {len(owners[c[3]])} "
                                  f"combinations sharing one hash160 would need more than the hit buffer ({cap}) "
                                  f"holds: {_combo_name(space, c[0], c[1], c[2])}")
            answers += _selftest_round(space, engine, idx, chunk, owners, sweep=True)
            rounds += 1
    return answers, rounds


def _slot_reach(space, engine, idx, combos, owners, width, rng):
    """The slot reach for the survivor `idx`: a block searches up to `width` = PLANTS_PER_BLOCK + PRIZE_COPIES
    targets, but the main rounds reach only as many K3 slots as the config has combinations (8 on e4) and the sweep
    slots 0 and 1. So the survivor is searched once more with `width` targets, its own combinations in turn (slot t
    holds combination t % combinations), and EXACTLY one report per owner at every slot is required. Where the hit
    buffer cannot hold all of them the slots are split over several rounds of `width` targets, the slots of the
    other rounds holding random fillers that nothing may hit. Returns (known answers, rounds)."""
    cap = getattr(engine, "hit_cap", 256)
    layout = [combos[t % len(combos)] for t in range(width)]
    groups, cur, cur_hits = [], [], 0
    for t, c in enumerate(layout):
        n_hit = len(owners[c[3]])
        if n_hit > cap:
            raise EngineError(f"combo self-test (slot reach): {n_hit} combinations share one hash160 but the hit "
                              f"buffer holds {cap}: {_combo_name(space, c[0], c[1], c[2])}")
        if cur and cur_hits + n_hit > cap:
            groups.append(cur)
            cur, cur_hits = [], 0
        cur.append(t)
        cur_hits += n_hit
    groups.append(cur)
    answers = 0
    for group in groups:
        live = set(group)
        chunk = [c if t in live else (None, None, None, rng.randbytes(20)) for t, c in enumerate(layout)]
        answers += _selftest_round(space, engine, idx, chunk, owners, reach=True)
    return answers, len(groups)


def combo_selftest(space, engine, seed=0):
    """A known-answer test of every (passphrase, path, key form) dimension of K2 and K3, before the first block of
    every run. For each of up to two CPU-verified survivors the CPU computes the hash160 of EVERY (passphrase,
    path, form); the survivor is scanned alone (a 1-wide window through K1, K2 and K3) with those hash160s as the
    targets, and EXACTLY the expected hits at the exact (index, passphrase, path, form, target) are required. Up to
    SELFTEST_MAX_TARGETS targets go to one round (fewer if the engine's hit buffer could not hold their hits);
    every target of every round sits at a different K3 slot, slot 0 included. Then the slot sweep (_slot_sweep):
    every value of every dimension again, at K3 slots 0 and 1 and with a single target; then the slot reach
    (_slot_reach): a known answer at every one of the PLANTS_PER_BLOCK + PRIZE_COPIES slots a block searches.
    Raises EngineError naming what was missing or unexpected. The engine's targets are left at the last round's:
    the caller sets its own before every scan. Returns a summary: survivors, combos per survivor, known_answers and
    rounds of the main rounds, sweep_answers and sweep_rounds of the slot sweep, reach_answers and reach_rounds of
    the slot reach."""
    idxs = selftest_survivors(space, seed)
    out = SimpleNamespace(survivors=idxs, combos=0, known_answers=0, rounds=0, sweep_answers=0, sweep_rounds=0,
                          reach_answers=0, reach_rounds=0)
    reach_rng = random.Random(f"{seed}:slot-reach")
    cap = getattr(engine, "hit_cap", 256)
    for idx in idxs:
        m = " ".join(space.decode(idx))
        combos = []                                   # (pass_i, path_i, form, hash160), every combination
        for pass_i, pp in enumerate(space.passes):
            combos += [(pass_i, path_i, form, h) for path_i, form, h in
                       sp_ref.hash160_forms(m, pp, space.paths, space.scheme)]
        out.combos = len(combos)
        owners = {}                                   # hash160 -> every combination that derives it
        for c in combos:
            owners.setdefault(c[3], []).append(c[:3])
        rounds, cur, cur_hits = [], [], 0
        for c in combos:
            n_hit = len(owners[c[3]])
            if n_hit > cap:
                raise EngineError(f"combo self-test: {n_hit} combinations share one hash160 but the hit buffer holds "
                                  f"{cap}")
            if cur and (len(cur) >= SELFTEST_MAX_TARGETS or cur_hits + n_hit > cap):
                rounds.append(cur)
                cur, cur_hits = [], 0
            cur.append(c)
            cur_hits += n_hit
        rounds.append(cur)
        for chunk in rounds:
            out.known_answers += _selftest_round(space, engine, idx, chunk, owners)
            out.rounds += 1
        answers, n_rounds = _slot_sweep(space, engine, idx, combos, owners)
        out.sweep_answers += answers
        out.sweep_rounds += n_rounds
        answers, n_rounds = _slot_reach(space, engine, idx, combos, owners, PLANTS_PER_BLOCK + PRIZE_COPIES,
                                        reach_rng)
        out.reach_answers += answers
        out.reach_rounds += n_rounds
    return out


# ---------------------------------------------------------------- checkpoint

def engine_files(root=HERE):
    kd = os.path.join(root, "kernels")
    cl = {f for f in os.listdir(kd) if f.startswith("sp_") and f.endswith(".cl")}
    cl |= {k + ".cl" for k in ENGINE_BASE_KERNELS}
    return ["kernels/" + f for f in sorted(cl)] + list(ENGINE_PY) + list(ENGINE_DATA)


def engine_hash(root=HERE):
    """sha256 over the engine's sources (name, length, bytes of each): see ENGINE_PY / ENGINE_DATA / engine_files."""
    h = hashlib.sha256()
    for rel in engine_files(root):
        with open(os.path.join(root, rel), "rb") as f:
            data = f.read()
        h.update(f"{rel}\0{len(data)}\0".encode())
        h.update(data)
    return h.hexdigest()[:16]


def sig_gpu(space, prize_addr, ub):
    base = sps.run_signature(space.cfg, prize_addr, space.paths_txt, space.passes)
    return hashlib.sha256(repr((base, "gpu-v5-plants-signoff", ub, engine_hash())).encode()).hexdigest()[:16]


class Ckpt:
    """SIG line, PLANT line, then `done <block> <tested> <derived> ok` lines, each appended + fsynced. A block's
    `done` line is written only after its own plant was found in place. A torn last line is ignored (that block
    simply runs again). `plant <k> ok` lines written by the previous (3-plant) version are tolerated and ignored."""

    def __init__(self, path, sig, plant_seed):
        self.path, self.sig, self.plant_seed, self.f = path, sig, plant_seed, None

    def load(self):
        done, seed, saved, torn = {}, self.plant_seed, None, False
        existed = bool(self.path) and os.path.exists(self.path)
        txt = ""
        if existed:
            with open(self.path, encoding="utf-8") as f:
                txt = f.read()
            existed = txt != ""                        # created but never written (a crash before the header): new
        if existed:
            lines = txt.splitlines()
            if not lines or lines[0].strip() != f"SIG {self.sig}":
                raise SystemExit(f"checkpoint {self.path} belongs to a different run (prize, config, block size or "
                                 f"engine) - refusing to resume")
            torn = not txt.endswith("\n")
            for line in lines[1:]:
                p = line.split()
                if len(p) == 2 and p[0] == "PLANT":
                    saved = int(p[1])
                elif len(p) == 5 and p[0] == "done" and p[4] == "ok":
                    done[int(p[1])] = (int(p[2]), int(p[3]))
            if done and saved is None:
                raise SystemExit(f"checkpoint {self.path} has finished blocks but no PLANT line - refusing to resume")
            if saved is not None:
                if seed is not None and seed != saved:
                    raise SystemExit(f"checkpoint was started with --plant-seed {saved}, not {seed}")
                seed = saved
        if seed is None:
            seed = secrets.randbits(32)
        self.plant_seed = seed
        if self.path:
            self.f = open(self.path, "a", encoding="utf-8")
            if torn:
                self.f.write("\n")                     # never glue a new line onto a torn one
            if not existed:
                self.f.write(f"SIG {self.sig}\n")
            if saved is None:
                self.f.write(f"PLANT {seed}\n")
            self._sync()
        return seed, done

    def _sync(self):
        if self.f:
            self.f.flush()
            os.fsync(self.f.fileno())

    def done(self, b, tested, derived):
        if self.f:
            self.f.write(f"done {b} {tested} {derived} ok\n")
            self._sync()

    def close(self):
        if self.f:
            self.f.close()
            self.f = None


# ---------------------------------------------------------------- estimate, bench, run

def estimate(space, rate):
    e = space.estimate()
    e["rate"] = rate
    e["seconds"] = e["derivations"] / rate
    return e


def bench(space, *, gpu=None, log=print, seed=1, target_deriv=1.5e6):
    """Rate on this config with a DUMMY target (never the prize) and one seed planted inside the span."""
    e = space.estimate()
    span = int(min(space.total, max(4096, target_deriv / max(e["valid_rate"] * len(space.passes), 1e-12))))
    plant = make_block_plant(space, seed, 0, 0, span)
    targets = [os.urandom(20)] + ([plant.h160] if plant is not None else [])
    eng = Engine(space, targets, gpu=gpu, log=log)
    t0 = time.perf_counter()
    res = eng.scan_block(0, span)
    dt = time.perf_counter() - t0
    found = plant is not None and any(
        h.tgt_i == 1 and (h.idx, h.pass_i, h.path_i, h.form) == (plant.idx, plant.pass_i, plant.path_i, plant.form)
        for h in res.hits)
    npass = len(space.passes)
    return {"config_words": space.n, "scheme": space.scheme, "paths": len(space.paths), "passes": npass,
            "span": span, "seconds": dt, "scanned": res.scanned, "tested": res.tested, "valid": res.valid,
            "cand_per_s": res.scanned / dt, "valid_per_s": res.valid / dt, "deriv_per_s": res.valid * npass / dt,
            "plant_found": found, "coverage_exact": res.scanned == span and res.processed == res.valid,
            "prize_loaded": False}


def probe_rate(space, gpu, log):
    b = bench(space, gpu=gpu, log=log, target_deriv=3e5)
    if not (b["plant_found"] and b["coverage_exact"]):
        raise EngineError("rate probe did not find its planted seed / cover its span: the engine is broken")
    return b["deriv_per_s"]


def _ledger(path, rec):
    if not path:
        return
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=repr) + "\n")


def _as_index(v):
    """v as a Python int if it is a true integer (int, numpy integer); TypeError for bool, float, str, None."""
    if isinstance(v, bool):
        raise TypeError("bool is not an index")
    return operator.index(v)


def _cpu_confirms(space, h, target):
    """The CPU re-derives the GPU-reported hit `h` and compares it to `target`. The only thing a FOUND (or an
    accepted twin report on a plant) is allowed to rest on. False - never an exception - for any malformed report;
    negative indices are refused explicitly (Python would silently wrap them onto a real passphrase / path)."""
    try:
        idx, pass_i, path_i, form = (_as_index(v) for v in (h.idx, h.pass_i, h.path_i, h.form))
        if not (0 <= idx < space.total and 0 <= pass_i < len(space.passes) and 0 <= path_i < len(space.paths_txt)
                and form in (0, 1)):
            return False
        m = " ".join(space.decode(idx))
        return sp_ref.confirm(m, space.passes[pass_i], space.paths_txt[path_i], form, space.scheme, target) is True
    except Exception:
        return False


def _in_block(h, lo, hi):
    try:
        return lo <= _as_index(h.idx) < hi
    except TypeError:
        return False


def _plain(v):
    try:
        return _as_index(v)
    except TypeError:
        return v


def _hit_rec(h, b):
    """A GPU report as the operator needs it to re-check that launch on the CPU: indices only, never a mnemonic."""
    at = tuple(h.at) if isinstance(h.at, (tuple, list)) and len(h.at) == 4 else (None,) * 4
    rec = {"block": b, "idx": h.idx, "pass_i": h.pass_i, "path_i": h.path_i, "form": h.form, "tgt_i": h.tgt_i,
           "chunk_start": at[0], "chunk_count": at[1], "batch_off": at[2], "batch_count": at[3]}
    return {k: _plain(v) for k, v in rec.items()}


def _signoff_text(signoff):
    """The operator's sign-off as it is recorded: a non-blank string, stripped. None for anything else."""
    if isinstance(signoff, str) and signoff.strip():
        return signoff.strip()
    return None


def _positive_finite(name, v):
    try:
        ok = not isinstance(v, bool) and math.isfinite(v) and v > 0
    except TypeError:
        ok = False
    if not ok:
        raise ValueError(f"{name} must be a finite number > 0, got {v!r}")


def run_search(space, *, prize_addr=TARGET, checkpoint=None, max_hours=1.0, force=False, dry_run=False,
               assume_rate=None, plant_seed=None, max_blocks=None, log=print, gpu=None, ledger=LEDGER,
               found_path=FOUND_PATH, surv_cap=1 << 22, signoff=None):
    """A dry run (estimate and cost guard, no GPU) or a real run. A real run needs `signoff`, the operator's
    explicit yes in words: without it the run is REFUSED before anything touches the GPU or the disk. With it, the
    rate is measured on the GPU (probe_rate: a dummy target and a planted seed, never the prize) and only then does
    the cost guard decide - so a run refused by the cost guard has run the rate probe, and nothing else."""
    _positive_finite("max_hours", max_hours)
    if max_hours > MAX_HOURS:
        raise ValueError(f"max_hours must be at most {MAX_HOURS} (one week), got {max_hours!r}")
    if assume_rate is not None:
        if not dry_run:
            raise ValueError("assume_rate is only allowed with dry_run: a real run measures its own rate")
        _positive_finite("assume_rate", assume_rate)
    _check_passes(space, sp_space.SpaceError)
    signed = _signoff_text(signoff)
    out = SimpleNamespace(status=None, exit_code=None, detail="", found=None, est=None, blocks_done=0,
                          tested=0, valid=0, plants_ok=False, blocks_verified=0, blocks_from_checkpoint=0,
                          selftest=None, plant_seed=None, audits=0, audits_skipped=0, plants_found=0, signoff=signed)
    if not dry_run and signed is None:
        out.status, out.exit_code, out.detail = "REFUSED", EXIT_REFUSED, NO_SIGNOFF
        log("REFUSED: " + out.detail)
        return out
    prize = hash160_of_address(prize_addr)
    ub = plan_block_units(space)
    nblocks = -(-space.units // ub)
    rate = assume_rate if assume_rate is not None else (DEFAULT_ASSUME_RATE if dry_run else None)
    if rate is None:
        if gpu is None:
            from sp_gpu import Gpu
            gpu = Gpu.get()
        rate = probe_rate(space, gpu, log)
    est = out.est = estimate(space, rate)
    log(f"{space.scheme} {space.n}w | raw {space.total:,} | ~{est['derivations']:,.0f} derivations x "
        f"{len(space.paths)} path(s) x 2 key forms | rate {rate:,.0f}/s "
        f"({'assumed' if dry_run else 'measured'}) -> ~{est['seconds'] / 3600:.2f} h | "
        f"{nblocks:,} blocks of {ub:,} unit(s), up to {PLANTS_PER_BLOCK} planted seeds in each, the prize at "
        f"{PRIZE_COPIES} target slots")
    if est["seconds"] > max_hours * 3600 and not force:
        out.status, out.exit_code = "REFUSED", EXIT_REFUSED
        out.detail = (f"projected {est['seconds'] / 3600:.2f} h exceeds --max-hours {max_hours}; "
                      f"operator sign-off is required: re-run with --force")
        log("REFUSED: " + out.detail)
        return out
    if dry_run:
        out.status, out.exit_code = "DRY_RUN", EXIT_NO_MATCH
        log("DRY RUN (estimate only): nothing was searched and no GPU was touched - exit 0 here is NOT a NO MATCH")
        return out

    sig = sig_gpu(space, prize_addr, ub)
    ck = Ckpt(checkpoint, sig, plant_seed)
    try:
        return _search(space, out, ck, prize, prize_addr, sig, ub, nblocks, est, max_blocks, log, gpu, ledger,
                       found_path, surv_cap, signoff=signed)
    finally:
        ck.close()


def _search(space, out, ck, prize, prize_addr, sig, ub, nblocks, est, max_blocks, log, gpu, ledger, found_path,
            surv_cap, signoff=None):
    signed = _signoff_text(signoff)
    if signed is None:                                   # checked here too: whatever led here, nothing is searched,
        out.status, out.exit_code, out.detail = "REFUSED", EXIT_REFUSED, NO_SIGNOFF      # loaded or built without it
        log("REFUSED: " + out.detail)
        return out
    seed, done = ck.load()
    out.plant_seed = seed
    from_ck = out.blocks_from_checkpoint = out.blocks_done = len(done)
    eng = Engine(space, [prize], gpu=gpu, log=log, surv_cap=surv_cap)
    npass = len(space.passes)
    rate_pass = space.estimate()["valid_rate"]
    exact_count = rate_pass >= 1                         # no filter (bip39raw): binomial_problem checks survivors == tested
    block = ub * space.per_unit
    tested = sum(t for t, _ in done.values())
    valid = sum(d for _, d in done.values()) // npass
    t_start = time.perf_counter()
    session = [0]                                        # derivations completed in this process

    def note(status, **extra):
        try:
            _ledger(ledger, dict(ts=time.strftime("%Y-%m-%dT%H:%M:%S"), status=status, target=prize_addr, sig=sig,
                                 signoff=signed, **extra))
        except OSError as e:                             # the ledger is an audit trail: it never decides a result
            log(f"COULD NOT WRITE the ledger {ledger}: {e}")

    def beat(res, cur):
        el = max(time.perf_counter() - t_start, 1e-9)
        now = session[0] + res.valid * npass
        left = max(0.0, est["derivations"] - sum(d for _, d in done.values()) - res.valid * npass)
        rate = now / el
        log(f"  index {cur:,} | {rate:,.0f} deriv/s | ETA {left / max(rate, 1.0) / 60:,.1f} min | "
            f"GPU {gpu_temp_c()} C")

    def void(reason, **extra):
        out.status, out.exit_code, out.detail = "VOID", EXIT_VOID, reason
        log("VOID: " + reason)
        note("VOID", reason=reason, blocks_done=out.blocks_done, **extra)
        return out

    def log_reports(title, recs):
        log(f"  {title} - re-check these launches on the CPU:")
        for rec in recs:
            log("    " + json.dumps(rec, default=repr))

    def judge_prize(b, lo, hi, hits, why=None):
        """A prize hit is judged by the CPU alone, before any control: a confirmed key is a key even if something
        else in this block looks wrong (or an exception cut the block short: `why`), and an unconfirmed GPU report
        is never a claim. `hits` carry the run's own target meaning (0 = prize). None when there is no prize hit;
        otherwise the FOUND or VOID result."""
        prize_hits = [h for h in hits if h.tgt_i == 0]
        if not prize_hits:
            return None
        ok = [_cpu_confirms(space, h, prize) for h in prize_hits]
        confirmed = [h for h, c in zip(prize_hits, ok) if c]
        n_bad = ok.count(False)
        reports = []                                     # ALL prize reports of this block, once any is unconfirmed
        if n_bad:
            reports = [dict(_hit_rec(h, b), cpu_confirmed=c) for h, c in zip(prize_hits, ok)]
            log_reports(f"block {b} [{lo:,}, {hi:,}){' (' + why + ')' if why else ''}: {n_bad} of "
                        f"{len(prize_hits)} prize report(s) not confirmed by the CPU; every prize report of this "
                        f"block", reports)
        if not confirmed:
            return void(f"block {b}: the GPU reported {n_bad} prize hit(s) that the CPU does not confirm "
                        f"(logged with their launch coordinates): engine bug; no FOUND written"
                        + (f" [{why}]" if why else ""), block=b, prize_hits=reports)
        h = Hit(*(_as_index(v) for v in confirmed[0][:4]), 0)            # confirmed: true integers, in range
        out.status, out.exit_code = "FOUND", EXIT_FOUND
        out.found = {"mnemonic": " ".join(space.decode(h.idx)), "passphrase": space.passes[h.pass_i],
                     "path": space.paths_txt[h.path_i], "form": h.form, "scheme": space.scheme,
                     "index": h.idx, "indices": sorted({_as_index(x.idx) for x in confirmed})}
        if n_bad:
            out.found["unconfirmed_gpu_reports"] = n_bad
        msg = ("".join(f"{k}: {v!r}\n" for k, v in out.found.items()) + f"target: {prize_addr}\n")
        log("\n*** MATCH ***\n" + msg + "READ-ONLY: record in STATE.md under '## !!! CANDIDATE KEY' and STOP. "
            "Claiming is the operator's decision.")       # printed first: a full disk must not hide a candidate
        try:
            with open(found_path, "a", encoding="utf-8") as f:      # append: never destroy an earlier candidate
                f.write(f"# FOUND {time.strftime('%Y-%m-%dT%H:%M:%S')} sig {sig}\n{msg}\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            log(f"COULD NOT WRITE {found_path}: {e} - copy the candidate above by hand")
        note("FOUND", index=h.idx, indices=out.found["indices"], block=b,
             **({"prize_hits": reports} if reports else {}), **({"after_error": why} if why else {}))
        return out

    # The seed that makes every plant reproducible is logged before anything can go wrong - with or without a
    # checkpoint - so a VOID or a crash can be re-run against the same controls.
    log(f"operator sign-off: {signed}")
    log(f"plant seed {seed}" + (f" (checkpoint line PLANT; re-run with --plant-seed {seed})" if ck.path else
                                 f" (no checkpoint: re-run with --plant-seed {seed} to reproduce the controls)"))
    note("START", plant_seed=seed, blocks=nblocks, blocks_from_checkpoint=from_ck, block_units=ub,
         plants_per_block=PLANTS_PER_BLOCK, prize_copies=PRIZE_COPIES)

    pending = [b for b in range(nblocks) if b not in done]
    if pending:
        try:
            st = out.selftest = combo_selftest(space, eng, seed)
            eng.set_targets([prize])                     # not left holding the self-test's targets
        except EngineError as e:
            return void(str(e))
        except Exception as e:
            return void(f"combo self-test: engine error: {type(e).__name__}: {e}")
        if st.survivors:
            log(f"combo self-test passed: {len(st.survivors)} survivor(s) x {st.combos} (passphrase, path, key form) "
                f"combos = {st.known_answers} known answers, exact, through K1/K2/K3 in {st.rounds} round(s); slot "
                f"sweep: {getattr(st, 'sweep_answers', 0)} more in {getattr(st, 'sweep_rounds', 0)} round(s); slot "
                f"reach: {getattr(st, 'reach_answers', 0)} more in {getattr(st, 'reach_rounds', 0)} round(s)")
        else:
            log("combo self-test: the CPU found no filter-passing candidate to test with - skipped")
    else:
        log("combo self-test: not run - every block is already in the checkpoint")

    ran = 0
    for b in range(nblocks):
        if b in done:
            continue
        lo, hi = b * block, min(space.total, (b + 1) * block)
        try:
            plants = make_block_plants(space, seed, b, lo, hi)
        except sp_space.SpaceError as e:
            return void(f"block {b}: cannot plant its control seed: {e}")
        if not plants:
            log(f"  block {b} [{lo:,}, {hi:,}): the CPU found no filter-passing candidate in it; no plant, and K1 "
                f"must report 0 valid")
        targets, prize_slots = block_targets(prize, [p.h160 for p in plants], b, seed)
        eng.partial = None
        try:
            eng.set_targets(targets)
            res = eng.scan_block(lo, hi, beat)
            res.hits = [h._replace(tgt_i=_logical_tgt(h.tgt_i, prize_slots, len(plants))) for h in res.hits]
        except Exception as e:                           # KeyboardInterrupt is not an Exception: it propagates
            err = f"{type(e).__name__}: {e}"
            partial = []                                 # the hits gathered before the exception, in the run's terms
            try:
                raw_hits = list(getattr(getattr(eng, "partial", None), "hits", None) or [])
            except Exception:
                raw_hits = []                            # a corrupt result object: nothing to judge
            for h in raw_hits:
                try:
                    partial.append(h._replace(tgt_i=_logical_tgt(h.tgt_i, prize_slots, len(plants))))
                except Exception:
                    pass                                 # a malformed report can never be confirmed
            r = judge_prize(b, lo, hi, partial, why=f"engine error in block {b}: {err}")
            if r is not None:
                return r
            recs = [_hit_rec(h, b) for h in partial]
            if recs:
                log_reports(f"block {b}: engine error - the {len(recs)} hit(s) gathered before it", recs)
            return void(f"engine error in block {b}: {err}", block=b, partial_hits=recs)

        # 1. A prize hit is judged by the CPU alone, before any control.
        r = judge_prize(b, lo, hi, res.hits)
        if r is not None:
            return r

        # 2. Controls: coverage, survivor accounting, then every plant of this block at its planted place.
        if res.scanned != hi - lo:
            return void(f"block {b}: scanned {res.scanned:,} of {hi - lo:,} candidates")
        want_sum = expected_idx_sum(lo, hi - lo)         # from THIS loop's own lo/hi: a shifted request is caught too
        if res.idx_sum != want_sum:
            return void(f"block {b}: K1 index checksum {res.idx_sum!r} != {want_sum:#x} for [{lo:,}, {hi:,}): the "
                        f"engine did not scan exactly the block that was asked for")
        if res.processed != res.valid:
            return void(f"block {b}: K2/K3 were handed {res.processed:,} of {res.valid:,} survivors")
        if not plants and res.valid != 0:
            return void(f"block {b}: K1 reported {res.valid:,} valid candidates where the CPU found none")
        why = binomial_problem(res.tested, res.valid, rate_pass)
        if why:
            return void(f"block {b}: implausible survivor count ({'exact' if exact_count else 'binomial'} check): {why}")
        if not space.cfg["distinct"] and res.tested != res.scanned:
            # without DISTINCT nothing is filtered before `tested` is counted: tested must equal scanned. Tested and
            # valid shrinking together (a K1 that skips a slice of candidates after the thread count) is otherwise
            # invisible to the binomial check, which only sees their ratio
            return void(f"block {b}: K1 tested {res.tested:,} of the {res.scanned:,} candidates it scanned although the "
                        f"config has no DISTINCT filter (every scanned candidate must be tested)")
        exact = [False] * len(plants)
        for h in res.hits:                               # tgt_i: 0 = the prize (judged above), 1 + j = plant j
            try:
                j = _as_index(h.tgt_i) - 1
            except TypeError:
                j = -1
            if not 0 <= j < len(plants):
                return void(f"block {b}: hit {tuple(h)} names a target that does not exist")
            plant = plants[j]
            if (h.idx, h.pass_i, h.path_i, h.form) == (plant.idx, plant.pass_i, plant.path_i, plant.form):
                exact[j] = True
            elif not (_in_block(h, lo, hi) and _cpu_confirms(space, h, plant.h160)):
                return void(f"block {b}: its plant #{j} was reported at a place the CPU does not confirm inside the "
                            f"block: {tuple(h)}")
            # else: the same seed reached another way inside this block (a duplicated REQUIRE word, a repeated
            # PATH) - a true twin
        lost = [j for j, ok in enumerate(exact) if not ok]
        if lost:
            names = "; ".join(f"plant #{j} (index {plants[j].idx:,}, passphrase #{plants[j].pass_i}, path "
                              f"#{plants[j].path_i}, form {plants[j].form})" for j in lost[:8])
            return void(f"block {b}: {len(lost)} of its {len(plants)} plant{'s' if len(plants) != 1 else ''} "
                        f"{'was' if len(lost) == 1 else 'were'} not found: {names}"
                        + (f"; and {len(lost) - 8} more" if len(lost) > 8 else ""), block=b, plants_lost=lost)

        # 3. The window audit, after the block's own controls (a confirmed prize hit was judged above, before any
        # control): K1's survivor set and K2/K3's hash160s on a sampled window, against the CPU. It runs on launches of
        # its own, so it cannot change anything the block above just measured.
        try:
            audit = eng.k1_spot_check(lo, hi, seed)
        except Exception as e:                           # KeyboardInterrupt is not an Exception: it propagates
            return void(f"block {b}: window audit (K1 survivor set + K2/K3 parity spot check): "
                        f"{type(e).__name__}: {e}", block=b)
        out.audits += 1
        if getattr(audit, "skipped", False):
            out.audits_skipped += 1
        ck.done(b, res.tested, res.valid * npass)
        done[b] = (res.tested, res.valid * npass)
        session[0] += res.valid * npass
        out.plants_found += len(plants)
        tested += res.tested
        valid += res.valid
        out.blocks_done = len(done)
        ran += 1
        out.blocks_verified = ran
        if max_blocks is not None and ran >= max_blocks:
            out.status, out.exit_code, out.tested, out.valid = "STOPPED", EXIT_STOPPED, tested, valid
            out.detail = f"stopped after {ran} block(s) at max_blocks: {len(done)} of {nblocks} done - not a result"
            return out
    out.tested, out.valid = tested, valid
    out.blocks_done = len(done)
    extra = sorted(set(done) - set(range(nblocks)))
    covered = sum(min(space.total, (b + 1) * block) - b * block for b in done if 0 <= b < nblocks)
    if extra or len(done) != nblocks or covered != space.total:
        return void(f"finished blocks cover {covered:,} of {space.total:,} candidates ({len(done)} of {nblocks} "
                    f"blocks; block numbers outside the space: {extra[:5]})")
    out.status, out.exit_code, out.plants_ok = "NO_MATCH", EXIT_NO_MATCH, True
    count_txt = ("exact survivor-count check (no filter: survivors == tested in every block)" if exact_count else
                 "binomial survivor-count check")
    st = out.selftest
    st_txt = (f"combo self-test: {len(st.survivors)} survivor(s) x {st.combos} (passphrase, path, key form) combos = "
              f"{st.known_answers} known answers, exact, + {getattr(st, 'sweep_answers', 0)} slot-sweep answers (every value of every "
              f"dimension at K3 slots 0 and 1 and alone) + {getattr(st, 'reach_answers', 0)} slot-reach answers (every "
              f"slot a block searches)" if st is not None and st.survivors else
              "combo self-test: not run" + (" (no survivor to test with)" if st is not None else
                                            " (every block was already in the checkpoint)"))
    out.detail = (f"tested {tested:,} | valid {valid:,} | {nblocks} blocks: {ran} verified in this session, "
                  f"{from_ck} taken from the checkpoint on trust (a hand-edited checkpoint is trusted) | {st_txt} | "
                  f"each session block: its own plants found in place ({out.plants_found:,} planted seeds in this "
                  f"session, up to {PLANTS_PER_BLOCK} per block), target slots rotated (the prize at {PRIZE_COPIES} "
                  f"slots), {count_txt}, "
                  f"tested == scanned where there is no DISTINCT, exact 64-bit K1 index sum, K2/K3 survivor "
                  f"slices checked, window audit (K1 survivor set + K2/K3 hash160 parity on a sampled window: "
                  f"{out.audits - out.audits_skipped} of {ran} blocks audited, {out.audits_skipped} skipped) | "
                  f"coverage exact")
    header = ("NO MATCH (verified)" if ran else
              "NO MATCH (trusted from the checkpoint - nothing was re-verified in this session)")
    log(header + ": " + out.detail)
    log("  not re-checked at run time (only sampled by the per-block window audit, not proven): the exact K1 survivor "
        "set outside the audited windows (rests on the sp_test_k1 parity tests against the CPU tool); plants are "
        "statistical controls for partial survivor loss")
    note("NO_MATCH", tested=tested, valid=valid, blocks=nblocks, blocks_verified=ran, blocks_from_checkpoint=from_ck,
         selftest_known_answers=(st.known_answers if st is not None else None),
         selftest_sweep_answers=(getattr(st, "sweep_answers", 0) if st is not None else None),
         selftest_reach_answers=(getattr(st, "reach_answers", 0) if st is not None else None),
         plants_found=out.plants_found, audits=out.audits, audits_skipped=out.audits_skipped)
    return out


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error, but 2 means FOUND here: usage errors exit 64 (EX_USAGE)."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def parse_args(argv=None):
    ap = _Parser(prog="gpu_setperm.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--checkpoint")
    ap.add_argument("--max-hours", type=float, default=1.0, help=f"cost guard, at most {MAX_HOURS}")
    ap.add_argument("--force", action="store_true", help="operator sign-off for a run over --max-hours")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="estimate and guard only; never touches the GPU")
    mode.add_argument("--bench", action="store_true", help="rate with a dummy target and a planted seed")
    ap.add_argument("--assume-rate", type=float, help="derivations/s for the estimate (only with --dry-run)")
    ap.add_argument("--plant-seed", type=int)
    ap.add_argument("--signoff", metavar="TEXT", help="the operator's sign-off in words (who approved what, and "
                    "when); every real run needs one, and it is written to the log and the ledger")
    a = ap.parse_args(argv)
    if a.assume_rate is not None and not a.dry_run:
        ap.error("--assume-rate is only allowed with --dry-run: a real run measures its own rate")
    for name, v in (("--max-hours", a.max_hours), ("--assume-rate", a.assume_rate)):
        if v is not None and not (math.isfinite(v) and v > 0):
            ap.error(f"{name} must be a finite number > 0, got {v}")
    if a.max_hours > MAX_HOURS:
        ap.error(f"--max-hours must be at most {MAX_HOURS} (one week), got {a.max_hours}")
    return a


def main(argv=None):
    a = parse_args(argv)
    try:
        space = sp_space.Space.load(a.config)
    except sp_space.SpaceError as e:
        sys.exit(f"unsupported by the GPU engine: {e}")
    except OSError as e:
        sys.exit(f"cannot read the config: {e}")
    try:
        if a.bench:
            b = bench(space)
            print(json.dumps(b, indent=1))
            sys.exit(EXIT_NO_MATCH if b["plant_found"] and b["coverage_exact"] else EXIT_VOID)
        r = run_search(space, checkpoint=a.checkpoint, max_hours=a.max_hours, force=a.force, dry_run=a.dry_run,
                       assume_rate=a.assume_rate, plant_seed=a.plant_seed, signoff=a.signoff)
    except (EngineError, ValueError, OSError, ImportError) as e:   # a bad checkpoint / ledger path is a message, not a traceback
        sys.exit(f"cannot run: {e}")
    except Exception as e:                                          # e.g. no OpenCL platform / a driver error
        if type(e).__module__.split(".")[0] != "pyopencl":
            raise
        sys.exit(f"cannot run: OpenCL {type(e).__name__}: {e}")
    sys.exit(r.exit_code)


if __name__ == "__main__":
    main()
