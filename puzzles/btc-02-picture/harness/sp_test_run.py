import ast
import contextlib
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import sp_ref
from sp_testutil import cfg_from_text, make_text, require_gpu, small_config_texts
from template_solve import WORDS, address_of_hash160, hash160_of_address

HERE = os.path.dirname(os.path.abspath(__file__))
E4 = "templates/e4_pins_only.conf"
QUIET = lambda *_: None                                              # noqa: E731
SIGNOFF = "test sign-off (self-generated target)"                    # what a real run must carry: the operator's yes


def setUpModule():
    """ADR-0001 rail for every test in this file: no test may reach a real search of the prize address. run_search's
    search step is wrapped so that a call carrying the real prize (run_search's default!) raises before any engine, GPU
    launch, checkpoint or ledger is touched; tests use self-generated targets (or patch run_search's default)."""
    import gpu_setperm
    import template_solve
    real = gpu_setperm._search
    real_prize = hash160_of_address(template_solve.TARGET)

    def guarded(space, out, ck, prize, prize_addr, *args, **kw):
        if prize_addr == template_solve.TARGET or prize == real_prize:
            raise AssertionError("a test reached a real search of the real prize address (ADR-0001: tests use "
                                 "self-generated targets)")
        return real(space, out, ck, prize, prize_addr, *args, **kw)
    patcher = mock.patch.object(gpu_setperm, "_search", guarded)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class SizerAndThermal(unittest.TestCase):
    def test_sizer_converges_and_never_grows_after_a_slow_launch(self):
        from gpu_setperm import Sizer
        s = Sizer(start=4096)
        rate = 1e6                                   # items/s
        for _ in range(40):
            s.record(s.n, s.n / rate)
        self.assertLess(abs(s.n / rate - 0.3), 0.06)
        before = s.n
        s.record(before, 2.0)                        # a 2 s launch (watchdog danger)
        self.assertLessEqual(s.n, before // 2)
        s.record(s.n, 0.0)                           # instantaneous launch: bounded growth
        self.assertLessEqual(s.n, 2 * (before // 2) + 1)
        big = Sizer(start=100, hi=1 << 12)
        for _ in range(30):
            big.record(big.n, 1e-9)
        self.assertEqual(big.n, 1 << 12)

    def test_sizer_ignores_latency_bound_small_launches(self):
        """Measured on this card: K2 launches of 1k..8k threads all take ~21 ms, so a rate taken from
        them is ~50x too low. The Sizer must double per launch (never jump), not extrapolate."""
        from gpu_setperm import Sizer
        s = Sizer(start=1 << 10, hi=1 << 17)
        prev = s.n
        for _ in range(6):
            s.record(s.n, 0.021)                     # flat 21 ms whatever the size
            self.assertLessEqual(s.n, 2 * prev)
            prev = s.n
        self.assertEqual(s.n, (1 << 10) * 2 ** 6)

    def test_sizer_halves_after_any_launch_over_the_hard_limit(self):
        """0.55 s is over the 0.5 s watchdog margin but the 0.3 s/0.55 s ratio alone would only shrink by 45%."""
        from gpu_setperm import Sizer
        s = Sizer(start=100_000)
        s.record(100_000, 0.55)
        self.assertLessEqual(s.n, 50_000)
        s = Sizer(start=100_000)
        s.record(100_000, 0.45)                        # under the hard limit: shrinks toward 0.3 s but not forced to half
        self.assertGreater(s.n, 50_000)
        self.assertLess(s.n, 100_000)

    def test_wait_cool_sleeps_until_below_limit(self):
        from gpu_setperm import wait_cool
        temps, slept = [90, 88, 80], []
        wait_cool(85, log=QUIET, temp_fn=lambda: temps.pop(0), sleep=slept.append)
        self.assertEqual(len(slept), 2)
        wait_cool(85, log=QUIET, temp_fn=lambda: None, sleep=slept.append)   # no sensor: never block
        self.assertEqual(len(slept), 2)

    def test_watchdog_limits(self):
        """K1's cost per candidate varies up to 4.7x with the position in a template: the chunk cap is 1<<25.
        One survivor's K2+K3 pair runs all its passphrases at once: at most 4096 of them."""
        import gpu_setperm
        self.assertLessEqual(gpu_setperm.K1_MAX_CHUNK, 1 << 25)
        self.assertEqual(gpu_setperm.MAX_PASSES, 4096)
        self.assertLessEqual(gpu_setperm.BEAT_SECONDS, 5)


def _fake_clock_engine(npass, k1_run, k2_launch, s1, s2, clock, k3_run=None):
    """An Engine with fake kernels on a fake clock (no GPU): only the host logic of scan_block runs. The fakes
    record what the real wrappers record: K1's index sum (exact for the range it was asked for) and the survivor
    slices K2 and K3 were handed."""
    import gpu_setperm
    from sp_k1_enum import expected_idx_sum
    eng = object.__new__(gpu_setperm.Engine)
    eng.space, eng.log, eng.max_temp, eng.partial = SimpleNamespace(passes=[""] * npass), QUIET, 85, None
    k1 = SimpleNamespace(cap=1 << 20, surv=None, last_idx_sum=None)
    k2 = SimpleNamespace(seeds=None, max_threads=1 << 17, last_slice=None)
    k3 = SimpleNamespace(last_slice=None)

    def run1(start, count):
        out = k1_run(start, count)
        k1.last_idx_sum = expected_idx_sum(start, count)
        return out

    def launch2(surv, off, nsurv):
        k2.last_slice = (int(off), int(nsurv))
        return k2_launch(surv, off, nsurv)

    def run3(seeds, surv, off, nsurv, npass_):
        k3.last_slice = (int(off), int(nsurv))
        return (k3_run or (lambda *a: []))(seeds, surv, off, nsurv, npass_)
    k1.run, k2.launch, k3.run = run1, launch2, run3
    eng.k1, eng.k2, eng.k3 = k1, k2, k3
    eng.s1, eng.s2 = s1, s2
    return eng


class HeartbeatNoGpu(unittest.TestCase):
    """The 5-second heartbeat + thermal check must follow EVERY launch: a long stretch of K1 chunks without a
    single survivor never enters the K2 loop, and used to never pause for heat."""

    def test_the_thermal_check_follows_every_k1_launch_even_without_survivors(self):
        import gpu_setperm
        clock = [0.0]

        def k1_run(start, count):
            clock[0] += 6.0                                                   # every K1 launch "takes" 6 s
            return count, count, 0

        def no_k2(*a):
            raise AssertionError("no survivors: K2 must not run")
        eng = _fake_clock_engine(1, k1_run, no_k2, gpu_setperm.Sizer(100, lo=100, hi=100), gpu_setperm.Sizer(64), clock)
        cooled, beats = [], []
        with mock.patch.object(gpu_setperm, "time", SimpleNamespace(perf_counter=lambda: clock[0])), \
                mock.patch.object(gpu_setperm, "wait_cool", lambda limit, log: cooled.append(limit)):
            res = eng.scan_block(0, 1000, heartbeat=lambda r, cur: beats.append(cur))
        self.assertEqual((res.scanned, res.valid), (1000, 0))
        self.assertEqual(cooled, [85] * 10)
        self.assertEqual(beats, list(range(0, 1000, 100)))

    def test_the_thermal_check_follows_every_k2_batch(self):
        import gpu_setperm
        clock = [0.0]

        def k2_launch(surv, off, nsurv):
            clock[0] += 6.0
        eng = _fake_clock_engine(1, lambda start, count: (count, count, 5), k2_launch,
                                 gpu_setperm.Sizer(100, lo=100, hi=100), gpu_setperm.Sizer(1, lo=1, hi=1), clock)
        cooled = []
        with mock.patch.object(gpu_setperm, "time", SimpleNamespace(perf_counter=lambda: clock[0])), \
                mock.patch.object(gpu_setperm, "wait_cool", lambda limit, log: cooled.append(limit)):
            res = eng.scan_block(0, 100)
        self.assertEqual((res.valid, res.processed), (5, 5))
        self.assertEqual(len(cooled), 5)                                      # one per 6-second K2 batch

    def test_the_five_second_interval_runs_across_blocks(self):
        """A run of many short blocks (each under 5 s) must still get its thermal check: the interval is
        measured from the last check, not restarted at every block."""
        import gpu_setperm
        clock = [0.0]

        def k1_run(start, count):
            clock[0] += 3.0
            return count, count, 0
        eng = _fake_clock_engine(1, k1_run, lambda *a: None, gpu_setperm.Sizer(100, lo=100, hi=100),
                                 gpu_setperm.Sizer(64), clock)
        cooled = []
        with mock.patch.object(gpu_setperm, "time", SimpleNamespace(perf_counter=lambda: clock[0])), \
                mock.patch.object(gpu_setperm, "wait_cool", lambda limit, log: cooled.append(limit)):
            eng.scan_block(0, 100)                                            # 3 s since the start: no check yet
            self.assertEqual(cooled, [])
            eng.scan_block(100, 200)                                          # 6 s since the start: the check runs
        self.assertEqual(cooled, [85])


class GuardAndPlan(unittest.TestCase):
    def test_dry_run_on_e4_needs_no_gpu_and_the_guard_refuses_without_force(self):
        from gpu_setperm import run_search
        from sp_space import Space
        sp = Space.load(E4)
        r = run_search(sp, dry_run=True, assume_rate=300_000, log=QUIET)
        self.assertEqual((r.status, r.exit_code), ("DRY_RUN", 0))
        self.assertAlmostEqual(r.est["derivations"] / 3.3e8, 1, delta=0.05)      # spec: ~3.3e8
        r = run_search(sp, dry_run=True, assume_rate=300_000, max_hours=0.1, log=QUIET)
        self.assertEqual((r.status, r.exit_code), ("REFUSED", 3))
        r = run_search(sp, dry_run=True, assume_rate=300_000, max_hours=0.1, force=True, log=QUIET)
        self.assertEqual(r.status, "DRY_RUN")

    def test_block_plan(self):
        from gpu_setperm import plan_block_units
        from sp_space import Space
        sp = Space.load(E4)
        ub = plan_block_units(sp)
        self.assertGreaterEqual(ub, 1)
        self.assertLessEqual(ub, sp.units)

    def test_every_block_gets_its_own_plant_inside_it_deterministic_and_verifiable(self):
        from gpu_setperm import make_block_plant
        from sp_space import Space
        empty_seen = 0
        for name, ub in (("A_bip39_12_r3k1", 3), ("D_electrum_12_wordlist", 5), ("J_bip39_12_dups", 4)):
            sp = Space(cfg_from_text(dict(small_config_texts())[name]))
            block = ub * sp.per_unit
            got = []
            for b in range(-(-sp.units // ub)):
                lo, hi = b * block, min(sp.total, (b + 1) * block)
                p = make_block_plant(sp, 42, b, lo, hi)
                if not any(sp.candidate_ok(i) for i in range(lo, hi)):    # J's 48-candidate blocks can be empty
                    self.assertIsNone(p, (name, b))
                    empty_seen += 1
                    got.append(p)
                    continue
                self.assertIsNotNone(p, (name, b))
                self.assertTrue(lo <= p.idx < hi, (name, b))
                self.assertTrue(sp.candidate_ok(p.idx))
                self.assertTrue(0 <= p.pass_i < len(sp.passes) and 0 <= p.path_i < len(sp.paths) and p.form in (0, 1))
                m = " ".join(sp.decode(p.idx))
                self.assertTrue(sp_ref.confirm(m, sp.passes[p.pass_i], sp.paths_txt[p.path_i], p.form, sp.scheme, p.h160))
                self.assertEqual(make_block_plant(sp, 42, b, lo, hi), p)                    # deterministic per (seed, b)
                got.append(p)
            other = [make_block_plant(sp, 43, b, min(sp.total, b * block), min(sp.total, (b + 1) * block))
                     for b in range(len(got))]
            self.assertNotEqual(got, other, name)                                           # the seed matters
            same_range = {make_block_plant(sp, 42, b, 0, sp.total) for b in range(8)}
            self.assertGreater(len(same_range), 1, name)                                    # so does the block number
        self.assertGreater(empty_seen, 0)                                                   # the None path ran

    def test_every_passphrase_path_and_key_form_is_planted_over_many_blocks(self):
        """One random (passphrase, path, form) per block: over e4's blocks (4 paths x 2 forms) and 200 blocks of
        A (3 passphrases x 2 paths x 2 forms) every combination is planted at least once."""
        from gpu_setperm import make_block_plant, plan_block_units
        from sp_space import Space
        e4 = Space.load(E4)
        ub = plan_block_units(e4)
        block, nb = ub * e4.per_unit, -(-e4.units // ub)
        self.assertGreaterEqual(nb, 80)
        combos = set()
        for b in range(nb):
            lo, hi = b * block, min(e4.total, (b + 1) * block)
            p = make_block_plant(e4, 7, b, lo, hi)
            self.assertTrue(lo <= p.idx < hi)
            combos.add((p.pass_i, p.path_i, p.form))
        self.assertEqual(combos, {(0, path, form) for path in range(4) for form in (0, 1)})
        a = Space(cfg_from_text(dict(small_config_texts())["A_bip39_12_r3k1"]))
        combos = {(p.pass_i, p.path_i, p.form) for p in (make_block_plant(a, 7, b, 0, a.total) for b in range(200))}
        self.assertEqual(combos, {(pp, path, form) for pp in range(3) for path in range(2) for form in (0, 1)})

    def test_a_block_without_candidates_gives_none_when_small_and_space_error_when_large(self):
        import gpu_setperm
        from gpu_setperm import make_block_plant
        from sp_space import Space, SpaceError
        d = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        calls = [0]

        def never(i):
            calls[0] += 1
            return False
        d.candidate_ok = never
        self.assertIsNone(make_block_plant(d, 1, 0, 0, 5000))
        self.assertEqual(calls[0], 5000)                              # a small block is scanned once, exhaustively
        e4 = Space.load(E4)
        e4.candidate_ok = lambda i: False
        with self.assertRaises(SpaceError):
            make_block_plant(e4, 1, 0, 0, 10 ** 9)                    # too large to prove empty on the CPU
        # 200k random draws miss, then the exhaustive scan of a <= 1M block finds the one candidate
        x, calls[0] = 412_345, 0

        def late(i):
            calls[0] += 1
            return calls[0] > gpu_setperm.PLANT_TRIES and i == x
        e4.candidate_ok = late
        p = make_block_plant(e4, 1, 0, 0, 500_000)
        self.assertEqual(p.idx, x)
        self.assertEqual(calls[0], gpu_setperm.PLANT_TRIES + x + 1)
        # a small block with a single candidate always finds it
        d.candidate_ok = lambda i: i == 30_000
        self.assertEqual(make_block_plant(d, 5, 0, 0, d.total).idx, 30_000)


class CheckpointFile(unittest.TestCase):
    SIG = "SIGSIGSIGSIGSIGS"

    def test_skip_torn_line_resume_and_wrong_run_refusal(self):
        from gpu_setperm import Ckpt
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ck")
            c = Ckpt(path, self.SIG, plant_seed=7)
            self.assertEqual(c.load(), (7, {}))
            c.done(0, 10, 5)
            c.done(1, 11, 6)
            c.close()
            with open(path, "a") as f:
                f.write("plant 2 ok\n")                             # an older version's plant line: tolerated
                f.write("done 2 12")                                # torn line: no trailing ok
            c2 = Ckpt(path, self.SIG, plant_seed=None)
            self.assertEqual(c2.load(), (7, {0: (10, 5), 1: (11, 6)}))
            c2.done(2, 12, 7)                                       # the torn block re-runs and is recorded
            c2.close()
            c3 = Ckpt(path, self.SIG, plant_seed=None)
            _, done = c3.load()
            c3.close()
            self.assertEqual(done[2], (12, 7))                      # not glued onto the torn line
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            self.assertEqual(lines[:2], [f"SIG {self.SIG}", "PLANT 7"])
            self.assertEqual(sum(1 for x in lines if x.startswith(("SIG ", "PLANT "))), 2)   # header written once
            with self.assertRaises(SystemExit):
                Ckpt(path, "OTHEROTHEROTHERX", plant_seed=None).load()
            with self.assertRaises(SystemExit):                     # explicit plant seed that differs
                Ckpt(path, self.SIG, plant_seed=8).load()

    def test_torn_lines_never_count(self):
        """A line torn after its numbers but before 'ok' must not count (the numbers may be truncated)."""
        from gpu_setperm import Ckpt
        for torn in ("done 2 12 7", "done 2 12 7 o", "plant 1", "plant 1 o"):
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, "ck")
                c = Ckpt(path, self.SIG, plant_seed=7)
                c.load()
                c.done(0, 10, 5)
                c.close()
                with open(path, "a") as f:
                    f.write(torn)
                c = Ckpt(path, self.SIG, plant_seed=None)
                got = c.load()
                c.close()
                self.assertEqual(got, (7, {0: (10, 5)}), torn)

    def test_every_line_is_fsynced_when_written(self):
        """Every header / 'done' line is flushed and fsynced as it is written (the checkpoint's durability promise):
        a power cut loses at most the block in flight."""
        import gpu_setperm
        from gpu_setperm import Ckpt
        calls, real = [], os.fsync
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(gpu_setperm.os, "fsync", lambda fd: (calls.append(fd), real(fd))[1]):
            c = Ckpt(os.path.join(d, "ck"), self.SIG, plant_seed=1)
            c.load()
            n0 = len(calls)
            self.assertGreaterEqual(n0, 1)                                   # the header
            for b in range(3):
                c.done(b, 1, 1)
                self.assertEqual(len(calls), n0 + b + 1, b)
            c.close()

    def test_an_empty_file_is_a_new_checkpoint(self):
        from gpu_setperm import Ckpt
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ck")
            open(path, "w").close()                                  # created, then a crash before the header
            c = Ckpt(path, self.SIG, plant_seed=9)
            self.assertEqual(c.load(), (9, {}))
            c.done(0, 1, 1)
            c.close()
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), f"SIG {self.SIG}\nPLANT 9\ndone 0 1 1 ok\n")
            c = Ckpt(path, self.SIG, plant_seed=None)
            self.assertEqual(c.load(), (9, {0: (1, 1)}))
            c.close()

    def test_finished_blocks_without_a_plant_line_are_refused(self):
        from gpu_setperm import Ckpt
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ck")
            with open(path, "w") as f:
                f.write(f"SIG {self.SIG}\ndone 0 10 5 ok\n")
            for seed in (None, 7):
                with self.assertRaises(SystemExit):
                    Ckpt(path, self.SIG, plant_seed=seed).load()
            with open(path, "w") as f:                              # a header without PLANT and nothing done yet
                f.write(f"SIG {self.SIG}\n")
            c = Ckpt(path, self.SIG, plant_seed=4)
            self.assertEqual(c.load(), (4, {}))
            c.close()
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), f"SIG {self.SIG}\nPLANT 4\n")


def _run(text, *, prize, checkpoint=None, probe=1e9, **kw):
    """(space, result). The temp ledger / FOUND paths are on result.ledger_path / result.found_path. `probe` is the
    rate the (patched) rate probe returns - run_search takes no assumed rate outside a dry run; probe=None runs the
    REAL probe on the GPU."""
    import gpu_setperm
    from gpu_setperm import run_search
    from sp_space import Space
    sp = Space(cfg_from_text(text))
    d = kw.pop("workdir", None) or tempfile.mkdtemp()
    ledger, found = os.path.join(d, "runs.jsonl"), os.path.join(d, "FOUND.txt")
    with contextlib.ExitStack() as stack:
        if probe is not None:
            stack.enter_context(mock.patch.object(gpu_setperm, "probe_rate", lambda space, gpu, log: probe))
        r = run_search(sp, prize_addr=prize, checkpoint=checkpoint, plant_seed=kw.pop("plant_seed", 11),
                       log=kw.pop("log", QUIET), ledger=ledger, found_path=found,
                       signoff=kw.pop("signoff", SIGNOFF), **kw)
    r.ledger_path, r.found_path = ledger, found
    return sp, r


def _addr(sp, idx, pp_i, path_i, form):
    m = " ".join(sp.decode(idx))
    return address_of_hash160(sp_ref.hash160_forms(m, sp.passes[pp_i], [sp.paths[path_i]], sp.scheme)[form][2]), m


def _combo(i, sp):
    """A (passphrase, path, form) for the i-th survivor that cycles through every combination."""
    npass, npaths = len(sp.passes), len(sp.paths)
    return i % npass, (i // npass) % npaths, (i // (npass * npaths)) % 2


def _ledger(r):
    if not os.path.exists(r.ledger_path):
        return []
    with open(r.ledger_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class TrustModelNoGpu(unittest.TestCase):
    """The safety properties that must hold with no GPU at all."""

    def test_dry_run_and_refused_never_construct_an_engine_or_touch_files(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space.load(E4)

        def boom(*a, **k):
            raise AssertionError("an Engine / rate probe was built on a path that must not touch the GPU")
        with tempfile.TemporaryDirectory() as d:
            ck, led, fnd = (os.path.join(d, n) for n in ("ck", "runs.jsonl", "FOUND.txt"))
            common = dict(log=QUIET, ledger=led, found_path=fnd, checkpoint=ck)
            rnd = address_of_hash160(os.urandom(20))
            with mock.patch.object(gpu_setperm.Engine, "__init__", boom), mock.patch.object(gpu_setperm, "probe_rate", boom):
                r = gpu_setperm.run_search(sp, prize_addr=rnd, dry_run=True, assume_rate=300_000, **common)
                self.assertEqual(r.status, "DRY_RUN")
                r = gpu_setperm.run_search(sp, prize_addr=rnd, dry_run=True, **common)      # default assumed rate, no probe
                self.assertEqual((r.status, r.exit_code), ("DRY_RUN", 0))
                r = gpu_setperm.run_search(sp, dry_run=True, **common)                      # the real prize as default: dry-run only
                self.assertEqual((r.status, r.exit_code), ("DRY_RUN", 0))
                r = gpu_setperm.run_search(sp, prize_addr=rnd, dry_run=True, assume_rate=300_000, max_hours=0.1, **common)
                self.assertEqual((r.status, r.exit_code), ("REFUSED", 3))
            with mock.patch.object(gpu_setperm.Engine, "__init__", boom), \
                    mock.patch.object(gpu_setperm, "probe_rate", lambda space, gpu, log: 300_000):
                r = gpu_setperm.run_search(sp, prize_addr=rnd, max_hours=0.1, gpu=object(), signoff=SIGNOFF,
                                           **common)                                          # NOT a dry run
                self.assertEqual((r.status, r.exit_code), ("REFUSED", 3))
            self.assertEqual(os.listdir(d), [])            # no checkpoint, ledger or FOUND file was created

    def test_the_prize_address_appears_only_as_run_searchs_default(self):
        """Static proof that no other code path can read the prize: TARGET is used exactly once, as the
        default of run_search(prize_addr=...), and the address literal is not in the module."""
        import gpu_setperm
        with open(gpu_setperm.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        uses = [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "TARGET"]
        self.assertEqual(len(uses), 1)
        rs = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_search")
        kw = dict(zip([a.arg for a in rs.args.kwonlyargs], rs.args.kw_defaults))
        self.assertIs(kw["prize_addr"], uses[0])
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute):
                self.assertNotEqual(n.attr, "TARGET", "template_solve.TARGET must not be read anywhere")
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                self.assertNotIn("1KfZ", n.value)
        self.assertFalse(any(isinstance(n, ast.Name) and n.id == "TARGET" for n in ast.walk(
            next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "bench"))))

    def test_gpu_setperm_imports_without_opencl(self):
        """The CPU-only paths (dry run, guard, checkpoint) must not need pyopencl to import."""
        import subprocess
        import sys
        code = "import sys; sys.modules['pyopencl'] = None; import gpu_setperm; assert 'sp_gpu' not in sys.modules"
        p = subprocess.run([sys.executable, "-c", code], cwd=HERE, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])

    def test_the_cli_wires_dry_run_max_hours_and_force(self):
        """main() as the operator runs it, with OpenCL blocked: a regression that dropped --dry-run fails at
        import instead of touching the GPU, and nothing (not even the checkpoint) is created."""
        import subprocess
        import sys
        code = "import sys; sys.modules['pyopencl'] = None; import gpu_setperm; gpu_setperm.main()"
        with tempfile.TemporaryDirectory() as d:
            ck = os.path.join(d, "ck")

            def cli(*args):
                return subprocess.run([sys.executable, "-c", code, E4, "--dry-run",
                                       "--checkpoint", ck, *args], cwd=HERE, capture_output=True, text=True,
                                      timeout=300).returncode
            self.assertEqual(cli(), 0)                                   # ~0.31 h at the placeholder rate
            self.assertEqual(cli("--max-hours", "0.1"), 3)
            self.assertEqual(cli("--max-hours", "0.1", "--force"), 0)
            self.assertEqual(cli("--assume-rate", "1e6"), 0)             # --assume-rate is fine WITH --dry-run
            # usage errors exit 64, never 2 (FOUND owns 2): argparse's own errors and the extra rules
            for args in (("--bench",), ("--max-hours", "nan"), ("--assume-rate", "0"), ("--bogus",)):
                self.assertEqual(cli(*args), 64, args)
            self.assertEqual(os.listdir(d), [])


class RailsNoGpu(unittest.TestCase):
    """Guard hardening: every rate / hour limit is a finite positive number, --assume-rate only in a dry run,
    usage errors never exit 2, an incomplete run never exits 0."""

    BAD = (float("nan"), float("inf"), float("-inf"), 0, 0.0, -1, -1e-9)

    def test_run_search_refuses_non_finite_or_non_positive_rate_and_hours(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space.load(E4)

        def boom(*a, **k):
            raise AssertionError("must be refused before any engine or probe")
        with tempfile.TemporaryDirectory() as d:
            common = dict(prize_addr=address_of_hash160(os.urandom(20)), log=QUIET, checkpoint=os.path.join(d, "ck"),
                          ledger=os.path.join(d, "l.jsonl"), found_path=os.path.join(d, "F.txt"))
            with mock.patch.object(gpu_setperm.Engine, "__init__", boom), mock.patch.object(gpu_setperm, "probe_rate", boom):
                for v in self.BAD:
                    for kw in (dict(assume_rate=v, dry_run=True), dict(assume_rate=v), dict(max_hours=v, dry_run=True),
                               dict(max_hours=v, assume_rate=300_000), dict(max_hours=v, assume_rate=300_000, force=True)):
                        with self.assertRaises(ValueError, msg=(v, kw)):
                            gpu_setperm.run_search(sp, **common, **kw)
            self.assertEqual(os.listdir(d), [])

    def test_parse_args_rules(self):
        from gpu_setperm import parse_args
        refused = [[E4, "--assume-rate", "5e5"],                                   # a real run measures its own rate
                   [E4, "--assume-rate", "5e5", "--checkpoint", "x.ck", "--force"],
                   [E4, "--bench", "--assume-rate", "5e5"],
                   [E4, "--dry-run", "--bench"],
                   [], [E4, "--bogus"], [E4, "--dry-run", "--max-hours", "abc"], [E4, "extra", "--dry-run"]]
        for bad in ("nan", "inf", "-inf", "0", "-3", "1e999"):
            refused += [[E4, "--dry-run", "--assume-rate", bad], [E4, "--dry-run", "--max-hours", bad],
                        [E4, "--max-hours", bad, "--checkpoint", "x.ck"]]
        with contextlib.redirect_stderr(io.StringIO()) as err:
            for argv in refused:
                with self.assertRaises(SystemExit, msg=argv) as cm:
                    parse_args(argv)
                self.assertEqual(cm.exception.code, 64, argv)
        self.assertIn("--assume-rate", err.getvalue())
        a = parse_args([E4, "--dry-run", "--assume-rate", "5e5", "--max-hours", "2"])
        self.assertEqual((a.config, a.dry_run, a.assume_rate, a.max_hours), (E4, True, 5e5, 2.0))
        a = parse_args([E4, "--bench"])
        self.assertTrue(a.bench)
        a = parse_args([E4, "--checkpoint", "x.ck", "--max-hours", "3", "--force", "--plant-seed", "5"])
        self.assertEqual((a.checkpoint, a.max_hours, a.force, a.plant_seed, a.assume_rate), ("x.ck", 3.0, True, 5, None))

    def test_main_refuses_before_loading_or_running_anything(self):
        import gpu_setperm
        import sp_space

        def boom(*a, **k):
            raise AssertionError("main() went past argument checking")
        with mock.patch.object(gpu_setperm, "run_search", boom), mock.patch.object(gpu_setperm, "bench", boom), \
                mock.patch.object(sp_space.Space, "load", boom), contextlib.redirect_stderr(io.StringIO()):
            for argv in ([E4, "--assume-rate", "5e5"], [E4, "--dry-run", "--bench"], [E4, "--max-hours", "nan"]):
                with self.assertRaises(SystemExit) as cm:
                    gpu_setperm.main(argv)
                self.assertEqual(cm.exception.code, 64, argv)

    def test_stopped_is_exit_5_never_0(self):
        _, r, _, blocks = _fake_search("D_electrum_12_wordlist", _rand(), ub=5, max_blocks=2)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("STOPPED", 5, 2))
        self.assertEqual(len(blocks), 2)

    def test_more_than_max_passes_is_refused_even_in_a_dry_run(self):
        import gpu_setperm
        from sp_space import Space, SpaceError
        text = dict(small_config_texts())["D_electrum_12_wordlist"]
        at_limit = Space(cfg_from_text(text + "".join(f"PASS p{i}\n" for i in range(4095))))   # + '' = 4096
        self.assertEqual(len(at_limit.passes), 4096)
        r = gpu_setperm.run_search(at_limit, prize_addr=_rand(), dry_run=True, assume_rate=1e9, log=QUIET)
        self.assertEqual(r.status, "DRY_RUN")
        over = Space(cfg_from_text(text + "".join(f"PASS p{i}\n" for i in range(4096))))
        self.assertEqual(len(over.passes), 4097)
        with self.assertRaises(SpaceError) as cm:
            gpu_setperm.run_search(over, prize_addr=_rand(), dry_run=True, assume_rate=1e9, log=QUIET)
        self.assertIn("4096", str(cm.exception))
        with self.assertRaises(gpu_setperm.EngineError):                    # refused before any OpenCL import
            gpu_setperm.Engine(over, [os.urandom(20)], log=QUIET)

    def test_main_turns_a_refused_config_into_cannot_run_and_exit_1(self):
        """A config the engine refuses (here: too many passphrases, raised by run_search as a SpaceError) is a
        clean 'cannot run' exit 1 through sys.exit(message), never a traceback and never a result code."""
        import gpu_setperm
        text = dict(small_config_texts())["D_electrum_12_wordlist"] + "".join(f"PASS p{i}\n" for i in range(4096))
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "many_passes.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(text)
            with self.assertRaises(SystemExit) as cm:
                gpu_setperm.main([conf, "--dry-run"])
        self.assertIsInstance(cm.exception.code, str)                        # sys.exit(str): status 1
        self.assertIn("cannot run", cm.exception.code)
        self.assertIn("4096", cm.exception.code)


def _rand():
    return address_of_hash160(os.urandom(20))


def _prize_slots(nplants, b, seed=11, copies=1):
    n = nplants + copies
    return [(b + seed + k * (n // copies)) % n for k in range(copies)]


def _rotated(ph, plant_h160s, b, seed=11, copies=1):
    """The target list of block b as the run must build it: `copies` of the prize at the slots
    (b + seed + k * (n // copies)) % n, n = plants + copies, and the plants in their order at the other slots.
    Written out here independently of gpu_setperm.block_targets."""
    hs = list(plant_h160s)
    slots = _prize_slots(len(hs), b, seed, copies)
    return [ph if t in slots else hs.pop(0) for t in range(len(plant_h160s) + copies)]


def _fake_search(name=None, prize=None, *, text=None, edit=None, post=None, ub=None, ck_lines=None, ck=None,
                 max_blocks=None, ledger=None, log=None, make=None, selftest=None, size=None, spot=None, nplants=1,
                 copies=1, signoff=SIGNOFF):
    """run_search with the GPU Engine replaced by an exact CPU stand-in (no GPU, no OpenCL): every block is
    reported fully scanned (with the exact K1 index sum) and that block's own plant is reported at its planted
    place; `edit(hits, lo, hi)` corrupts the hits (in the run's own meaning: tgt_i 0 = prize, 1 + j = the block's
    plant j - the stand-in reports them at whatever K3 slot that target really had) and `post(res, lo, hi)` the rest
    of the block result. `nplants` is the number of plants per block and `copies` the number of K3 slots that
    hold the prize (PLANTS_PER_BLOCK and PRIZE_COPIES are patched to them; the defaults 1 and 1 keep the blocks of
    [prize, plant] most tests here are written against). `make` replaces the maker of plant 0; `selftest(space, engine, seed)` the combo self-test (default: a
    recorder that passes); `size(lo, hi)` -> (tested, valid) fills the survivor counts of a block (default: an
    honest engine's - tested == scanned, valid = tested x the scheme's pass rate, 0 in a block without a plant);
    `spot(lo, hi, seed)` replaces the window audit (default: a recorder that passes).
    `ck_lines` pre-writes a checkpoint (SIG, PLANT 11, then those lines); `ck` names a checkpoint path.
    Also checks that every block was scanned against exactly its own targets in the rotated order (_rotated: the
    plants in order with the prize at slot (block + plant seed) % (plants + 1); [prize] alone where the CPU found
    no plant).
    Returns (space, result, {block: plant 0}, [(lo, hi) of every scanned block]); the result also carries
    .selftest_calls, .given (the target lists), .spot_calls (lo, hi, seed of every window audit) and .plist
    ({block: [every plant of the block]})."""
    import gpu_setperm
    from sp_k1_enum import expected_idx_sum
    from sp_space import Space
    sp = Space(cfg_from_text(text or dict(small_config_texts())[name]))
    ubf = (lambda space, target_deriv=4e6: ub) if ub else gpu_setperm.plan_block_units
    block = ubf(sp) * sp.per_unit
    plants, plist, blocks, given, st_calls, spots = {}, {}, [], [], [], []
    rate = sp.estimate()["valid_rate"]
    real_make = make or gpu_setperm.make_block_plant
    real_make_all = gpu_setperm.make_block_plants
    ph = hash160_of_address(prize)

    def spy_make(space, seed, b, lo, hi):
        plants[b] = real_make(space, seed, b, lo, hi)
        return plants[b]

    def spy_make_all(space, seed, b, lo, hi, count=None):
        plist[b] = real_make_all(space, seed, b, lo, hi, count)
        return plist[b]

    def spy_selftest(space, engine, seed=0):
        st_calls.append((space, engine, seed))
        if selftest:
            return selftest(space, engine, seed)
        return SimpleNamespace(survivors=[0, 1], combos=2, known_answers=4, rounds=2, sweep_answers=6, sweep_rounds=6)

    class StandIn:
        hit_cap = 256

        def __init__(self, space, targets, **_):
            self.targets, self.partial = list(targets), None

        def set_targets(self, targets):
            self.targets = list(targets)

        def scan_block(self, lo, hi, heartbeat=None):
            blocks.append((lo, hi))
            given.append(list(self.targets))
            ps = plist[lo // block]
            res = self.partial = gpu_setperm.BlockResult()
            res.scanned = hi - lo
            res.idx_sum = expected_idx_sum(lo, hi - lo)
            if size:
                res.tested, res.valid = size(lo, hi)
            else:                                                       # an honest engine's counts
                res.tested = res.scanned
                res.valid = int(round(res.tested * rate)) if ps else 0
            res.processed = res.valid
            hits = [gpu_setperm.Hit(p.idx, p.pass_i, p.path_i, p.form, 1 + j) for j, p in enumerate(ps)]
            if edit:
                hits = edit(hits, lo, hi)
            logical = [ph] + [p.h160 for p in ps]                       # 0 = prize, 1 + j = plant j

            def raw(t):                                                 # the K3 slot(s) that target really has
                if isinstance(t, int) and not isinstance(t, bool) and 0 <= t < len(logical) \
                        and logical[t] in self.targets:
                    return [k for k, x in enumerate(self.targets) if x == logical[t]]
                return [t]                                              # a target that does not exist: as reported
            res.hits = [h._replace(tgt_i=k) for h in hits for k in raw(h.tgt_i)]   # the prize: once per copy
            if post:
                post(res, lo, hi)
            return res

        def k1_spot_check(self, lo, hi, seed, width=None):
            spots.append((lo, hi, seed))
            if spot:
                return spot(lo, hi, seed)
            return SimpleNamespace(window=(lo, hi - lo), survivors=0, checked=0, skipped=False)
    d = tempfile.mkdtemp()
    if ck_lines is not None:
        ck = os.path.join(d, "ck")
        with open(ck, "w", encoding="utf-8") as f:
            f.write(f"SIG {gpu_setperm.sig_gpu(sp, prize, ubf(sp))}\nPLANT 11\n" + "".join(x + "\n" for x in ck_lines))
    ledger = ledger or os.path.join(d, "runs.jsonl")
    with mock.patch.object(gpu_setperm, "Engine", StandIn), mock.patch.object(gpu_setperm, "make_block_plant", spy_make), \
            mock.patch.object(gpu_setperm, "make_block_plants", spy_make_all), \
            mock.patch.object(gpu_setperm, "PLANTS_PER_BLOCK", nplants), \
            mock.patch.object(gpu_setperm, "PRIZE_COPIES", copies), \
            mock.patch.object(gpu_setperm, "plan_block_units", ubf), \
            mock.patch.object(gpu_setperm, "combo_selftest", spy_selftest), \
            mock.patch.object(gpu_setperm, "probe_rate", lambda space, gpu, log: 1e9):
        r = gpu_setperm.run_search(sp, prize_addr=prize, checkpoint=ck, gpu=object(), plant_seed=11,
                                   max_blocks=max_blocks, log=log or QUIET, ledger=ledger,
                                   found_path=os.path.join(d, "FOUND.txt"), signoff=signoff)
    r.ledger_path, r.found_path = ledger, os.path.join(d, "FOUND.txt")
    r.selftest_calls, r.given, r.spot_calls, r.plist = st_calls, given, spots, plist
    for (lo, hi), t in zip(blocks, given):
        b = lo // block
        want = _rotated(ph, [p.h160 for p in plist[b]], b, copies=copies)
        assert t == want, f"block {b} (at {lo}) was not scanned for its targets in the rotated order"
    return sp, r, plants, blocks


class ControlsWithoutGpu(unittest.TestCase):
    """Each run_search / bench / Engine control, shown to fire on the one fault it exists for, against CPU
    stand-ins for the GPU (so these hold with no OpenCL at all)."""

    D = "D_electrum_12_wordlist"                                  # 24 units x 2048, 1 passphrase, 1 path

    def test_the_stand_in_gives_a_verified_no_match_with_a_plant_in_every_block(self):
        sp, r, plants, blocks = _fake_search(self.D, _rand(), ub=5)        # 24 units: 5+5+5+5+4
        self.assertEqual((r.status, r.exit_code, r.plants_ok), ("NO_MATCH", 0, True), r.detail)
        block = 5 * sp.per_unit
        self.assertEqual(blocks, [(lo, min(sp.total, lo + block)) for lo in range(0, sp.total, block)])
        self.assertEqual(len(blocks), 5)
        self.assertLess(blocks[-1][1] - blocks[-1][0], block)              # the last block really is partial
        self.assertEqual(sorted(plants), [0, 1, 2, 3, 4])
        for b, (lo, hi) in enumerate(blocks):
            self.assertTrue(lo <= plants[b].idx < hi, b)                    # each block's plant lies inside it
        self.assertEqual(len({p.idx for p in plants.values()}), 5)

    def test_every_block_is_void_when_its_own_plant_is_missing(self):
        """Not only the first and last: a block anywhere whose plant does not come back voids the run there,
        and nothing after it is scanned or checkpointed."""
        from sp_space import Space
        block = 5 * Space(cfg_from_text(dict(small_config_texts())[self.D])).per_unit
        for k in range(5):
            _, r, _, blocks = _fake_search(self.D, _rand(), ub=5,
                                           edit=lambda hits, lo, hi, k=k: [] if lo == k * block else hits)
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, k), k)
            self.assertIn(f"block {k}", r.detail)
            self.assertIn("was not found", r.detail)
            self.assertEqual(len(blocks), k + 1)

    def test_a_block_is_recorded_done_only_after_its_controls_held(self):
        """The checkpoint of a run that went VOID in block k holds exactly the 'done' lines of blocks 0..k-1."""
        from sp_space import Space
        block = 5 * Space(cfg_from_text(dict(small_config_texts())[self.D])).per_unit
        for k in (0, 2, 4):
            ck = os.path.join(tempfile.mkdtemp(), "ck")
            _, r, _, _ = _fake_search(self.D, _rand(), ub=5, ck=ck,
                                      edit=lambda hits, lo, hi, k=k: [] if lo == k * block else hits)
            self.assertEqual((r.status, r.blocks_done), ("VOID", k))
            with open(ck, encoding="utf-8") as f:
                done = [line.split()[1] for line in f if line.startswith("done ")]
            self.assertEqual(done, [str(b) for b in range(k)], k)

    def test_an_old_plant_line_is_tolerated_but_every_block_must_find_its_own_plant(self):
        sp, r, _, blocks = _fake_search(self.D, _rand(), ck_lines=["plant 0 ok"],
                                        edit=lambda hits, lo, hi: [h for h in hits if h.tgt_i != 1])
        self.assertEqual(blocks, [(0, sp.total)])
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIn("was not found", r.detail)
        _, r, _, _ = _fake_search(self.D, _rand(), ck_lines=["plant 0 ok", "plant 1 ok", "plant 2 ok"])
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)

    def test_done_lines_are_the_record_and_must_cover_exactly_this_space(self):
        """A 'done' line is written only after its block's plant was found, so resumed blocks are trusted; but a
        block number outside the space, or a missing one, is VOID."""
        rnd = _rand()
        _, r, _, blocks = _fake_search(self.D, rnd, ck_lines=["done 0 0 0 ok", "done 7 0 0 ok"])
        self.assertEqual(blocks, [])
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIn("cover", r.detail)
        _, r, _, blocks = _fake_search(self.D, rnd, ub=5, ck_lines=[f"done {b} {10 + b} {b} ok" for b in range(5)])
        self.assertEqual(blocks, [])
        self.assertEqual((r.status, r.exit_code, r.tested, r.valid), ("NO_MATCH", 0, 60, 10), r.detail)
        _, r, _, blocks = _fake_search(self.D, rnd, ub=5, ck_lines=[f"done {b} 1 1 ok" for b in (0, 1, 2, 3, 5)])
        self.assertEqual(len(blocks), 1)                                  # block 4 runs ...
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))           # ... but block 5 does not exist
        self.assertIn("cover", r.detail)

    def test_a_block_without_any_candidate_needs_zero_valid_and_no_plant_report(self):
        import gpu_setperm
        real = gpu_setperm.make_block_plant

        def none_in_block_1(space, seed, b, lo, hi):
            return None if b == 1 else real(space, seed, b, lo, hi)
        _, r, plants, blocks = _fake_search(self.D, _rand(), ub=5, make=none_in_block_1)
        self.assertIsNone(plants[1])
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)

        def valid_in_1(res, lo, hi):
            if lo == blocks[1][0]:
                res.valid = res.processed = 3
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, make=none_in_block_1, post=valid_in_1)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 1))
        self.assertIn("block 1", r.detail)
        H = gpu_setperm.Hit

        def stray_in_1(hits, lo, hi):
            return hits + [H(lo + 5, 0, 0, 0, 1)] if lo == blocks[1][0] else hits
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, make=none_in_block_1, edit=stray_in_1)
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIn("does not exist", r.detail)

    def test_a_block_whose_plant_cannot_be_made_is_void(self):
        from sp_space import SpaceError

        def fail(space, seed, b, lo, hi):
            raise SpaceError("no filter-passing candidate found to plant")
        _, r, _, blocks = _fake_search(self.D, _rand(), make=fail)
        self.assertEqual((r.status, r.exit_code, blocks), ("VOID", 4, []))
        self.assertIn("block 0", r.detail)

    def test_a_plant_reported_with_the_wrong_passphrase_path_or_key_form_is_void(self):
        n = {"pass_i": 3, "path_i": 2, "form": 2}                          # A: passes '', moon, MOON; 2 paths
        for field, mod in n.items():
            def wrong(hits, lo, hi, field=field, mod=mod):
                return [h._replace(**{field: (getattr(h, field) + 1) % mod}) if h.tgt_i == 1 else h for h in hits]
            _, r, _, _ = _fake_search("A_bip39_12_r3k1", _rand(), edit=wrong)
            self.assertEqual((r.status, r.exit_code), ("VOID", 4), field)
            self.assertIn("does not confirm", r.detail, field)

    def test_a_malformed_report_on_the_plant_target_is_void_never_a_traceback(self):
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["A_bip39_12_r3k1"]))
        for field, value in (("idx", sp.total), ("idx", sp.total + 5), ("idx", -1), ("idx", 2 ** 64 - 1),
                             ("pass_i", 3), ("pass_i", -1), ("pass_i", -3), ("path_i", 2), ("path_i", -1),
                             ("path_i", -2), ("form", 2), ("form", -1), ("form", -2), ("idx", "7"), ("form", None)):
            def bad(hits, lo, hi, field=field, value=value):
                return hits + [hits[0]._replace(**{field: value})]
            _, r, _, _ = _fake_search("A_bip39_12_r3k1", _rand(), edit=bad)
            self.assertEqual((r.status, r.exit_code), ("VOID", 4), (field, value))
            self.assertIn("does not confirm", r.detail, (field, value))

    def test_a_report_on_the_plant_outside_its_block_is_void(self):
        """A true twin of the plant can only come from this block's own survivors."""
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["J_bip39_12_dups"]))
        _, _, surv = sp_ref.ref_survivors(sp.cfg)
        twins = {}
        for i in surv:
            twins.setdefault(" ".join(sp.decode(i)), []).append(i)
        pairs = [v for v in twins.values() if len(v) >= 2 and v[-1] // (3 * sp.per_unit) != v[0] // (3 * sp.per_unit)]
        self.assertTrue(pairs)                                             # twins that lie in different blocks
        import gpu_setperm
        real = gpu_setperm.make_block_plant
        a, z = pairs[0][0], pairs[0][-1]
        blk_a = a // (3 * sp.per_unit)

        def plant_at_a(space, seed, b, lo, hi):
            p = real(space, seed, b, lo, hi)
            if b != blk_a:
                return p
            h = sp_ref.hash160_forms(" ".join(space.decode(a)), space.passes[0], [space.paths[0]], space.scheme)[0][2]
            return p._replace(idx=a, pass_i=0, path_i=0, form=0, h160=h)

        def twin_elsewhere(hits, lo, hi):
            return hits + [h._replace(idx=z) for h in hits if h.tgt_i == 1 and h.idx == a]
        _, r, _, _ = _fake_search("J_bip39_12_dups", _rand(), ub=3, make=plant_at_a)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        _, r, _, _ = _fake_search("J_bip39_12_dups", _rand(), ub=3, make=plant_at_a, edit=twin_elsewhere)
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIn("does not confirm", r.detail)

    def test_cpu_confirms_is_false_for_any_malformed_hit_and_never_raises(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["A_bip39_12_r3k1"]))
        idx = next(i for i in range(sp.total) if sp.candidate_ok(i))
        addr, _ = _addr(sp, idx, 1, 1, 0)
        target = hash160_of_address(addr)
        good = gpu_setperm.Hit(idx, 1, 1, 0, 0)
        self.assertTrue(gpu_setperm._cpu_confirms(sp, good, target))
        # -2 / -1 alias the right passphrase / path in Python: only an explicit range check refuses them
        self.assertEqual((sp.passes[-2], sp.paths_txt[-1]), (sp.passes[1], sp.paths_txt[1]))
        for field, value in (("idx", sp.total), ("idx", -1), ("idx", -sp.total), ("idx", 1.5), ("idx", None),
                             ("idx", float(idx)), ("pass_i", 3), ("pass_i", -2), ("pass_i", -1), ("path_i", 2),
                             ("path_i", -1), ("form", 2), ("form", -1), ("form", "0"), ("form", None), ("form", False),
                             ("pass_i", True)):
            self.assertIs(gpu_setperm._cpu_confirms(sp, good._replace(**{field: value}), target), False, (field, value))
        self.assertIs(gpu_setperm._cpu_confirms(sp, good, os.urandom(20)), False)
        import numpy as np                                                  # integer types other than int still work
        self.assertIs(gpu_setperm._cpu_confirms(sp, gpu_setperm.Hit(np.uint64(idx), np.uint32(1), np.int64(1),
                                                                     np.uint32(0), 0), target), True)

        def raises(*a, **k):
            raise ValueError("boom")
        with mock.patch.object(sp_ref, "confirm", raises):
            self.assertIs(gpu_setperm._cpu_confirms(sp, good, target), False)
        with mock.patch.object(sp, "decode", raises):
            self.assertIs(gpu_setperm._cpu_confirms(sp, good, target), False)

    def test_a_stray_report_on_the_plant_target_or_a_target_that_does_not_exist_is_void(self):
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[self.D]))

        def stray(hits, lo, hi):
            p0 = next(h for h in hits if h.tgt_i == 1)
            other = next(i for i in range(lo, hi) if sp.candidate_ok(i) and sp.decode(i) != sp.decode(p0.idx))
            return hits + [p0._replace(idx=other)]                         # not a twin: the CPU cannot confirm it
        _, r, _, _ = _fake_search(self.D, _rand(), edit=stray)
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIn("does not confirm", r.detail)
        for t in (2, 3, -1):                                               # targets are [prize, plant]: 0 and 1 only
            _, r, _, _ = _fake_search(self.D, _rand(), edit=lambda hits, lo, hi, t=t: hits + [hits[0]._replace(tgt_i=t)])
            self.assertEqual((r.status, r.exit_code), ("VOID", 4), t)
            self.assertIn("does not exist", r.detail)

    def _good_bad(self, n_bad=1):
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[self.D]))
        ok = [i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i)]
        good = ok[0]
        bad = [i for i in ok[1:] if sp.decode(i) != sp.decode(good)][:n_bad]
        return sp, good, bad

    def test_found_names_the_confirmed_hit_even_when_an_unconfirmed_report_comes_first(self):
        import gpu_setperm
        sp, good, (bad,) = self._good_bad()
        addr, m = _addr(sp, good, 0, 0, 0)
        H = gpu_setperm.Hit
        _, r, _, _ = _fake_search(self.D, addr, edit=lambda hits, lo, hi: [H(bad, 0, 0, 0, 0, at=(1, 2, 3, 4))] + hits
                                  + [H(good, 0, 0, 0, 0)])
        self.assertEqual((r.status, r.exit_code), ("FOUND", 2))
        self.assertEqual((r.found["mnemonic"], r.found["index"], r.found["indices"]), (m, good, [good]))
        self.assertEqual(r.found.get("unconfirmed_gpu_reports"), 1)
        rec = _ledger(r)
        self.assertEqual([x["status"] for x in rec], ["START", "FOUND"])
        self.assertEqual([(u["idx"], u["cpu_confirmed"], u["chunk_start"], u["batch_count"]) for u in rec[1]["prize_hits"]],
                         [(bad, False, 1, 4), (good, True, None, None)])      # ALL prize reports of that block
        self.assertNotIn(m, json.dumps(rec))
        _, r, _, _ = _fake_search(self.D, addr, edit=lambda hits, lo, hi: hits + [H(good, 0, 0, 0, 0)])
        self.assertEqual((r.status, r.found["index"]), ("FOUND", good))
        self.assertNotIn("prize_hits", _ledger(r)[1])                          # nothing unconfirmed: nothing extra

    def test_a_malformed_prize_report_next_to_a_genuine_one_still_gives_found(self):
        import gpu_setperm
        sp, good, _ = self._good_bad()
        addr, m = _addr(sp, good, 0, 0, 0)
        H = gpu_setperm.Hit
        junk = [H(sp.total + 5, 0, 0, 0, 0), H(-1, 0, 0, 0, 0), H(good, 5, 0, 0, 0), H(good, -1, 0, 0, 0),
                H(good, 0, 9, 0, 0), H(good, 0, 0, 2, 0), H(good, 0, 0, None, 0), H("x", 0, 0, 0, 0)]
        _, r, _, _ = _fake_search(self.D, addr, edit=lambda hits, lo, hi: junk + hits + [H(good, 0, 0, 0, 0)])
        self.assertEqual((r.status, r.exit_code), ("FOUND", 2), r.detail)
        self.assertEqual((r.found["mnemonic"], r.found["index"]), (m, good))
        self.assertEqual(r.found["unconfirmed_gpu_reports"], len(junk))
        _, r, _, _ = _fake_search(self.D, addr, edit=lambda hits, lo, hi: [H(good, 0, 0, 0, 0.0)] + hits)
        self.assertEqual((r.status, r.found["index"]), ("FOUND", good))                     # an odd tgt_i type: still a key
        _, r, _, _ = _fake_search(self.D, addr, edit=lambda hits, lo, hi: junk + hits)       # junk only: VOID
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIsNone(r.found)
        self.assertFalse(os.path.exists(r.found_path))

    def test_unconfirmed_prize_reports_are_logged_with_their_block_chunk_and_batch(self):
        import gpu_setperm
        sp, _, bad = self._good_bad(2)
        H = gpu_setperm.Hit
        reports = [H(bad[0], 0, 0, 0, 0, at=(24576, 4096, 3, 5)), H(bad[1], 0, 0, 1, 0, at=(28672, 4096, 0, 2))]
        lines = []
        _, r, _, _ = _fake_search(self.D, _rand(), edit=lambda hits, lo, hi: hits + reports, log=lines.append)
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIn("does not confirm", r.detail)
        text = "\n".join(map(str, lines))
        for h in reports:
            for key, v in (("idx", h.idx), ("chunk_start", h.at[0]), ("chunk_count", h.at[1]), ("batch_off", h.at[2]),
                           ("batch_count", h.at[3]), ("form", h.form), ("block", 0)):
                self.assertIn(f'"{key}": {v}', text, (h, key))
        rec = [x for x in _ledger(r) if x["status"] == "VOID"]
        self.assertEqual(len(rec), 1)
        self.assertEqual([(p["block"], p["idx"], p["pass_i"], p["path_i"], p["form"], p["tgt_i"], p["chunk_start"],
                           p["chunk_count"], p["batch_off"], p["batch_count"], p["cpu_confirmed"])
                          for p in rec[0]["prize_hits"]],
                         [(0, bad[0], 0, 0, 0, 0, 24576, 4096, 3, 5, False), (0, bad[1], 0, 0, 1, 0, 28672, 4096, 0, 2, False)])
        with open(r.ledger_path, encoding="utf-8") as f:
            ledger_text = f.read()
        for i in bad:                                                       # never a mnemonic
            m = " ".join(sp.decode(i))
            self.assertNotIn(m, text)
            self.assertNotIn(m, ledger_text)

    def test_found_survives_an_unwritable_ledger_and_the_found_file_is_fsynced(self):
        import gpu_setperm
        sp, good, _ = self._good_bad()
        addr, m = _addr(sp, good, 0, 0, 0)
        d = tempfile.mkdtemp()
        blocker = os.path.join(d, "not_a_dir")
        with open(blocker, "w") as f:
            f.write("x")
        synced, real_fsync = [], os.fsync

        def spy(fd):
            synced.append(os.fstat(fd).st_ino)
            return real_fsync(fd)
        lines = []
        with mock.patch.object(gpu_setperm.os, "fsync", spy):
            _, r, _, _ = _fake_search(self.D, addr, edit=lambda hits, lo, hi: hits + [gpu_setperm.Hit(good, 0, 0, 0, 0)],
                                      ledger=os.path.join(blocker, "runs.jsonl"), log=lines.append)
        self.assertEqual((r.status, r.exit_code), ("FOUND", 2))
        text = "\n".join(map(str, lines))
        self.assertIn(m, text)
        self.assertIn("COULD NOT WRITE", text)
        self.assertIn("runs.jsonl", text)
        with open(r.found_path, encoding="utf-8") as f:
            self.assertIn(m, f.read())
        self.assertIn(os.stat(r.found_path).st_ino, synced)

    def test_any_exception_in_a_block_is_void_and_keyboard_interrupt_propagates(self):
        import gpu_setperm
        for exc in (ZeroDivisionError("x"), KeyError("k"), ValueError("v"), AttributeError("a"), MemoryError()):
            def post(res, lo, hi, exc=exc):
                raise exc
            _, r, _, _ = _fake_search(self.D, _rand(), post=post)
            self.assertEqual((r.status, r.exit_code), ("VOID", 4), exc)
            self.assertIn(type(exc).__name__, r.detail)
            self.assertIn("block 0", r.detail)
        closed = []
        real_close = gpu_setperm.Ckpt.close

        def spy_close(self):
            real_close(self)
            closed.append(self.f)

        def interrupt(res, lo, hi):
            raise KeyboardInterrupt
        with mock.patch.object(gpu_setperm.Ckpt, "close", spy_close):
            with self.assertRaises(KeyboardInterrupt):
                _fake_search(self.D, _rand(), post=interrupt, ck_lines=[])
        self.assertEqual(closed, [None])                                    # the checkpoint was closed

    def test_bench_and_the_rate_probe_trust_only_the_plant_found_at_its_place(self):
        """The probe's rate is believed only if its planted seed came back at its exact place."""
        import time
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[self.D]))
        box = {}
        real_make = gpu_setperm.make_block_plant

        def spy_make(space, seed, b, lo, hi):
            box["plant"] = real_make(space, seed, b, lo, hi)
            box["span"] = (lo, hi)
            return box["plant"]

        def stand_in(edit):
            class StandIn:
                def __init__(self, space, targets, **_):
                    box["targets"] = list(targets)

                def scan_block(self, lo, hi, heartbeat=None):
                    time.sleep(0.002)
                    res = gpu_setperm.BlockResult()
                    res.scanned = res.tested = hi - lo
                    res.valid = res.processed = 1
                    p = box["plant"]
                    res.hits = edit([gpu_setperm.Hit(p.idx, p.pass_i, p.path_i, p.form, 1)])
                    return res
            return StandIn
        cases = [("exact", lambda hs: hs, True), ("missing", lambda hs: [], False),
                 ("wrong form", lambda hs: [h._replace(form=1 - h.form) for h in hs], False),
                 ("wrong index", lambda hs: [h._replace(idx=h.idx + 1) for h in hs], False),
                 ("wrong target", lambda hs: [h._replace(tgt_i=0) for h in hs], False)]
        for name, edit, expect in cases:
            with mock.patch.object(gpu_setperm, "make_block_plant", spy_make), \
                    mock.patch.object(gpu_setperm, "Engine", stand_in(edit)):
                b = gpu_setperm.bench(sp, log=QUIET)
                self.assertIs(b["plant_found"], expect, name)
                self.assertEqual(box["span"], (0, b["span"]))                  # the plant lies inside the bench span
                self.assertEqual(box["targets"][1:], [box["plant"].h160])
                if expect:
                    self.assertGreater(gpu_setperm.probe_rate(sp, None, QUIET), 0)
                    b = gpu_setperm.bench(sp, log=QUIET, target_deriv=20)       # a span smaller than the space
                    self.assertEqual(b["span"], 20 * 256)
                    self.assertEqual(box["span"], (0, b["span"]))
                    self.assertLess(box["plant"].idx, b["span"])
                    self.assertTrue(b["plant_found"])
                else:
                    with self.assertRaises(gpu_setperm.EngineError, msg=name):
                        gpu_setperm.probe_rate(sp, None, QUIET)

    def test_bench_probe_and_cli_also_require_exact_coverage(self):
        """A planted seed found by a bench that scanned too much or too little, or lost a survivor, is not a rate
        to believe: coverage_exact is False, the probe refuses, and `--bench` exits 4 (VOID), not 0."""
        import time
        import gpu_setperm
        from sp_space import Space
        text = dict(small_config_texts())[self.D]
        sp = Space(cfg_from_text(text))
        box = {}
        real_make = gpu_setperm.make_block_plant

        def spy_make(space, seed, b, lo, hi):
            box["plant"] = real_make(space, seed, b, lo, hi)
            return box["plant"]

        def stand_in(scanned_delta, processed_delta):
            class StandIn:
                def __init__(self, space, targets, **_):
                    pass

                def scan_block(self, lo, hi, heartbeat=None):
                    time.sleep(0.002)
                    res = gpu_setperm.BlockResult()
                    res.scanned = res.tested = hi - lo + scanned_delta
                    res.valid, res.processed = 1, 1 + processed_delta
                    p = box["plant"]
                    res.hits = [gpu_setperm.Hit(p.idx, p.pass_i, p.path_i, p.form, 1)]
                    return res
            return StandIn
        cases = (("exact", 0, 0, True), ("short scan", -1, 0, False), ("extra scan", 1, 0, False),
                 ("lost survivor", 0, -1, False))
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "d.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(text)
            for name, sd, pd, exact in cases:
                with mock.patch.object(gpu_setperm, "make_block_plant", spy_make), \
                        mock.patch.object(gpu_setperm, "Engine", stand_in(sd, pd)):
                    b = gpu_setperm.bench(sp, log=QUIET)
                    self.assertIs(b["plant_found"], True, name)              # the plant itself always came back
                    self.assertIs(b["coverage_exact"], exact, name)
                    if exact:
                        self.assertGreater(gpu_setperm.probe_rate(sp, None, QUIET), 0)
                    else:
                        with self.assertRaises(gpu_setperm.EngineError, msg=name):
                            gpu_setperm.probe_rate(sp, None, QUIET)
                    with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as cm:
                        gpu_setperm.main([conf, "--bench"])
                    self.assertEqual(cm.exception.code, 0 if exact else 4, name)

    def test_a_slow_launch_shrinks_the_sizer_even_when_it_was_clipped(self):
        """A launch clipped by the block end (K1) or by the survivors left (K2) that was SLOW must still shrink
        the size - the next, unclipped launch would be slower still. A clipped launch that was fast must not
        collapse it. Fake kernels on a fake clock: no GPU."""
        import gpu_setperm
        clock = [0.0]

        def engine(npass, k1_secs, k2_secs, valid_of):
            def k1_run(start, count):
                clock[0] += k1_secs(count)
                return count, count, valid_of(count)

            def k2_launch(surv, off, nsurv):
                clock[0] += k2_secs(nsurv * npass)
            return _fake_clock_engine(npass, k1_run, k2_launch, gpu_setperm.Sizer(1000, hi=1 << 20),
                                      gpu_setperm.Sizer(400, hi=1 << 17), clock)
        with mock.patch.object(gpu_setperm, "time", SimpleNamespace(perf_counter=lambda: clock[0])), \
                mock.patch.object(gpu_setperm, "wait_cool", lambda *a, **k: None):
            # K1: a full 1000-candidate launch at 0.1 s (size -> 2000), then the 500-candidate block tail at 0.6 s
            eng = engine(1, lambda n: 0.1 if n == 1000 else 0.6, lambda n: 0.0, lambda n: 0)
            eng.scan_block(0, 1500)
            self.assertLessEqual(eng.s1.n, 250)
            # K1: a full launch at 0.1 s (size -> 2000), then a fast 10-candidate tail that says nothing about the rate
            eng = engine(1, lambda n: 0.1 if n == 1000 else 0.001, lambda n: 0.0, lambda n: 0)
            eng.scan_block(0, 1010)
            self.assertEqual(eng.s1.n, 2000)
            # K2, 2 passphrases: 200 survivors (400 threads) at 0.1 s (size -> 800), then the last 100 at 0.6 s
            eng = engine(2, lambda n: 0.0, lambda n: 0.1 if n == 400 else 0.6, lambda n: n)
            res = eng.scan_block(0, 300)
            self.assertEqual((res.valid, res.processed), (300, 300))
            self.assertLessEqual(eng.s2.n, 100)
            # K2: the same, but the last 100 survivors are fast: the size stays at 800 threads
            eng = engine(2, lambda n: 0.0, lambda n: 0.1 if n == 400 else 0.001, lambda n: n)
            eng.scan_block(0, 300)
            self.assertEqual(eng.s2.n, 800)
            # K1 survivors overflow the buffer by exactly one: the chunk is redone smaller, K2 never reads past
            # the stored survivors, and every survivor is counted once
            eng = engine(1, lambda n: 0.0, lambda n: 0.0, lambda n: n)
            eng.k1.cap, reads, real_launch = 100, [], eng.k2.launch
            eng.k2.launch = lambda surv, off, nsurv: (reads.append(off + nsurv), real_launch(surv, off, nsurv))[1]
            res = eng.scan_block(0, 101)
            self.assertEqual((res.scanned, res.valid, res.processed), (101, 101, 101))
            self.assertLessEqual(max(reads), 100)


# ---------------------------------------------------------------- survivor accounting (fix B)

def _skip_last(real):
    def batches(self, valid, npass):
        for off, n, full in real(self, valid, npass):
            if off + n == valid:
                n -= 1                                   # the chunk's last survivor never reaches K2/K3
            if n:
                yield off, n, full
    return batches


def _skip_at_boundaries(self, valid, npass):
    off = 0
    while off < valid:
        n = min(valid - off, max(1, self.s2.n // npass))
        yield off, n, True
        off += n + 1                                     # one survivor lost at every batch boundary


def _repeat_one(real):
    def batches(self, valid, npass):
        for off, n, full in real(self, valid, npass):
            yield (off - 1 if off else off), n, full     # same total, but one survivor twice and the last never
    return batches


def _mutants():
    import gpu_setperm
    real = gpu_setperm.Engine._batches
    return [("skip last", _skip_last(real)), ("skip at boundaries", _skip_at_boundaries), ("repeat one", _repeat_one(real))]


_CPU_FORMS = {}
_CPU_SEEDS = {}                                    # (scheme, mnemonic, passphrase) -> the 8 big-endian uint64 seed words
_CPU_DUMPS = {}                                    # (seed words, paths) -> (npaths, 2, 20) uint8 hash160s (pure functions)


def _cpu_engine(chunk=37, batch=2, fault=None):
    """An Engine whose three kernels are exact CPU stand-ins (no OpenCL): K1 filters with Space.candidate_ok (and
    hands its survivors back through survivors(n), like the wrapper), K2 records the survivor slices it is handed and
    writes the PBKDF2 seeds of that slice (as big-endian uint64 words, like the kernel) into `seeds`, K3 compares
    sp_ref's hash160s of every (survivor, passphrase, path, form) of its slice with the current targets and dumps
    the hash160s of a seed array. Everything else - scan_block's batching and survivor accounting, set_targets,
    the window audit k1_spot_check and _derive_dump, run_search's controls - is the real code. Launch sizes are
    fixed, tiny and odd."""
    import gpu_setperm
    import numpy as np
    import setperm_solve as sps

    class K1:
        def __init__(self, space):
            self.space, self.cap, self.surv, self.runs, self.last_idx_sum = space, 1 << 20, [], [], None

        def run(self, start, count):
            from sp_k1_enum import expected_idx_sum
            sp = self.space
            self.runs.append((start, count))
            tested = [i for i in range(start, start + count) if not sp.cfg["distinct"] or len(set(sp.decode(i))) == sp.n]
            self.surv = [i for i in tested if sp.candidate_ok(i)]
            self.last_idx_sum = expected_idx_sum(start, count)
            return count, len(tested), len(self.surv)

        def survivors(self, n):
            return np.array(self.surv[:min(int(n), self.cap)], dtype=np.uint64)

    class K2:
        seeds, max_threads = None, 1 << 17

        def __init__(self, space):
            self.space, self.slices, self.last_slice = space, [], None

        def launch(self, surv, off, nsurv):
            self.last_slice = (int(off), int(nsurv))
            if off < 0 or off + nsurv > len(surv):
                raise ValueError("survivor slice lies outside the survivor buffer")
            self.slices.append((off, nsurv))
            sp = self.space                                 # thread t = survivor t // npass, passphrase t % npass
            rows = []
            for idx in surv[off:off + nsurv]:
                m = " ".join(sp.decode(int(idx)))
                for pp in sp.passes:
                    key = (sp.scheme, m, pp)
                    if key not in _CPU_SEEDS:
                        _CPU_SEEDS[key] = np.frombuffer(sps.seed_of(m, pp, sp.scheme), dtype=">u8").astype(np.uint64)
                    rows.append(_CPU_SEEDS[key])
            self.seeds = np.array(rows, dtype=np.uint64).reshape(len(rows), 8)

    class K3:
        def __init__(self, space, targets):
            self.space, self.last_slice = space, None
            self.set_targets(targets)

        def set_targets(self, targets):
            self.targets = list(targets)

        def run(self, seeds, surv, off, nsurv, npass):
            self.last_slice = (int(off), int(nsurv))
            sp, out = self.space, []
            for idx in surv[off:off + nsurv]:
                for pi in range(npass):
                    key = (sp.scheme, " ".join(sp.decode(idx)), sp.passes[pi], tuple(sp.paths_txt))
                    if key not in _CPU_FORMS:
                        _CPU_FORMS[key] = sp_ref.hash160_forms(key[1], key[2], sp.paths, sp.scheme)
                    for path_i, form, h in _CPU_FORMS[key]:
                        out += [(idx, pi, path_i, form, t) for t, tgt in enumerate(self.targets) if h == tgt]
            return out

        def dump(self, seeds):
            sp = self.space
            out = np.zeros((len(seeds), len(sp.paths), 2, 20), dtype=np.uint8)
            for t, row in enumerate(seeds):
                key = (tuple(int(v) for v in row), tuple(sp.paths))
                if key not in _CPU_DUMPS:
                    hashes = np.zeros((len(sp.paths), 2, 20), dtype=np.uint8)
                    for path_i, form, h in sp_ref.hash160_forms_seed(np.asarray(row, dtype=">u8").tobytes(), sp.paths):
                        hashes[path_i, form] = np.frombuffer(h, dtype=np.uint8)
                    _CPU_DUMPS[key] = hashes
                out[t] = _CPU_DUMPS[key]
            return out

    class FakeGpu:
        @staticmethod
        def read(buf, dtype, count):
            return np.asarray(buf, dtype=dtype).reshape(-1)[:count]

    class CpuEngine(gpu_setperm.Engine):
        made = []

        def __init__(self, space, targets, *, log=print, max_temp=85, hit_cap=256, **_):
            self.space, self.log, self.max_temp, self.hit_cap, self.partial = space, log, max_temp, hit_cap, None
            self.gpu = FakeGpu()
            self.k1, self.k2, self.k3 = K1(space), K2(space), K3(space, targets)
            t = batch * len(space.passes)
            self.s1 = gpu_setperm.Sizer(chunk, lo=chunk, hi=chunk)
            self.s2 = gpu_setperm.Sizer(t, lo=t, hi=t)
            if fault:
                fault(self)
            CpuEngine.made.append(self)
    return CpuEngine


def _cpu_run(text, prize, *, chunk=37, batch=2, fault=None, **kw):
    import gpu_setperm
    eng = _cpu_engine(chunk, batch, fault)
    kw.setdefault("gpu", object())                                        # no OpenCL: run_search never builds a Gpu
    with mock.patch.object(gpu_setperm, "Engine", eng):
        sp, r = _run(text, prize=prize, **kw)
    return sp, r, eng.made


class SurvivorAccountingNoGpu(unittest.TestCase):
    """Every survivor K1 counts must be handed to K2/K3 exactly once, in order: a batch loop that loses or repeats
    one is VOID, never a NO MATCH - whether or not the lost survivor was a plant."""

    def test_scan_block_refuses_a_batch_loop_that_drops_or_repeats_survivors(self):
        import gpu_setperm
        for npass, threads in ((1, 7), (3, 7), (2, 5)):
            clock = [0.0]
            slices = []
            eng = _fake_clock_engine(npass, lambda start, count: (count, count, 23),
                                     lambda surv, off, n: slices.append((off, n)),
                                     gpu_setperm.Sizer(100, lo=100, hi=100), gpu_setperm.Sizer(threads, lo=threads, hi=threads),
                                     clock)
            res = eng.scan_block(0, 300)                                      # honest: 3 chunks of 23 survivors
            self.assertEqual((res.valid, res.processed), (69, 69))
            per = max(1, threads // npass)
            one_chunk = [(o, min(per, 23 - o)) for o in range(0, 23, per)]
            self.assertEqual(slices, one_chunk * 3)                           # the slices tile every chunk exactly
            self.assertGreater(len(one_chunk), 1)
            for name, mutant in _mutants():
                with mock.patch.object(gpu_setperm.Engine, "_batches", mutant):
                    with self.assertRaises(gpu_setperm.EngineError, msg=(npass, name)) as cm:
                        eng.scan_block(0, 300)
                self.assertIn("survivor", str(cm.exception))

    def test_a_thread_budget_below_one_survivors_passphrases_still_makes_progress(self):
        """More passphrases than the K2 sizer's thread budget: a batch is still one whole survivor (all its
        passphrases), never an empty slice - the batch size is clamped to at least 1."""
        import gpu_setperm
        slices = []
        eng = _fake_clock_engine(5, lambda start, count: (count, count, 6), lambda surv, off, n: slices.append((off, n)),
                                 gpu_setperm.Sizer(100, lo=100, hi=100), gpu_setperm.Sizer(3, lo=3, hi=3), [0.0])
        res = eng.scan_block(0, 200)                                          # 2 chunks of 6 survivors, budget 3 < 5
        self.assertEqual((res.valid, res.processed), (12, 12))
        self.assertEqual(slices, [(o, 1) for o in range(6)] * 2)

    def test_every_hit_carries_the_launch_coordinates_that_produced_it(self):
        """`at` = (K1 chunk start, K1 chunk size as launched, K2/K3 slice offset, slice size): what the operator
        needs to re-run an unconfirmed report's launch on the CPU. Includes a clipped tail chunk and slice."""
        import gpu_setperm
        eng = _fake_clock_engine(2, lambda start, count: (count, count, 5), lambda surv, off, n: None,
                                 gpu_setperm.Sizer(100, lo=100, hi=100), gpu_setperm.Sizer(4, lo=4, hi=4), [0.0],
                                 k3_run=lambda seeds, surv, off, n, npass: [(1000 + off, 0, 0, 0, 0)])
        res = eng.scan_block(0, 250)                                          # chunks 0..100, 100..200, 200..250
        want = [(c, n1, off, k) for c, n1 in ((0, 100), (100, 100), (200, 50)) for off, k in ((0, 2), (2, 2), (4, 1))]
        self.assertEqual([h.at for h in res.hits], want)
        self.assertEqual([h.idx for h in res.hits], [1000 + w[2] for w in want])
        self.assertTrue(all(h.tgt_i == 0 for h in res.hits))

    def test_a_batch_loop_that_drops_survivors_is_void_not_no_match(self):
        import gpu_setperm
        text = dict(small_config_texts())["J_bip39_12_dups"]
        _, r, made = _cpu_run(text, _rand(), chunk=101, batch=2)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)       # the honest loop
        self.assertTrue(any(off > 0 for off, _ in made[-1].k2.slices))             # chunks really had several batches
        for name, mutant in _mutants():
            with mock.patch.object(gpu_setperm.Engine, "_batches", mutant):
                _, r, _ = _cpu_run(text, _rand(), chunk=101, batch=2)
            self.assertEqual((r.status, r.exit_code), ("VOID", 4), name)
            self.assertIn("survivor", r.detail, name)

    def test_a_block_result_whose_processed_count_differs_from_valid_is_void(self):
        """Defence in depth: run_search re-checks the block's totals even if scan_block were replaced."""
        for n in (0, 1):
            _, r, _, _ = _fake_search("D_electrum_12_wordlist", _rand(),
                                      post=lambda res, lo, hi, n=n: setattr(res, "valid", 2) or setattr(res, "processed", n))
            self.assertEqual((r.status, r.exit_code), ("VOID", 4), n)
            self.assertIn("survivor", r.detail)

    def test_the_cpu_engine_gives_a_verified_no_match_with_exact_counts(self):
        for name in ("A_bip39_12_r3k1", "J_bip39_12_dups", "K_bip39_12_distinct_overlap"):
            text = dict(small_config_texts())[name]
            _, r, _ = _cpu_run(text, _rand())
            self.assertEqual((r.status, r.exit_code, r.plants_ok), ("NO_MATCH", 0, True), (name, r.detail))
            tested, valid, _ = sp_ref.ref_survivors(cfg_from_text(text))
            self.assertEqual((r.tested, r.valid), (tested, valid), name)

    def test_every_survivor_is_found_as_the_prize_through_tiny_odd_batches(self):
        from sp_space import Space
        for name in ("A_bip39_12_r3k1", "G_bip39_12_k0", "J_bip39_12_dups", "K_bip39_12_distinct_overlap"):
            text = dict(small_config_texts())[name]
            sp = Space(cfg_from_text(text))
            _, _, surv = sp_ref.ref_survivors(sp.cfg)
            self.assertGreaterEqual(len(surv), 4, name)
            for i, idx in enumerate(surv):
                pass_i, path_i, form = _combo(i, sp)
                addr, m = _addr(sp, idx, pass_i, path_i, form)
                _, r, _ = _cpu_run(text, addr)
                self.assertEqual((r.status, r.exit_code), ("FOUND", 2), (name, idx, r.detail))
                self.assertIn(idx, r.found["indices"], (name, idx))
                self.assertEqual((r.found["mnemonic"], r.found["passphrase"], r.found["path"], r.found["form"]),
                                 (m, sp.passes[pass_i], sp.paths_txt[path_i], form), (name, idx))


class CheckpointSignature(unittest.TestCase):
    """A checkpoint is resumed only into the same prize, config, block size AND engine sources."""

    D = "D_electrum_12_wordlist"

    def test_the_engine_hash_covers_every_source_the_engine_runs(self):
        import gpu_setperm
        files = gpu_setperm.engine_files()
        for f in os.listdir(os.path.join(HERE, "kernels")):
            if f.startswith("sp_") and f.endswith(".cl"):
                self.assertIn("kernels/" + f, files)
        for f in ("sp_ref.py", "sp_space.py", "sp_k1_enum.py", "sp_k2_pbkdf2.py", "sp_k3_derive.py", "gpu_setperm.py",
                  "setperm_solve.py", "template_solve.py", "english.txt"):      # the CPU reference and the wordlist too
            self.assertIn(f, files)
        built = set()                                    # every kernel file a wrapper builds, read from the source
        for mod in ("sp_k1_enum.py", "sp_k2_pbkdf2.py", "sp_k3_derive.py", "sp_gpu.py"):
            with open(os.path.join(HERE, mod), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for n in ast.walk(tree):
                if isinstance(n, ast.List) and n.elts and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                                                              for e in n.elts):
                    built |= {e.value for e in n.elts if os.path.exists(os.path.join(HERE, "kernels", e.value + ".cl"))}
        self.assertTrue({"sp_enum", "sp_pbkdf2", "sp_derive", "secp256k1", "common", "sha2"} <= built)
        for k in built:
            self.assertIn(f"kernels/{k}.cl", files)
        for rel in files:
            self.assertTrue(os.path.isfile(os.path.join(HERE, rel)), rel)

    def test_every_local_module_and_data_file_the_engine_imports_or_reads_is_hashed(self):
        """Closed under imports: whatever an engine source imports from this directory, and every data file whose
        name appears in one of them, is itself in the hash (setperm_solve and template_solve were not, nor the wordlist)."""
        import gpu_setperm
        files = set(gpu_setperm.engine_files())
        todo, seen = [f for f in files if f.endswith(".py")], set()
        while todo:
            rel = todo.pop()
            if rel in seen:
                continue
            seen.add(rel)
            with open(os.path.join(HERE, rel), encoding="utf-8") as f:
                src = f.read()
            for n in ast.walk(ast.parse(src)):
                mods = ([a.name for a in n.names] if isinstance(n, ast.Import) else
                        [n.module] if isinstance(n, ast.ImportFrom) and n.module else [])
                for m in mods:
                    local = m.split(".")[0] + ".py"
                    if os.path.exists(os.path.join(HERE, local)):
                        self.assertIn(local, files, f"{rel} imports {m}")
                        todo.append(local)
            for name in os.listdir(HERE):
                if name.endswith(".txt") and name in src:
                    self.assertIn(name, files, f"{rel} reads {name}")
        self.assertIn("english.txt", files)
        self.assertTrue({"setperm_solve.py", "template_solve.py", "sp_ref.py", "sp_space.py"} <= seen)

    def test_changing_any_engine_source_changes_the_hash(self):
        import gpu_setperm
        files = gpu_setperm.engine_files()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "kernels"))
            for rel in files:
                shutil.copyfile(os.path.join(HERE, rel), os.path.join(d, rel))
            h0 = gpu_setperm.engine_hash(d)
            self.assertEqual(h0, gpu_setperm.engine_hash())
            for rel in files:
                p = os.path.join(d, rel)
                with open(p, "rb") as f:
                    orig = f.read()
                with open(p, "wb") as f:
                    f.write(orig + b" ")
                self.assertNotEqual(gpu_setperm.engine_hash(d), h0, rel)
                with open(p, "wb") as f:
                    f.write(orig)
            self.assertEqual(gpu_setperm.engine_hash(d), h0)
            with open(os.path.join(d, "kernels", "sp_new.cl"), "w") as f:     # a new engine kernel joins the hash
                f.write("// new\n")
            self.assertNotEqual(gpu_setperm.engine_hash(d), h0)

    def test_a_same_length_edit_of_any_engine_source_changes_the_hash(self):
        """The hash covers the bytes, not just names and lengths: flipping one bit in place must change it."""
        import gpu_setperm
        files = gpu_setperm.engine_files()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "kernels"))
            for rel in files:
                shutil.copyfile(os.path.join(HERE, rel), os.path.join(d, rel))
            h0 = gpu_setperm.engine_hash(d)
            for rel in files:
                p = os.path.join(d, rel)
                with open(p, "rb") as f:
                    orig = f.read()
                for pos in (0, len(orig) // 2, len(orig) - 1):
                    edited = bytearray(orig)
                    edited[pos] ^= 1
                    with open(p, "wb") as f:
                        f.write(bytes(edited))
                    self.assertNotEqual(gpu_setperm.engine_hash(d), h0, (rel, pos))
                with open(p, "wb") as f:
                    f.write(orig)
            self.assertEqual(gpu_setperm.engine_hash(d), h0)

    def test_a_checkpoint_from_another_prize_ub_config_or_engine_is_refused(self):
        import gpu_setperm
        prize = _rand()
        text = dict(small_config_texts())[self.D]
        ck = os.path.join(tempfile.mkdtemp(), "ck")
        _, r, _, _ = _fake_search(self.D, prize, ub=5, ck=ck, max_blocks=2)
        self.assertEqual((r.status, r.exit_code), ("STOPPED", 5))
        with open(ck, encoding="utf-8") as f:
            before = f.read()
        with self.assertRaises(SystemExit):
            _fake_search(self.D, _rand(), ub=5, ck=ck)                                  # another prize
        with self.assertRaises(SystemExit):
            _fake_search(self.D, prize, ub=6, ck=ck)                                    # another block size
        with self.assertRaises(SystemExit):
            _fake_search(text=text + "PASS extra\n", prize=prize, ub=5, ck=ck)          # another config
        with mock.patch.object(gpu_setperm, "engine_hash", lambda root=None: "0" * 16):
            with self.assertRaises(SystemExit):
                _fake_search(self.D, prize, ub=5, ck=ck)                                # another engine
        with open(ck, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)                                          # refusals wrote nothing
        _, r, _, blocks = _fake_search(self.D, prize, ub=5, ck=ck)                      # the same run resumes
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        self.assertEqual(len(blocks), 3)

    def test_the_signature_depends_on_every_input(self):
        import gpu_setperm
        from sp_space import Space
        text = dict(small_config_texts())[self.D]
        sp, sp2 = Space(cfg_from_text(text)), Space(cfg_from_text(text + "PASS extra\n"))
        a, b = _rand(), _rand()
        base = gpu_setperm.sig_gpu(sp, a, 5)
        self.assertEqual(base, gpu_setperm.sig_gpu(Space(cfg_from_text(text)), a, 5))
        self.assertEqual(len({base, gpu_setperm.sig_gpu(sp, b, 5), gpu_setperm.sig_gpu(sp, a, 6),
                              gpu_setperm.sig_gpu(sp2, a, 5)}), 4)
        with mock.patch.object(gpu_setperm, "engine_hash", lambda root=None: "f" * 16):
            self.assertNotEqual(gpu_setperm.sig_gpu(sp, a, 5), base)


# ---------------------------------------------------------------- GPU

@contextlib.contextmanager
def _tiny_launches(chunk=37, batch=3):
    """Every Engine built inside uses fixed, tiny, odd launch sizes: K1 chunks of `chunk` candidates and K2/K3
    batches of `batch` survivors, so a block needs many K1 chunks and a chunk several K2/K3 batches."""
    import gpu_setperm
    orig = gpu_setperm.Engine.__init__

    def init(self, space, targets, **kw):
        orig(self, space, targets, **kw)
        t = batch * len(space.passes)
        self.s1 = gpu_setperm.Sizer(chunk, lo=chunk, hi=chunk)
        self.s2 = gpu_setperm.Sizer(t, lo=t, hi=t)
    with mock.patch.object(gpu_setperm.Engine, "__init__", init):
        yield


@require_gpu
class EndToEnd(unittest.TestCase):
    def _pick(self, name):
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[name]))
        idx = next(i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i))
        return sp, idx

    def test_finds_a_compressed_hit_on_a_late_path_with_a_passphrase(self):
        sp, idx = self._pick("A_bip39_12_r3k1")
        addr, m = _addr(sp, idx, 1, 1, 0)
        sp2, r = _run(dict(small_config_texts())["A_bip39_12_r3k1"], prize=addr)
        self.assertEqual((r.status, r.exit_code), ("FOUND", 2))
        self.assertEqual((r.found["mnemonic"], r.found["passphrase"], r.found["path"], r.found["form"]),
                         (m, sp.passes[1], sp.paths_txt[1], 0))

    def test_passphrase_not_in_the_list_is_a_miss(self):
        sp, idx = self._pick("A_bip39_12_r3k1")
        addr, _ = _addr(sp, idx, 1, 1, 0)                       # derived with passes[1] = 'moon' (passes[0] is '')
        text = dict(small_config_texts())["A_bip39_12_r3k1"].replace("PASS moon\n", "PASS Moon\n").replace("PASS MOON\n", "PASS Moon2\n")
        _, r = _run(text, prize=addr)                            # BIP39 keeps case: 'moon' is no longer tried
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0))

    def test_finds_an_uncompressed_hit_electrum(self):
        sp, idx = self._pick("D_electrum_12_wordlist")
        addr, m = _addr(sp, idx, 0, 0, 1)
        _, r = _run(dict(small_config_texts())["D_electrum_12_wordlist"], prize=addr)
        self.assertEqual((r.status, r.found["mnemonic"], r.found["form"]), ("FOUND", m, 1))

    def test_no_match_is_verified_by_plants_and_coverage(self):
        _, r = _run(dict(small_config_texts())["E_electrum_13_alts"], prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0))
        self.assertTrue(r.plants_ok)
        cfg = cfg_from_text(dict(small_config_texts())["E_electrum_13_alts"])
        tested, valid, _ = sp_ref.ref_survivors(cfg)
        self.assertEqual((r.tested, r.valid), (tested, valid))

    def test_void_when_the_engine_drops_survivors(self):
        import sp_k1_enum
        orig = sp_k1_enum.EnumKernel.run

        def lossy(self, start, count):
            s, t, v = orig(self, start, count)
            return s, t, 0                                           # pretend nothing survived
        sp_k1_enum.EnumKernel.run = lossy
        try:
            _, r = _run(dict(small_config_texts())["D_electrum_12_wordlist"], prize=address_of_hash160(os.urandom(20)))
        finally:
            sp_k1_enum.EnumKernel.run = orig
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))

    def test_void_when_the_engine_skips_candidates(self):
        """Coverage is checked independently of the plants: a K1 that under-reports `scanned` is VOID."""
        import sp_k1_enum
        orig = sp_k1_enum.EnumKernel.run

        def short(self, start, count):
            s, t, v = orig(self, start, count)
            return s - 1, t, v
        with mock.patch.object(sp_k1_enum.EnumKernel, "run", short):
            _, r = _run(dict(small_config_texts())["D_electrum_12_wordlist"], prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIn("scanned", r.detail)

    def test_void_when_k1_scans_a_shifted_range(self):
        """The same thread count over [start+1, start+count+1): only the index checksum can see it."""
        import sp_k1_enum
        orig = sp_k1_enum.EnumKernel.run

        def shifted(self, start, count):
            return orig(self, start + 1, count) if start + count < self.space.total else orig(self, start, count)
        with mock.patch.object(sp_k1_enum.EnumKernel, "run", shifted), \
                mock.patch.object(sp_k1_enum, "expected_idx_sum", lambda s, c, real=sp_k1_enum.expected_idx_sum: real(s - 1, c)):
            _, r = _run(dict(small_config_texts())["D_electrum_12_wordlist"], prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIn("checksum", r.detail)

    def test_survivor_buffer_overflow_shrinks_the_chunk_and_stays_exact(self):
        cfg = cfg_from_text(dict(small_config_texts())["C_bip39_15_wordlist"])
        tested, valid, _ = sp_ref.ref_survivors(cfg)
        _, r = _run(dict(small_config_texts())["C_bip39_15_wordlist"], prize=address_of_hash160(os.urandom(20)),
                    surv_cap=32)
        self.assertEqual((r.status, r.tested, r.valid), ("NO_MATCH", tested, valid))

    def test_duplicate_require_words_report_every_hit(self):
        from sp_space import Space
        text = dict(small_config_texts())["J_bip39_12_dups"]
        sp = Space(cfg_from_text(text))
        cfg = sp.cfg
        _, _, surv = sp_ref.ref_survivors(cfg)
        idx = surv[len(surv) // 2]
        addr, m = _addr(sp, idx, 0, 0, 0)
        same = [i for i in surv if " ".join(sp.decode(i)) == m]
        self.assertGreaterEqual(len(same), 2)                        # the duplicated REQUIRE word makes twins
        from gpu_setperm import Engine
        h = hash160_of_address(addr)
        eng = Engine(sp, [h], log=QUIET)
        res = eng.scan_block(0, sp.total)
        got = sorted(x.idx for x in res.hits if x.path_i == 0 and x.pass_i == 0 and x.form == 0)
        self.assertEqual(got, sorted(same))

    def test_every_survivor_is_found_as_the_prize_through_tiny_odd_launches(self):
        """K1 chunks of 37 candidates and K2/K3 batches of 3 survivors: every survivor of four configs, in turn,
        is the prize, and each run must be FOUND at that index."""
        from sp_space import Space
        with _tiny_launches(chunk=37, batch=3):
            for name in ("G_bip39_12_k0", "J_bip39_12_dups", "K_bip39_12_distinct_overlap", "I_electrum_13_r0k1"):
                text = dict(small_config_texts())[name]
                sp = Space(cfg_from_text(text))
                _, _, surv = sp_ref.ref_survivors(sp.cfg)
                for i, idx in enumerate(surv):
                    pass_i, path_i, form = _combo(i, sp)
                    addr, m = _addr(sp, idx, pass_i, path_i, form)
                    _, r = _run(text, prize=addr)
                    self.assertEqual((r.status, r.exit_code), ("FOUND", 2), (name, idx, r.detail))
                    self.assertIn(idx, r.found["indices"], (name, idx))
                    self.assertEqual((r.found["mnemonic"], r.found["form"]), (m, form), (name, idx))

    def test_every_survivor_of_a_multi_pass_multi_path_config_is_reported_through_tiny_launches(self):
        """A: 3 passphrases x 2 paths. Each survivor gets its own target with its own (passphrase, path, form);
        with K1 chunks of 53 and K2/K3 batches of 2 survivors (6 threads), every one must be reported."""
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["A_bip39_12_r3k1"]))
        _, _, surv = sp_ref.ref_survivors(sp.cfg)
        want, targets = set(), []
        for i, idx in enumerate(surv):
            pass_i, path_i, form = _combo(i, sp)
            targets.append(hash160_of_address(_addr(sp, idx, pass_i, path_i, form)[0]))
            want.add((idx, pass_i, path_i, form, i))
        with _tiny_launches(chunk=53, batch=2):
            eng = gpu_setperm.Engine(sp, targets, log=QUIET)
            res = eng.scan_block(0, sp.total)
        self.assertEqual((res.scanned, res.valid, res.processed), (sp.total, len(surv), len(surv)))
        got = {(h.idx, h.pass_i, h.path_i, h.form, h.tgt_i) for h in res.hits}
        self.assertEqual(want - got, set())
        for h in got - want:                                             # anything extra must be a true twin
            self.assertTrue(gpu_setperm._cpu_confirms(sp, gpu_setperm.Hit(*h), targets[h[4]]), h)

    def test_a_batch_loop_that_drops_survivors_is_void_on_the_gpu(self):
        import gpu_setperm
        text = dict(small_config_texts())["J_bip39_12_dups"]
        with _tiny_launches(chunk=101, batch=2):
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)))
            self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
            for name, mutant in _mutants():
                with mock.patch.object(gpu_setperm.Engine, "_batches", mutant):
                    _, r = _run(text, prize=address_of_hash160(os.urandom(20)))
                self.assertEqual((r.status, r.exit_code), ("VOID", 4), name)
                self.assertIn("survivor", r.detail, name)

    def test_checkpoint_resume_finishes_the_same_run(self):
        text = dict(small_config_texts())["D_electrum_12_wordlist"]
        prize = address_of_hash160(os.urandom(20))
        with tempfile.TemporaryDirectory() as d:
            ck = os.path.join(d, "ck")
            # force several blocks by shrinking the block target
            import gpu_setperm
            orig = gpu_setperm.plan_block_units
            gpu_setperm.plan_block_units = lambda space, target_deriv=4e6: max(1, space.units // 4)
            try:
                _, r1 = _run(text, prize=prize, checkpoint=ck, max_blocks=2)
                self.assertEqual((r1.status, r1.exit_code), ("STOPPED", 5))
                self.assertEqual(r1.blocks_done, 2)
                _, r2 = _run(text, prize=prize, checkpoint=ck)
            finally:
                gpu_setperm.plan_block_units = orig
            cfg = cfg_from_text(text)
            tested, valid, _ = sp_ref.ref_survivors(cfg)
            self.assertEqual((r2.status, r2.tested, r2.valid), ("NO_MATCH", tested, valid))

    def test_checkpoint_resume_with_several_passphrases_keeps_the_counts_exact(self):
        """`derived` in the checkpoint is valid x passphrases; resuming must turn it back into `valid`."""
        text = dict(small_config_texts())["A_bip39_12_r3k1"]          # 3 passphrases, 2 paths
        prize = address_of_hash160(os.urandom(20))
        with tempfile.TemporaryDirectory() as d:
            ck = os.path.join(d, "ck")
            import gpu_setperm
            with mock.patch.object(gpu_setperm, "plan_block_units", lambda space, target_deriv=4e6: max(1, space.units // 4)):
                _, r1 = _run(text, prize=prize, checkpoint=ck, max_blocks=3)
                self.assertEqual((r1.status, r1.blocks_done), ("STOPPED", 3))
                _, r2 = _run(text, prize=prize, checkpoint=ck)
            with open(ck, encoding="utf-8") as f:
                self.assertEqual(sum(1 for line in f if line.startswith("done ")), 4)
        tested, valid, _ = sp_ref.ref_survivors(cfg_from_text(text))
        self.assertEqual((r2.status, r2.tested, r2.valid, r2.plants_ok), ("NO_MATCH", tested, valid, True))

    def test_bench_uses_a_dummy_target_and_finds_its_plant(self):
        from gpu_setperm import bench
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        b = bench(sp, log=QUIET)
        self.assertTrue(b["plant_found"])
        self.assertTrue(b["coverage_exact"])
        self.assertGreater(b["deriv_per_s"], 0)

    def test_thermal_pause_and_heartbeat_run_at_least_every_five_seconds(self):
        """Every K1 launch and every K2/K3 batch is followed by the check; with each clock read 6 s apart,
        one K1 launch and one K2 batch give two checks."""
        import itertools
        import time
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
        eng.s1.n = 1 << 20                                           # one K1 launch, one K2 launch
        ticks = itertools.count()
        fake_time = SimpleNamespace(perf_counter=lambda: 6.0 * next(ticks), strftime=time.strftime, sleep=lambda s: None)
        cooled, beats = [], []
        with mock.patch.object(gpu_setperm, "time", fake_time), \
                mock.patch.object(gpu_setperm, "wait_cool", lambda limit, log: cooled.append(limit)):
            eng._last_beat = None
            res = eng.scan_block(0, sp.total, heartbeat=lambda r, cur: beats.append((r.valid, cur)))
        self.assertEqual(cooled, [eng.max_temp] * 2)
        self.assertEqual(len(beats), 2)
        self.assertEqual(beats[-1][0], res.valid)

    def test_bench_never_loads_the_prize(self):
        import gpu_setperm
        import template_solve
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        seen = []
        orig = gpu_setperm.Engine

        class Spy(orig):
            def __init__(self, space, targets, **kw):
                seen.append(list(targets))
                super().__init__(space, targets, **kw)
        with mock.patch.object(gpu_setperm, "Engine", Spy):
            b = gpu_setperm.bench(sp, log=QUIET)
        self.assertEqual(len(seen), 1)
        self.assertNotIn(hash160_of_address(template_solve.TARGET), seen[0])
        self.assertEqual(len(seen[0]), 2)                            # one dummy + one planted seed
        self.assertFalse(b["prize_loaded"])

    def test_a_real_run_measures_its_own_rate_and_never_uses_the_placeholder(self):
        """Without an assumed rate the guard's projection comes from the rate probe, not DEFAULT_ASSUME_RATE
        (which is only for --dry-run)."""
        import gpu_setperm
        calls, real = [], gpu_setperm.probe_rate

        def spy(space, gpu, log):
            calls.append(1)
            return real(space, gpu, log)
        with mock.patch.object(gpu_setperm, "probe_rate", spy):
            _, r = _run(dict(small_config_texts())["D_electrum_12_wordlist"], prize=address_of_hash160(os.urandom(20)),
                        probe=None)
        self.assertEqual(len(calls), 1)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0))
        self.assertNotEqual(r.est["rate"], gpu_setperm.DEFAULT_ASSUME_RATE)

    def test_probing_the_rate_uses_no_prize_and_the_run_still_completes(self):
        text = dict(small_config_texts())["D_electrum_12_wordlist"]
        _, r = _run(text, prize=address_of_hash160(os.urandom(20)), probe=None)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0))
        self.assertGreater(r.est["rate"], 0)


@require_gpu
class TrustModel(unittest.TestCase):
    """What makes a FOUND / NO MATCH / VOID trustworthy: the CPU has the last word."""

    TEXT = dict(small_config_texts())["D_electrum_12_wordlist"]

    def _prize(self, name="D_electrum_12_wordlist", pass_i=0, path_i=0, form=0, nth=0):
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[name]))
        idx = [i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i)][nth]
        addr, m = _addr(sp, idx, pass_i, path_i, form)
        return sp, idx, addr, m

    def test_a_prize_hit_the_cpu_does_not_confirm_is_void_and_never_claims_found(self):
        sp, idx, addr, m = self._prize()
        calls = []

        def deny(mn, pp, path, form, scheme, target):
            calls.append((mn, target))
            return False
        with mock.patch.object(sp_ref, "confirm", deny):
            _, r = _run(self.TEXT, prize=addr)
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIsNone(r.found)
        self.assertFalse(os.path.exists(r.found_path), "a FOUND file was written for an unconfirmed hit")
        self.assertIn(m, [c[0] for c in calls])                      # the GPU did report the hit ...
        self.assertTrue(all(c[1] == hash160_of_address(addr) for c in calls))   # ... and the CPU was asked about the prize
        self.assertNotIn("FOUND", [rec["status"] for rec in _ledger(r)])
        self.assertEqual([rec["status"] for rec in _ledger(r)], ["START", "VOID"])
        # the reports, for a CPU re-check: one from each of the two K3 slots that hold the prize
        self.assertEqual([p["idx"] for p in _ledger(r)[1]["prize_hits"]], [idx, idx])
        self.assertEqual(len({p["tgt_i"] for p in _ledger(r)[1]["prize_hits"]}), 1)     # both mean target 0, the prize
        self.assertIsNotNone(_ledger(r)[1]["prize_hits"][0]["chunk_start"])

    def test_a_confirmed_prize_hit_writes_the_found_file_and_exits_2(self):
        sp, idx, addr, m = self._prize("A_bip39_12_r3k1", pass_i=1, path_i=1, form=0)
        _, r = _run(dict(small_config_texts())["A_bip39_12_r3k1"], prize=addr)
        self.assertEqual((r.status, r.exit_code), ("FOUND", 2))
        with open(r.found_path, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn(f"mnemonic: {m!r}", txt)
        self.assertIn(f"passphrase: {sp.passes[1]!r}", txt)
        self.assertIn(f"path: {sp.paths_txt[1]!r}", txt)
        self.assertIn("form: 0", txt)
        self.assertIn(f"target: {addr}", txt)
        recs = _ledger(r)
        self.assertEqual([x["status"] for x in recs], ["START", "FOUND"])
        self.assertNotIn(m, json.dumps(recs))                        # the ledger never holds the seed phrase

    def test_a_candidate_is_printed_even_if_the_found_file_cannot_be_written(self):
        import gpu_setperm
        sp, idx, addr, m = self._prize()
        lines, d = [], tempfile.mkdtemp()
        with mock.patch.object(gpu_setperm, "probe_rate", lambda space, gpu, log: 1e9):
            r = gpu_setperm.run_search(sp, prize_addr=addr, plant_seed=11, log=lines.append, signoff=SIGNOFF,
                                       ledger=os.path.join(d, "l.jsonl"), found_path=d)     # a directory: open() fails
        self.assertEqual((r.status, r.exit_code), ("FOUND", 2))
        text = "\n".join(map(str, lines))
        self.assertIn(m, text)
        self.assertIn("COULD NOT WRITE", text)

    def test_a_found_file_is_never_overwritten_by_a_later_find(self):
        _, _, addr1, m1 = self._prize(nth=0)
        _, _, addr2, m2 = self._prize(nth=1)
        d = tempfile.mkdtemp()
        _, r1 = _run(self.TEXT, prize=addr1, workdir=d)
        _, r2 = _run(self.TEXT, prize=addr2, workdir=d)
        self.assertEqual((r1.status, r2.status), ("FOUND", "FOUND"))
        with open(r1.found_path, encoding="utf-8") as f:
            txt = f.read()
        self.assertIn(m1, txt)
        self.assertIn(m2, txt)

    def test_a_confirmed_prize_hit_is_reported_even_if_a_control_plant_failed(self):
        """FOUND rests on the CPU confirmation alone; plants only gate a NO MATCH."""
        import gpu_setperm
        sp, idx, addr, m = self._prize()
        real = gpu_setperm.make_block_plant

        def broken_plant(space, seed, b, lo, hi):
            return real(space, seed, b, lo, hi)._replace(h160=os.urandom(20))     # can never be found
        with mock.patch.object(gpu_setperm, "make_block_plant", broken_plant):
            _, r = _run(self.TEXT, prize=addr)
            self.assertEqual((r.status, r.exit_code, r.found["mnemonic"]), ("FOUND", 2, m))
            _, r = _run(self.TEXT, prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))       # ... but with no hit the broken control voids the run

    def test_the_prize_comes_only_from_the_prize_addr_argument(self):
        """Two prizes from two different candidates: each run reports exactly the one it was given, in the
        FOUND file and the ledger, and the real prize address appears nowhere."""
        import template_solve
        _, i1, addr1, m1 = self._prize(nth=0)
        _, i2, addr2, m2 = self._prize(nth=3)
        self.assertNotEqual(m1, m2)
        d = tempfile.mkdtemp()
        with mock.patch.object(template_solve, "TARGET", address_of_hash160(os.urandom(20))):
            _, r1 = _run(self.TEXT, prize=addr1, workdir=d)
            _, r2 = _run(self.TEXT, prize=addr2, workdir=d)
            _, r3 = _run(self.TEXT, prize=address_of_hash160(os.urandom(20)), workdir=d)
        self.assertEqual([r.status for r in (r1, r2, r3)], ["FOUND", "FOUND", "NO_MATCH"])
        self.assertEqual((r1.found["mnemonic"], r2.found["mnemonic"]), (m1, m2))
        recs = _ledger(r1)
        self.assertEqual([x["status"] for x in recs], ["START", "FOUND", "START", "FOUND", "START", "NO_MATCH"])
        self.assertEqual([x["target"] for x in recs if x["status"] == "FOUND"], [addr1, addr2])
        with open(r1.found_path, encoding="utf-8") as f:
            txt = f.read()
        self.assertEqual(txt.count("target: "), 2)
        self.assertIn(f"target: {addr1}", txt)
        self.assertIn(f"target: {addr2}", txt)
        with open(r1.ledger_path, encoding="utf-8") as f:
            self.assertNotIn(template_solve.TARGET, txt + f.read())

    def test_twin_seeds_on_a_plant_do_not_void_a_clean_run(self):
        """J has a duplicated REQUIRE word, so every seed is reachable from >= 2 indices. The twin reports on a
        plant's target are the same seed and must be accepted (CPU-confirmed), not called 'unexpected'."""
        _, r = _run(dict(small_config_texts())["J_bip39_12_dups"], prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        self.assertTrue(r.plants_ok)

    def test_a_prize_with_twins_reports_every_index(self):
        from sp_space import Space
        text = dict(small_config_texts())["J_bip39_12_dups"]
        sp = Space(cfg_from_text(text))
        _, _, surv = sp_ref.ref_survivors(sp.cfg)
        idx = surv[len(surv) // 2]
        addr, m = _addr(sp, idx, 0, 0, 0)
        same = sorted(i for i in surv if " ".join(sp.decode(i)) == m)
        _, r = _run(text, prize=addr)
        self.assertEqual((r.status, r.found["mnemonic"]), ("FOUND", m))
        self.assertEqual(sorted(r.found["indices"]), same)

    def test_a_broken_engine_voids_in_the_first_block_not_at_the_end(self):
        """Every block carries its own plant, so a broken run fails in its first block, not after hours. (The
        combo self-test that precedes block 0 is switched off here so that the BLOCK control is what is shown.)"""
        import gpu_setperm
        import sp_k1_enum
        orig = sp_k1_enum.EnumKernel.run

        def lossy(self, start, count):
            s, t, v = orig(self, start, count)
            return s, t, 0
        with mock.patch.object(gpu_setperm, "plan_block_units", lambda space, target_deriv=4e6: max(1, space.units // 4)), \
                mock.patch.object(gpu_setperm, "combo_selftest", lambda space, engine, seed=0: SimpleNamespace(
                    survivors=[], combos=0, known_answers=0, rounds=0)), \
                mock.patch.object(sp_k1_enum.EnumKernel, "run", lossy):
            _, r = _run(self.TEXT, prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0))
        self.assertIn("block 0", r.detail)

    def test_the_same_broken_engine_is_void_before_block_0_by_the_combo_self_test(self):
        import gpu_setperm
        import sp_k1_enum
        orig = sp_k1_enum.EnumKernel.run

        def lossy(self, start, count):
            s, t, v = orig(self, start, count)
            return s, t, 0
        scanned = []
        real_scan = gpu_setperm.Engine.scan_block
        with mock.patch.object(gpu_setperm, "plan_block_units", lambda space, target_deriv=4e6: max(1, space.units // 4)), \
                mock.patch.object(gpu_setperm.Engine, "scan_block",
                                  lambda self, lo, hi, hb=None: (scanned.append((lo, hi)), real_scan(self, lo, hi, hb))[1]), \
                mock.patch.object(sp_k1_enum.EnumKernel, "run", lossy):
            _, r = _run(self.TEXT, prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0))
        self.assertIn("combo self-test", r.detail)
        self.assertTrue(scanned and all(hi - lo == 1 for lo, hi in scanned), scanned)     # no block was scanned

    def test_a_twin_report_alone_does_not_count_as_finding_the_plant(self):
        """A plant counts only when reported at the index it was planted at; the same seed reported at a
        twin index proves nothing about the planted index (spec: 'found at its expected index')."""
        import gpu_setperm
        text = dict(small_config_texts())["J_bip39_12_dups"]
        box = {}
        real_make = gpu_setperm.make_block_plant

        def spy_make(space, seed, b, lo, hi):
            box["plant"] = real_make(space, seed, b, lo, hi)
            return box["plant"]
        orig = gpu_setperm.Engine.scan_block

        def no_exact_hits(self, start, end, heartbeat=None):
            res = orig(self, start, end, heartbeat)
            if "plant" not in box:                                   # the combo self-test scans before any block
                return res
            p = box["plant"]
            box["twins"] = [h for h in res.hits if h.tgt_i == 1 and h.idx != p.idx]
            res.hits = [h for h in res.hits if not (h.tgt_i == 1 and h.idx == p.idx)]
            return res
        with mock.patch.object(gpu_setperm, "make_block_plant", spy_make), \
                mock.patch.object(gpu_setperm, "PLANTS_PER_BLOCK", 1), \
                mock.patch.object(gpu_setperm, "PRIZE_COPIES", 1), \
                mock.patch.object(gpu_setperm.Engine, "scan_block", no_exact_hits):
            # one plant, one prize slot and an even plant seed: block 0 searches [prize, plant], the plant is raw slot 1
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)), plant_seed=12)
        self.assertTrue(box["twins"])                                # the plant's seed WAS reported, at a twin index
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIn("was not found", r.detail)

    def test_launch_sizes_are_capped_for_the_display_watchdog(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
        self.assertLessEqual(eng.s2.hi, 1 << 17)          # K2 at 1<<20 threads would take ~2.7 s
        self.assertLessEqual(eng.k2.max_threads, 1 << 17)
        self.assertLessEqual(eng.s1.hi, 1 << 25)
        self.assertLessEqual(eng.s1.n, 1 << 12)           # both start small and grow 2x per launch
        self.assertLessEqual(eng.s2.n, 1 << 10)

    def test_a_hit_buffer_overflow_is_void_never_truncation(self):
        import gpu_setperm
        from sp_space import Space
        text = dict(small_config_texts())["J_bip39_12_dups"]
        sp = Space(cfg_from_text(text))
        _, _, surv = sp_ref.ref_survivors(sp.cfg)
        addr, _ = _addr(sp, surv[len(surv) // 2], 0, 0, 0)          # >= 2 twin hits + the plants' hits
        orig = gpu_setperm.Engine.__init__

        def tiny_hits(self, space, targets, **kw):
            kw["hit_cap"] = 1
            orig(self, space, targets, **kw)
        with mock.patch.object(gpu_setperm.Engine, "__init__", tiny_hits):
            _, r = _run(text, prize=addr)
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIsNone(r.found)


# ================================================================ fixes found by the independent review of 9e338f2

@contextlib.contextmanager
def _main_rounds_only():
    """The combo self-test without its slot sweep and its slot reach (each has tests of its own)."""
    import gpu_setperm
    with mock.patch.object(gpu_setperm, "_slot_sweep", lambda *a: (0, 0)), \
            mock.patch.object(gpu_setperm, "_slot_reach", lambda *a: (0, 0)):
        yield


def _k3_fault(transform):
    """A `fault(engine)` hook for _cpu_run / _selftest_engine: the CPU K3 stand-in's hits go through `transform`."""
    def apply(eng):
        real = eng.k3.run
        eng.k3.run = lambda seeds, surv, off, n, npass: transform(list(real(seeds, surv, off, n, npass)))
    return apply


def _selftest_engine(name_or_text, fault=None, hit_cap=256, chunk=37, batch=2):
    """(space, CPU engine) for calling combo_selftest directly: the real Engine.scan_block over CPU kernels."""
    from sp_space import Space
    text = dict(small_config_texts()).get(name_or_text, name_or_text)
    sp = Space(cfg_from_text(text))
    eng = _cpu_engine(chunk, batch, fault)(sp, [os.urandom(20)], hit_cap=hit_cap)
    return sp, eng


class PrizeSlotRotationNoGpu(unittest.TestCase):
    """Fix 1: the prize used to be K3 target slot 0 and the plant slot 1 in EVERY block, so a fault confined to slot 0
    (a kernel loop starting at t=1) gave 40/40 false NO_MATCH: the plants never used slot 0. Now the two swap.
    These tests run ONE plant per block and ONE copy of the prize with the plant seed 11 (_fake_search), so the
    prize is at slot (block + 11) % 2: slot 1 in even blocks, slot 0 in odd ones. PrizeSlotNoGpu has the rule for
    many plants and two copies of the prize."""

    D = "D_electrum_12_wordlist"

    def _blk(self, ub=5):
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[self.D]))
        return sp, ub * sp.per_unit

    def test_block_targets_alternate_and_map_back(self):
        from gpu_setperm import _logical_tgt, block_targets
        prize, plant = b"p" * 20, b"q" * 20
        self.assertEqual(block_targets(prize, [plant], 0, 11, 1), ([plant, prize], (1,)))
        self.assertEqual(block_targets(prize, [plant], 1, 11, 1), ([prize, plant], (0,)))
        self.assertEqual([block_targets(prize, [plant], b, 11, 1)[1] for b in (2, 3, 4, 5)], [(1,), (0,), (1,), (0,)])
        for b in range(4):                                                      # no plant: the prize alone, slot 0
            self.assertEqual(block_targets(prize, [], b, 11, 1), ([prize], (0,)))
        # _logical_tgt(raw slot, prize slots, plants): 0 = the prize, 1 = the plant
        self.assertEqual([_logical_tgt(t, (0,), 1) for t in (0, 1, 2, -1, 9)], [0, 1, 2, -1, 9])
        self.assertEqual([_logical_tgt(t, (1,), 1) for t in (0, 1, 2, -1, 9)], [1, 0, 2, -1, 9])
        self.assertEqual([_logical_tgt(t, (0,), 0) for t in (0, 1, 2)], [0, 1, 2])   # 1 stays 1: refused, no plant

    def test_every_block_is_scanned_for_its_targets_in_the_rotated_order(self):
        sp, blk = self._blk()
        prize = _rand()
        _, r, plants, blocks = _fake_search(self.D, prize, ub=5)
        self.assertEqual(len(blocks), 5)
        ph = hash160_of_address(prize)
        for b, targets in enumerate(r.given):
            want = [plants[b].h160, ph] if b % 2 == 0 else [ph, plants[b].h160]
            self.assertEqual(targets, want, b)
        self.assertEqual([t.index(ph) for t in r.given], [1, 0, 1, 0, 1])      # the prize slot, block by block

    def test_a_block_without_a_plant_searches_the_prize_alone(self):
        import gpu_setperm
        real = gpu_setperm.make_block_plant
        prize = _rand()
        _, r, plants, _ = _fake_search(self.D, prize, ub=5,
                                       make=lambda space, seed, b, lo, hi: None if b in (1, 2) else real(space, seed, b, lo, hi))
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        self.assertEqual([len(t) for t in r.given], [2, 1, 1, 2, 2])
        self.assertEqual(r.given[1], [hash160_of_address(prize)])
        self.assertEqual(r.given[2], [hash160_of_address(prize)])

    def test_a_fault_confined_to_one_target_slot_is_void_in_the_first_block_that_uses_it(self):
        """Drop every K3 report at raw slot k (a kernel loop that starts at t=1, or stops one short). With the plant
        at slot 1 in every block, slot 0 was never exercised; now block 0 (plant at 0) or block 1 (plant at 1)
        fails, whichever slot is broken."""
        sp, blk = self._blk()
        for k, void_at in ((0, 0), (1, 1)):
            def drop(res, lo, hi, k=k):
                res.hits = [h for h in res.hits if h.tgt_i != k]
            _, r, _, _ = _fake_search(self.D, _rand(), ub=5, post=drop)
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, void_at), k)
            self.assertIn(f"block {void_at}", r.detail)
            self.assertIn("was not found", r.detail)

    def test_a_prize_hit_reported_at_either_slot_is_found(self):
        import gpu_setperm
        sp, blk = self._blk()
        good = next(i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i))
        addr, m = _addr(sp, good, 0, 0, 0)
        H = gpu_setperm.Hit
        for b, slot in ((1, 0), (2, 1)):
            # the hit is only ever reported by block b, where the prize sits at raw slot `slot`
            _, r, plants, _ = _fake_search(self.D, addr, ub=5, edit=lambda hits, lo, hi, b=b: hits + [H(good, 0, 0, 0, 0)]
                                           if lo == b * blk else hits)
            self.assertEqual((r.status, r.exit_code, r.found["mnemonic"]), ("FOUND", 2, m), b)
            self.assertEqual(r.given[b].index(hash160_of_address(addr)), slot, b)
            self.assertEqual(r.given[b][1 - slot], plants[b].h160, b)
            self.assertEqual(r.blocks_done, b)

    def test_a_report_at_the_plant_slot_of_a_swapped_block_is_not_a_prize_claim(self):
        """Raw slot 0 of block 2 IS the plant (the prize is at slot 1 there): a hit at raw slot 0 is judged as a
        plant report (the CPU does not confirm it as the plant's seed: VOID), never as a prize hit (FOUND)."""
        import gpu_setperm
        sp, blk = self._blk()
        good = next(i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i))
        addr, m = _addr(sp, good, 0, 0, 0)
        H = gpu_setperm.Hit

        def raw_slot_0(res, lo, hi):
            if lo == 2 * blk:
                res.hits = res.hits + [H(good, 0, 0, 0, 0)]                     # POST-conversion: raw slot 0
        _, r, _, _ = _fake_search(self.D, addr, ub=5, post=raw_slot_0)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 2))
        self.assertIsNone(r.found)
        self.assertIn("does not confirm", r.detail)


class ComboSelftestNoGpu(unittest.TestCase):
    """Fix 2: a known-answer test of every (passphrase, path, key form) of K2/K3, before the first block of every run."""

    def test_an_honest_engine_passes_and_reports_what_it_tested(self):
        import gpu_setperm
        for name, npass, npaths in (("A_bip39_12_r3k1", 3, 2), ("D_electrum_12_wordlist", 1, 1),
                                    ("E_electrum_13_alts", 1, 3), ("J_bip39_12_dups", 1, 1)):
            sp, eng = _selftest_engine(name)
            st = gpu_setperm.combo_selftest(sp, eng, seed=5)
            self.assertEqual((len(sp.passes), len(sp.paths)), (npass, npaths), name)
            self.assertEqual(len(st.survivors), 2, name)
            self.assertEqual(st.combos, npass * npaths * 2, name)
            self.assertEqual(st.known_answers, 2 * npass * npaths * 2, name)
            self.assertGreaterEqual(st.rounds, 2, name)
            self.assertTrue(all(sp.candidate_ok(i) for i in st.survivors), name)
            lo, hi = st.survivors
            self.assertLess(lo, sp.total // 2, name)                             # two different regions of the space
            self.assertGreaterEqual(hi, sp.total // 2, name)

    def test_it_scans_only_one_wide_windows_of_its_own_survivors_and_never_the_prize(self):
        import gpu_setperm
        sp, eng = _selftest_engine("A_bip39_12_r3k1")
        seen = []
        real = eng.k3.set_targets
        eng.k3.set_targets = lambda t: (seen.append(len(t)), real(t))[1]
        eng.set_targets = eng.k3.set_targets
        st = gpu_setperm.combo_selftest(sp, eng, seed=1)
        # per survivor: the main round(s), the slot sweep's rounds, then the slot reach - every one a 1-wide window
        # at that survivor
        per_survivor = (st.rounds + st.sweep_rounds + st.reach_rounds) // len(st.survivors)
        self.assertEqual(eng.k1.runs, [(i, 1) for i in st.survivors for _ in range(per_survivor)])
        self.assertEqual(seen[0], 12)                                            # 3 pass x 2 paths x 2 forms, one round each
        self.assertEqual(seen[:per_survivor][0], 12)
        self.assertEqual(seen[per_survivor], 12)                                 # ... for the second survivor too
        for i, n in enumerate(seen):
            if i % per_survivor == per_survivor - 1:
                self.assertEqual(n, gpu_setperm.PLANTS_PER_BLOCK + gpu_setperm.PRIZE_COPIES, seen)   # the slot reach
            elif i % per_survivor:
                self.assertIn(n, (1, 2), seen)                                    # the sweep: [h, h] / [h]
        self.assertEqual(len(seen), len(st.survivors) * per_survivor)

    def test_survivors_are_deterministic_per_seed_and_the_seed_matters(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["A_bip39_12_r3k1"]))
        a = gpu_setperm.selftest_survivors(sp, 3)
        self.assertEqual(a, gpu_setperm.selftest_survivors(sp, 3))
        self.assertEqual(len({tuple(gpu_setperm.selftest_survivors(sp, k)) for k in range(12)}) > 3, True)
        self.assertTrue(all(sp.candidate_ok(i) for i in a))

    def test_no_survivor_no_test(self):
        import gpu_setperm
        sp, eng = _selftest_engine("A_bip39_12_r3k1")
        sp.candidate_ok = lambda i: False
        called = []
        eng.set_targets = lambda t: called.append(t)
        st = gpu_setperm.combo_selftest(sp, eng, seed=1)
        self.assertEqual((st.survivors, st.known_answers), ([], 0))
        self.assertEqual(called, [])

    def test_one_survivor_when_only_one_half_has_any(self):
        import gpu_setperm
        sp, eng = _selftest_engine("A_bip39_12_r3k1")
        ok = sp.candidate_ok
        sp.candidate_ok = lambda i: ok(i) and i < sp.total // 2
        st = gpu_setperm.combo_selftest(sp, eng, seed=1)
        self.assertEqual(len(st.survivors), 1)
        self.assertEqual(st.known_answers, 12)

    def test_every_kind_of_wrong_answer_is_refused_and_named(self):
        """The CPU K3 stand-in with one fault each: a target slot that is never compared (slot 0: a loop that starts
        at t=1), swapped key forms, swapped paths, swapped passphrases, a lost hit, a doubled report, a wrong
        index."""
        import gpu_setperm
        faults = {
            "slot 0 never compared": (lambda hits: [h for h in hits if h[4] != 0], "target #0"),
            "last slot never compared": (lambda hits: [h for h in hits if h[4] != 11], "target #11"),
            "forms swapped": (lambda hits: [(i, p, q, f ^ 1, t) for i, p, q, f, t in hits], "compressed key"),
            "uncompressed keys never found": (lambda hits: [h for h in hits if h[3] != 1], "uncompressed key"),
            "compressed keys never found": (lambda hits: [h for h in hits if h[3] != 0], "compressed key"),
            "the last path never found": (lambda hits: [h for h in hits if h[2] != 1], "path #1"),
            "the last passphrase never found": (lambda hits: [h for h in hits if h[1] != 2], "passphrase #2"),
            "paths swapped": (lambda hits: [(i, p, q ^ 1, f, t) for i, p, q, f, t in hits], "path #"),
            "passphrases swapped": (lambda hits: [(i, p ^ 1, q, f, t) for i, p, q, f, t in hits], "passphrase #"),
            "one hit lost": (lambda hits: hits[:-1], "missing"),
            "everything reported twice": (lambda hits: hits + hits, "duplicated"),
            "index off by one": (lambda hits: [(i + 1, p, q, f, t) for i, p, q, f, t in hits], "missing"),
            "target index shifted": (lambda hits: [(i, p, q, f, t + 1) for i, p, q, f, t in hits], "missing"),
            "nothing reported": (lambda hits: [], "missing"),
            "an extra hit": (lambda hits: hits + [(hits[0][0], 0, 0, 0, 5)], "unexpected"),
        }
        for name, (transform, needle) in faults.items():
            sp, eng = _selftest_engine("A_bip39_12_r3k1", fault=_k3_fault(transform))
            with self.assertRaises(gpu_setperm.EngineError, msg=name) as cm:
                gpu_setperm.combo_selftest(sp, eng, seed=2)
            self.assertIn("combo self-test", str(cm.exception), name)
            self.assertIn(needle, str(cm.exception), name)

    def test_a_k1_or_pipeline_fault_in_the_window_is_refused(self):
        import gpu_setperm

        def no_survivors(eng):
            real = eng.k1.run
            eng.k1.run = lambda start, count: (lambda r: (r[0], r[1], 0))(real(start, count))
        sp, eng = _selftest_engine("A_bip39_12_r3k1", fault=no_survivors)
        with self.assertRaises(gpu_setperm.EngineError):
            gpu_setperm.combo_selftest(sp, eng, seed=2)

        def counts_only(delta):                    # every hit is right; only the window's own counts are wrong
            def fault(eng):
                real = eng.k1.run
                eng.k1.run = lambda start, count: (lambda r: (r[0] + delta[0], r[1] + delta[1], r[2]))(real(start, count))
            return fault
        for delta in ((1, 0), (0, 1), (-1, 0)):
            sp, eng = _selftest_engine("A_bip39_12_r3k1", fault=counts_only(delta))
            with self.assertRaises(gpu_setperm.EngineError, msg=delta) as cm:
                gpu_setperm.combo_selftest(sp, eng, seed=2)
            self.assertIn("expected 1/1/1/1", str(cm.exception), delta)

    def test_more_targets_than_a_round_holds_are_split_into_rounds(self):
        import gpu_setperm
        text = dict(small_config_texts())["A_bip39_12_r3k1"] + "".join(f"PASS extra{i}\n" for i in range(70))
        sp, eng = _selftest_engine(text)
        self.assertEqual(len(sp.passes), 73)
        seen = []
        real = eng.k3.set_targets
        eng.k3.set_targets = lambda t: (seen.append(len(t)), real(t))[1]
        eng.set_targets = eng.k3.set_targets
        with _main_rounds_only():         # the main rounds only (the sweep has its own tests)
            st = gpu_setperm.combo_selftest(sp, eng, seed=4)
        self.assertEqual(st.combos, 73 * 2 * 2)
        self.assertEqual(st.known_answers, 2 * 73 * 2 * 2)
        self.assertTrue(all(0 < n <= gpu_setperm.SELFTEST_MAX_TARGETS for n in seen), seen)
        self.assertEqual(sum(seen), 2 * 73 * 2 * 2)                            # every combination, once per survivor
        self.assertEqual(st.rounds, len(seen))
        self.assertGreaterEqual(len(seen), 4)

    def test_a_round_never_holds_more_than_the_target_limit_even_with_a_huge_hit_buffer(self):
        import gpu_setperm
        text = dict(small_config_texts())["A_bip39_12_r3k1"] + "".join(f"PASS more{i}\n" for i in range(70))
        sp, eng = _selftest_engine(text, hit_cap=10 ** 6)
        seen = []
        real = eng.k3.set_targets
        eng.k3.set_targets = lambda t: (seen.append(len(t)), real(t))[1]
        eng.set_targets = eng.k3.set_targets
        with _main_rounds_only():         # the main rounds only
            gpu_setperm.combo_selftest(sp, eng, seed=4)
        self.assertEqual(sorted(set(seen)), [36, 256], seen)                     # 292 = 256 + 36, for each survivor
        self.assertEqual(len(seen), 4)

    def test_rounds_never_expect_more_hits_than_the_hit_buffer_holds(self):
        """A repeated PATH line makes every target match two combinations (two hits): the round is cut so that its
        expected hits fit hit_cap, and a single target that could not fit is a refusal, not an overflow."""
        import gpu_setperm
        text = dict(small_config_texts())["D_electrum_12_wordlist"] + "PATH m/0/0\nPATH m/0/0\n"
        sp, eng = _selftest_engine(text, hit_cap=4)
        counts = []
        real = eng.k3.run
        eng.k3.run = lambda seeds, surv, off, n, npass: (lambda h: (counts.append((len(eng.k3.targets), len(h))), h)[1])(
            real(seeds, surv, off, n, npass))
        with _main_rounds_only():         # the main rounds only
            st = gpu_setperm.combo_selftest(sp, eng, seed=1)
        self.assertEqual(st.known_answers, 2 * 4 * 2)                            # 4 combinations, 2 hits each, 2 survivors
        self.assertEqual([n for _, n in counts], [4, 4, 4, 4])
        self.assertTrue(all(t == 2 for t, _ in counts), counts)
        sp, eng = _selftest_engine(text, hit_cap=1)
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            gpu_setperm.combo_selftest(sp, eng, seed=1)
        self.assertIn("hit buffer", str(cm.exception))
        sp, eng = _selftest_engine(text)                                         # default cap: one round per survivor
        self.assertEqual(gpu_setperm.combo_selftest(sp, eng, seed=1).rounds, 2)
        sp, eng = _selftest_engine(text, hit_cap=3)                              # the main rounds fit (2 hits per target) ...
        with self.assertRaises(gpu_setperm.EngineError) as cm:                   # ... the sweep's [h, h] (4 hits) does not
            gpu_setperm.combo_selftest(sp, eng, seed=1)
        self.assertIn("slot sweep", str(cm.exception))
        self.assertIn("hit buffer", str(cm.exception))


class RunSelftestNoGpu(unittest.TestCase):
    """The self-test is part of every real run: before the first block, also on a resume, VOID on failure."""

    D = "D_electrum_12_wordlist"

    def test_it_runs_once_before_the_first_block_with_the_plant_seed(self):
        events = []
        _, r, _, blocks = _fake_search(self.D, _rand(), ub=5,
                                       selftest=lambda sp, eng, seed: events.append(("selftest", seed)) or
                                       SimpleNamespace(survivors=[1, 2], combos=2, known_answers=4, rounds=2),
                                       post=lambda res, lo, hi: events.append(("block", lo)))
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        self.assertEqual(events[0], ("selftest", 11))
        self.assertEqual([e[0] for e in events], ["selftest"] + ["block"] * 5)
        self.assertEqual(len(r.selftest_calls), 1)
        self.assertEqual((r.selftest.known_answers, r.selftest.combos), (4, 2))

    def test_it_also_runs_when_a_checkpoint_is_resumed_with_blocks_left(self):
        _, r, _, blocks = _fake_search(self.D, _rand(), ub=5, ck_lines=["done 0 1 1 ok", "done 1 1 1 ok"])
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        self.assertEqual(len(r.selftest_calls), 1)
        self.assertEqual(len(blocks), 3)

    def test_it_is_skipped_when_every_block_is_already_in_the_checkpoint(self):
        _, r, _, blocks = _fake_search(self.D, _rand(), ub=5, ck_lines=[f"done {b} 1 1 ok" for b in range(5)])
        self.assertEqual((r.status, r.exit_code, blocks), ("NO_MATCH", 0, []))
        self.assertEqual(r.selftest_calls, [])
        self.assertIn("not run", r.detail)

    def test_a_failed_self_test_is_void_before_any_block_is_scanned(self):
        import gpu_setperm

        def fail(space, engine, seed):
            raise gpu_setperm.EngineError("combo self-test failed at survivor index 5: 1 of 4 expected hit(s) missing "
                                          "- target #0: passphrase #0 '', path #0 m/0/0, compressed key")
        ck = os.path.join(tempfile.mkdtemp(), "ck")
        _, r, _, blocks = _fake_search(self.D, _rand(), ub=5, selftest=fail, ck=ck)
        self.assertEqual((r.status, r.exit_code, r.blocks_done, blocks), ("VOID", 4, 0, []))
        self.assertIn("combo self-test failed", r.detail)
        self.assertIn("target #0", r.detail)
        with open(ck, encoding="utf-8") as f:
            self.assertFalse([line for line in f if line.startswith("done ")])
        self.assertEqual([x["status"] for x in _ledger(r)], ["START", "VOID"])

    def test_any_other_exception_in_the_self_test_is_void_too(self):
        for exc in (KeyError("k"), RuntimeError("hit buffer overflow"), MemoryError()):
            def boom(space, engine, seed, exc=exc):
                raise exc
            _, r, _, blocks = _fake_search(self.D, _rand(), ub=5, selftest=boom)
            self.assertEqual((r.status, r.exit_code, blocks), ("VOID", 4, []), exc)
            self.assertIn("combo self-test", r.detail)
            self.assertIn(type(exc).__name__, r.detail)

    def test_the_real_self_test_on_the_cpu_engine_gives_a_verified_no_match_and_says_so(self):
        text = dict(small_config_texts())["A_bip39_12_r3k1"]
        lines = []
        _, r, made = _cpu_run(text, _rand(), log=lines.append)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        self.assertEqual((len(r.selftest.survivors), r.selftest.combos, r.selftest.known_answers), (2, 12, 24))
        self.assertIn("24 known answers", r.detail)
        self.assertTrue(any("combo self-test passed" in str(x) and "24 known answers" in str(x) for x in lines))
        first = made[-1].k1.runs[:2]
        self.assertTrue(all(c == 1 for _, c in first), first)                    # the two 1-wide windows come first

    def test_a_k3_fault_confined_to_target_slot_0_is_void_before_block_0_on_a_real_run(self):
        """The reviewers' mutant: a kernel loop starting at t=1. The plants alone missed it (40/40 NO_MATCH)."""
        text = dict(small_config_texts())["J_bip39_12_dups"]
        drop0 = _k3_fault(lambda hits: [h for h in hits if h[4] != 0])
        _, r, made = _cpu_run(text, _rand(), fault=drop0)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0))
        self.assertIn("combo self-test", r.detail)
        self.assertIn("target #0", r.detail)
        self.assertTrue(all(c == 1 for _, c in made[-1].k1.runs), made[-1].k1.runs)   # no block was scanned

    def test_every_dimension_fault_is_void_on_a_multi_pass_multi_path_run(self):
        text = dict(small_config_texts())["A_bip39_12_r3k1"]
        for name, tf in (("form", lambda hits: [(i, p, q, f ^ 1, t) for i, p, q, f, t in hits]),
                         ("path", lambda hits: [(i, p, q ^ 1, f, t) for i, p, q, f, t in hits]),
                         ("pass", lambda hits: [(i, p ^ 1, q, f, t) for i, p, q, f, t in hits])):
            _, r, _ = _cpu_run(text, _rand(), fault=_k3_fault(tf))
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), name)
            self.assertIn("combo self-test", r.detail, name)


class BinomialNoGpu(unittest.TestCase):
    """Fix 3: statistical plants cannot see a small systematic survivor loss; a per-block binomial check can."""

    def test_the_threshold_is_8_sigma_and_small_blocks_are_not_judged(self):
        from gpu_setperm import BINOM_MIN_TESTED, binomial_problem
        self.assertEqual(BINOM_MIN_TESTED, 10_000)
        r, n = 1 / 256, 1_000_000
        mean, sd = n * r, (n * r * (1 - r)) ** 0.5
        self.assertIsNone(binomial_problem(n, round(mean), r))
        self.assertIsNone(binomial_problem(n, int(mean + 7.9 * sd), r))
        self.assertIsNone(binomial_problem(n, int(mean - 7.9 * sd), r))
        for z in (8.1, -8.1, 20, -20):
            msg = binomial_problem(n, round(mean + z * sd), r)
            self.assertIsNotNone(msg, z)
            self.assertIn("sigma", msg)
        self.assertIsNone(binomial_problem(BINOM_MIN_TESTED - 1, 0, r))          # not judged below 10000 tested ...
        self.assertIsNone(binomial_problem(BINOM_MIN_TESTED - 1, BINOM_MIN_TESTED - 1, r))
        self.assertIsNotNone(binomial_problem(BINOM_MIN_TESTED, BINOM_MIN_TESTED, r))    # ... and judged from there
        self.assertIsNotNone(binomial_problem(BINOM_MIN_TESTED, 300, r))

    def test_the_rates_are_the_schemes_pass_rates(self):
        from sp_space import Space
        for name, rate in (("A_bip39_12_r3k1", 1 / 16), ("C_bip39_15_wordlist", 1 / 32), ("D_electrum_12_wordlist", 1 / 256),
                           ("E_electrum_13_alts", 1 / 256)):
            self.assertEqual(Space(cfg_from_text(dict(small_config_texts())[name])).estimate()["valid_rate"], rate, name)

    def test_a_bip39_block_is_judged_at_its_own_rate(self):
        """C is bip39-15 (rate 1/32): 12,288 candidates. 1/32 of them is honest; 1/256 or 1/16 of them is not."""
        for div, void in ((32, False), (256, True), (16, True), (31, False), (33, False)):
            _, r, _, _ = _fake_search("C_bip39_15_wordlist", _rand(), size=lambda lo, hi, div=div: (hi - lo, (hi - lo) // div))
            self.assertEqual((r.status, r.exit_code), ("VOID", 4) if void else ("NO_MATCH", 0), (div, r.detail))
            if void:
                self.assertIn("binomial", r.detail, div)

    def _e4(self):
        with open(os.path.join(HERE, E4), encoding="utf-8") as f:
            return f.read()

    def test_a_systematic_survivor_loss_or_excess_on_e4_shaped_blocks_is_void(self):
        """e4 blocks hold ~4e6 survivors (sigma ~2000, 8 sigma ~0.4%): 0.5% and 5% too few or too many are refused
        in block 0; an honest count, and one 0.2% off (4 sigma), pass."""
        text = self._e4()
        size_of = lambda delta: (lambda lo, hi: (hi - lo, int(round((hi - lo) / 256 * (1 + delta)))))   # noqa: E731
        for delta, void in ((0.0, False), (0.002, False), (-0.002, False), (0.005, True), (-0.005, True), (0.05, True),
                            (-0.05, True), (1.0, True), (-1.0, True)):
            _, r, plants, blocks = _fake_search(text=text, prize=_rand(), max_blocks=2, size=size_of(delta))
            if void:
                self.assertEqual((r.status, r.exit_code, r.blocks_done, len(blocks)), ("VOID", 4, 0, 1), delta)
                self.assertIn("binomial", r.detail, delta)
                self.assertIn("block 0", r.detail, delta)
            else:
                self.assertEqual((r.status, r.blocks_done), ("STOPPED", 2), delta)

    def test_a_block_below_10000_tested_is_not_judged_and_an_honest_small_space_passes(self):
        """B has DISTINCT, so its `tested` is free to differ from `scanned` (without DISTINCT it may not)."""
        _, r, _, _ = _fake_search("B_bip39_12_distinct_alts", _rand(), size=lambda lo, hi: (9_999, 0))
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        _, r, _, _ = _fake_search("B_bip39_12_distinct_alts", _rand(), size=lambda lo, hi: (100_000, 0))
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0))
        self.assertIn("binomial", r.detail)

    def test_the_check_comes_after_a_confirmed_prize_hit(self):
        """A confirmed prize hit is truth even in a block whose survivor count is implausible."""
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        good = next(i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i))
        addr, m = _addr(sp, good, 0, 0, 0)
        _, r, _, _ = _fake_search("D_electrum_12_wordlist", addr, ub=5, size=lambda lo, hi: (10 ** 6, 0),
                                  edit=lambda hits, lo, hi: hits + [gpu_setperm.Hit(good, 0, 0, 0, 0)])
        self.assertEqual((r.status, r.found["mnemonic"]), ("FOUND", m))


class SliceRecordNoGpu(unittest.TestCase):
    """Fix 4: the processed==valid accounting used scan_block's own loop variables, so a consumer-side fault (K2 or K3
    handed off+1 or nsurv-1) gave a false NO_MATCH. Now both wrappers record what they RECEIVED and scan_block compares."""

    def _engine(self, k2_wrap=None, k3_wrap=None, npass=2):
        import gpu_setperm
        eng = _fake_clock_engine(npass, lambda start, count: (count, count, 23), lambda surv, off, n: None,
                                 gpu_setperm.Sizer(100, lo=100, hi=100), gpu_setperm.Sizer(7, lo=7, hi=7), [0.0])
        if k2_wrap:
            real = eng.k2.launch
            eng.k2.launch = lambda surv, off, n: real(surv, *k2_wrap(off, n))
        if k3_wrap:
            real3 = eng.k3.run
            eng.k3.run = lambda seeds, surv, off, n, npass_: real3(seeds, surv, *k3_wrap(off, n), npass_)
        return eng

    def test_an_honest_engine_passes(self):
        res = self._engine().scan_block(0, 300)
        self.assertEqual((res.valid, res.processed), (69, 69))

    def test_a_consumer_side_shift_of_the_slice_is_refused_in_k2_only_k3_only_or_both(self):
        import gpu_setperm
        shifts = {"off+1": lambda off, n: (off + 1, n), "nsurv-1": lambda off, n: (off, n - 1),
                  "off+1 and nsurv-1": lambda off, n: (off + 1, n - 1)}
        for sname, shift in shifts.items():
            for who, kw in (("K2 only", dict(k2_wrap=shift)), ("K3 only", dict(k3_wrap=shift)),
                            ("both", dict(k2_wrap=shift, k3_wrap=shift))):
                with self.assertRaises(gpu_setperm.EngineError, msg=(sname, who)) as cm:
                    self._engine(**kw).scan_block(0, 300)
                self.assertIn("slice mismatch", str(cm.exception), (sname, who))

    def test_a_shifted_slice_leaves_the_hits_gathered_so_far_for_the_caller(self):
        """The hits of the batch that failed the slice check are in `partial` too: the CPU judges them, not this code."""
        import gpu_setperm
        eng = _fake_clock_engine(2, lambda start, count: (count, count, 23), lambda surv, off, n: None,
                                 gpu_setperm.Sizer(100, lo=100, hi=100), gpu_setperm.Sizer(7, lo=7, hi=7), [0.0],
                                 k3_run=lambda seeds, surv, off, n, npass: [(500 + off, 0, 0, 0, 0)])
        real3 = eng.k3.run
        eng.k3.run = lambda seeds, surv, off, n, npass_: real3(seeds, surv, off, n - 1, npass_)
        with self.assertRaises(gpu_setperm.EngineError):
            eng.scan_block(0, 300)
        self.assertIsInstance(eng.partial, gpu_setperm.BlockResult)
        self.assertEqual([h.idx for h in eng.partial.hits], [500])                 # the first batch's hit was kept

    def test_the_run_is_void_never_no_match_on_a_real_run(self):
        """The reviewers' mutants against run_search with the CPU engine (whose K2 refuses a slice past its buffer,
        so a few of these die of that instead: VOID either way)."""
        text = dict(small_config_texts())["J_bip39_12_dups"]
        _, r, _ = _cpu_run(text, _rand(), chunk=101, batch=2)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)       # the control
        cases = {"nsurv-1 in K2 only": ("k2", lambda off, n: (off, n - 1)), "nsurv-1 in K3 only": ("k3", lambda off, n: (off, n - 1)),
                 "nsurv-1 in both": ("both", lambda off, n: (off, n - 1)), "off+1 in K3 only": ("k3", lambda off, n: (off + 1, n)),
                 "off+1 in both": ("both", lambda off, n: (off + 1, n)), "off+1 in K2 only": ("k2", lambda off, n: (off + 1, n))}
        for name, (who, shift) in cases.items():
            def fault(eng, who=who, shift=shift):
                if who in ("k2", "both"):
                    real = eng.k2.launch
                    eng.k2.launch = lambda surv, off, n: real(surv, *shift(off, n))
                if who in ("k3", "both"):
                    real3 = eng.k3.run
                    eng.k3.run = lambda seeds, surv, off, n, npass: real3(seeds, surv, *shift(off, n), npass)
            _, r, _ = _cpu_run(text, _rand(), chunk=101, batch=2, fault=fault)
            self.assertEqual((r.status, r.exit_code), ("VOID", 4), name)


class IndexSumNoGpu(unittest.TestCase):
    """Fix 5 (host side): the whole block's index sums are compared with run_search's own [lo, hi)."""

    def test_a_chunk_sum_that_is_not_the_sum_of_its_range_is_refused(self):
        import gpu_setperm
        from sp_k1_enum import expected_idx_sum
        for delta in (1, -1, 1 << 32, 100, -(1 << 40)):
            eng = _fake_clock_engine(1, lambda s, c: (c, c, 0), lambda *a: None, gpu_setperm.Sizer(100, lo=100, hi=100),
                                     gpu_setperm.Sizer(8), [0.0])
            real = eng.k1.run

            def run(start, count, real=real, delta=delta, eng=eng):
                out = real(start, count)
                eng.k1.last_idx_sum = (eng.k1.last_idx_sum + delta) % 2 ** 64
                return out
            eng.k1.run = run
            with self.assertRaises(gpu_setperm.EngineError, msg=delta) as cm:
                eng.scan_block(0, 300)
            self.assertIn("index checksum", str(cm.exception))
        eng = _fake_clock_engine(1, lambda s, c: (c, c, 0), lambda *a: None, gpu_setperm.Sizer(100, lo=100, hi=100),
                                 gpu_setperm.Sizer(8), [0.0])
        eng.k1.run = (lambda real: lambda s, c: (real(s, c), setattr(eng.k1, "last_idx_sum", None))[0])(eng.k1.run)
        with self.assertRaises(gpu_setperm.EngineError):                             # no sum reported at all
            eng.scan_block(0, 300)
        eng = _fake_clock_engine(1, lambda s, c: (c, c, 0), lambda *a: None, gpu_setperm.Sizer(100, lo=100, hi=100),
                                 gpu_setperm.Sizer(8), [0.0])
        res = eng.scan_block(1000, 1300)
        self.assertEqual(res.idx_sum, expected_idx_sum(1000, 300))

    def test_a_block_far_above_2_32_keeps_its_full_64_bit_sum(self):
        import gpu_setperm
        from sp_k1_enum import expected_idx_sum
        for start in (2 ** 32 - 100, 2 ** 33 + 5, 2 ** 40 + 3, 2 ** 62 + 11):
            eng = _fake_clock_engine(1, lambda s, c: (c, c, 0), lambda *a: None, gpu_setperm.Sizer(100, lo=100, hi=100),
                                     gpu_setperm.Sizer(8), [0.0])
            res = eng.scan_block(start, start + 300)
            self.assertEqual(res.idx_sum, expected_idx_sum(start, 300), start)
            self.assertEqual(res.idx_sum, sum(range(start, start + 300)) % 2 ** 64, start)
        self.assertGreater(expected_idx_sum(2 ** 40 + 3, 300), 2 ** 32)

    def test_a_confirmed_prize_hit_is_found_even_if_the_index_sum_is_wrong(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        good = next(i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i))
        addr, m = _addr(sp, good, 0, 0, 0)
        _, r, _, _ = _fake_search("D_electrum_12_wordlist", addr, ub=5,
                                  edit=lambda hits, lo, hi: hits + [gpu_setperm.Hit(good, 0, 0, 0, 0)],
                                  post=lambda res, lo, hi: setattr(res, "idx_sum", res.idx_sum + 1))
        self.assertEqual((r.status, r.found["mnemonic"]), ("FOUND", m))

    def test_the_whole_block_sum_is_compared_with_the_block_asked_for(self):
        """Every chunk honest, but the sums of the chunks do not add up to [start, end)."""
        import gpu_setperm
        real = gpu_setperm.expected_idx_sum
        eng = _fake_clock_engine(1, lambda s, c: (c, c, 0), lambda *a: None, gpu_setperm.Sizer(100, lo=100, hi=100),
                                 gpu_setperm.Sizer(8), [0.0])
        with mock.patch.object(gpu_setperm, "expected_idx_sum", lambda s, c: real(s, c) if c <= 100 else real(s + 1, c)):
            with self.assertRaises(gpu_setperm.EngineError) as cm:
                eng.scan_block(0, 300)
        self.assertIn("add up", str(cm.exception))

    def test_a_consistent_shift_of_the_whole_request_at_the_call_site_is_void(self):
        """scan_block(lo+1, hi+1): every launch and scan_block's own totals are consistent with what it was asked;
        only run_search's comparison with ITS lo/hi can see it."""
        import gpu_setperm
        text = dict(small_config_texts())["J_bip39_12_dups"]
        eng_cls = _cpu_engine(101, 2)
        real_scan = eng_cls.scan_block
        eng_cls.scan_block = lambda self, lo, hi, hb=None: (real_scan(self, lo + 1, hi + 1, hb) if hi - lo > 1 else
                                                            real_scan(self, lo, hi, hb))     # blocks only, not self-test windows
        with mock.patch.object(gpu_setperm, "Engine", eng_cls), \
                mock.patch.object(gpu_setperm, "plan_block_units", lambda space, target_deriv=4e6: 6):
            _, r = _run(text, prize=_rand(), gpu=object())                          # 4 blocks of 72 candidates
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0))
        self.assertIn("block 0", r.detail)
        self.assertIn("index checksum", r.detail)

    def test_shifts_a_32_bit_sum_could_not_see_are_void_at_the_block_level(self):
        import gpu_setperm
        from sp_k1_enum import expected_idx_sum
        for name, sum_of in (("shift of exactly count", lambda lo, n: expected_idx_sum(lo + n, n)),
                             ("shift of 2**32", lambda lo, n: expected_idx_sum(lo + (1 << 32), n)),
                             ("plus 2**32", lambda lo, n: (expected_idx_sum(lo, n) + (1 << 32)) % 2 ** 64),
                             ("off by one candidate", lambda lo, n: expected_idx_sum(lo + 1, n))):
            def post(res, lo, hi, sum_of=sum_of):
                res.idx_sum = sum_of(lo, hi - lo)
            _, r, _, blocks = _fake_search("D_electrum_12_wordlist", _rand(), ub=5, post=post)
            self.assertEqual((r.status, r.exit_code, r.blocks_done, len(blocks)), ("VOID", 4, 0, 1), name)
            self.assertIn("index checksum", r.detail, name)

    def test_the_stand_ins_honest_sum_passes(self):
        _, r, _, _ = _fake_search("D_electrum_12_wordlist", _rand(), ub=5)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)


class PartialHitsNoGpu(unittest.TestCase):
    """Fix 9: if an exception cuts a block short, the hits gathered before it are still judged by the CPU."""

    D = "D_electrum_12_wordlist"

    def _good(self):
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[self.D]))
        ok = [i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i)]
        good = ok[0]
        bad = [i for i in ok[1:] if sp.decode(i) != sp.decode(good)][:2]
        return sp, good, bad

    @staticmethod
    def _raise(res, lo, hi):
        raise RuntimeError("the GPU fell over")

    def test_a_cpu_confirmed_prize_hit_gathered_before_the_exception_is_found(self):
        import gpu_setperm
        sp, good, _ = self._good()
        addr, m = _addr(sp, good, 0, 0, 0)
        H = gpu_setperm.Hit
        _, r, _, _ = _fake_search(self.D, addr, edit=lambda hits, lo, hi: hits + [H(good, 0, 0, 0, 0)], post=self._raise)
        self.assertEqual((r.status, r.exit_code), ("FOUND", 2), r.detail)
        self.assertEqual((r.found["mnemonic"], r.found["index"]), (m, good))
        rec = _ledger(r)
        self.assertEqual([x["status"] for x in rec], ["START", "FOUND"])
        self.assertIn("RuntimeError", rec[1]["after_error"])
        with open(r.found_path, encoding="utf-8") as f:
            self.assertIn(m, f.read())

    def test_the_same_in_an_odd_block_where_the_prize_sits_at_slot_1(self):
        import gpu_setperm
        sp, good, _ = self._good()
        addr, m = _addr(sp, good, 0, 0, 0)
        blk = 5 * sp.per_unit
        H = gpu_setperm.Hit
        _, r, _, _ = _fake_search(self.D, addr, ub=5, edit=lambda hits, lo, hi: hits + [H(good, 0, 0, 0, 0)] if lo == blk else hits,
                                  post=lambda res, lo, hi: self._raise(res, lo, hi) if lo == blk else None)
        self.assertEqual((r.status, r.found["mnemonic"], r.blocks_done), ("FOUND", m, 1))

    def test_an_unconfirmed_prize_report_before_the_exception_is_void_and_logged(self):
        import gpu_setperm
        sp, good, (bad, _) = self._good()
        lines = []
        H = gpu_setperm.Hit
        _, r, _, _ = _fake_search(self.D, _rand(), edit=lambda hits, lo, hi: hits + [H(bad, 0, 0, 0, 0, at=(1, 2, 3, 4))],
                                  post=self._raise, log=lines.append)
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIsNone(r.found)
        self.assertFalse(os.path.exists(r.found_path))
        self.assertIn("does not confirm", r.detail)
        self.assertIn("RuntimeError", r.detail)
        rec = [x for x in _ledger(r) if x["status"] == "VOID"][0]
        self.assertEqual([(p["idx"], p["cpu_confirmed"]) for p in rec["prize_hits"]], [(bad, False)])
        self.assertNotIn(" ".join(sp.decode(bad)), "\n".join(map(str, lines)) + json.dumps(rec))

    def test_only_plant_hits_before_the_exception_is_void_with_the_partial_hits_logged_as_indices(self):
        sp, good, _ = self._good()
        lines = []
        _, r, plants, _ = _fake_search(self.D, _rand(), post=self._raise, log=lines.append)
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        self.assertIn("engine error in block 0", r.detail)
        self.assertIn("RuntimeError", r.detail)
        rec = [x for x in _ledger(r) if x["status"] == "VOID"][0]
        p = plants[0]
        self.assertEqual([(h["idx"], h["tgt_i"], h["pass_i"], h["path_i"], h["form"]) for h in rec["partial_hits"]],
                         [(p.idx, 1, p.pass_i, p.path_i, p.form)])            # in the run's own target meaning
        text = "\n".join(map(str, lines))
        self.assertIn(f'"idx": {p.idx}', text)
        self.assertNotIn(" ".join(sp.decode(p.idx)), text + json.dumps(rec))     # indices only, never a mnemonic

    def test_no_partial_hits_gives_a_plain_engine_error(self):
        _, r, _, _ = _fake_search(self.D, _rand(), edit=lambda hits, lo, hi: [], post=self._raise)
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))
        rec = [x for x in _ledger(r) if x["status"] == "VOID"][0]
        self.assertEqual(rec["partial_hits"], [])

    def test_a_malformed_partial_report_never_turns_the_void_into_a_traceback(self):
        import gpu_setperm
        H = gpu_setperm.Hit

        def post(res, lo, hi):
            res.hits = res.hits + [H("x", None, 1.5, -1, "y"), object(), None]
            raise RuntimeError("boom")
        _, r, _, _ = _fake_search(self.D, _rand(), post=post)
        self.assertEqual((r.status, r.exit_code), ("VOID", 4))

    def test_the_hits_of_an_earlier_block_are_not_re_judged_after_a_later_setup_failure(self):
        """`eng.partial` is reset before every block: an exception before scan_block starts has no partial hits (the
        plant hit block 0 left in it must not be logged again as block 1's)."""
        import gpu_setperm
        state = {"blocks": 0}

        def fault(eng):
            real_scan, real_set = eng.scan_block, eng.set_targets

            def scan(lo, hi, hb=None):
                res = real_scan(lo, hi, hb)
                state["blocks"] += hi - lo > 1                                   # not a combo self-test window
                return res

            def set_targets(targets):
                if state["blocks"] == 1:
                    raise OSError("device lost")
                real_set(targets)
            eng.scan_block, eng.set_targets = scan, set_targets
        with mock.patch.object(gpu_setperm, "plan_block_units", lambda space, target_deriv=4e6: 6):
            _, r, made = _cpu_run(dict(small_config_texts())[self.D], _rand(), chunk=4096, fault=fault)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 1))
        self.assertIn("OSError", r.detail)
        self.assertIn("block 1", r.detail)
        rec = [x for x in _ledger(r) if x["status"] == "VOID"][0]
        self.assertEqual(rec["partial_hits"], [])

    def test_scan_block_keeps_the_hits_of_the_batches_before_the_one_that_raised(self):
        import gpu_setperm
        n = [0]

        def k3_run(seeds, surv, off, nsurv, npass):
            n[0] += 1
            if n[0] == 3:
                raise RuntimeError("hit buffer overflow")
            return [(1000 + off, 0, 0, 0, 0)]
        eng = _fake_clock_engine(1, lambda s, c: (c, c, 10), lambda *a: None, gpu_setperm.Sizer(100, lo=100, hi=100),
                                 gpu_setperm.Sizer(4, lo=4, hi=4), [0.0], k3_run=k3_run)
        with self.assertRaises(RuntimeError):
            eng.scan_block(0, 100)
        self.assertEqual([h.idx for h in eng.partial.hits], [1000, 1004])         # batches 1 and 2 of the chunk


class RobustnessNoGpu(unittest.TestCase):
    """Fix 8: hits post-processed inside the try, OSError as a message, the plant seed logged from the start."""

    D = "D_electrum_12_wordlist"

    def test_a_malformed_block_result_is_void_never_a_traceback(self):
        import gpu_setperm
        for name, bad in (("no _replace", lambda res, lo, hi: setattr(res, "hits", [object()])),
                          ("None", lambda res, lo, hi: setattr(res, "hits", [None])),
                          ("tuple", lambda res, lo, hi: setattr(res, "hits", [(1, 2, 3, 4, 1)])),
                          ("hits not iterable", lambda res, lo, hi: setattr(res, "hits", 7))):
            _, r, _, _ = _fake_search(self.D, _rand(), post=bad)
            self.assertEqual((r.status, r.exit_code), ("VOID", 4), name)
            self.assertIn("engine error in block 0", r.detail, name)

    def test_the_plant_seed_is_logged_at_the_start_and_in_the_ledger_with_and_without_a_checkpoint(self):
        for ck in (None, os.path.join(tempfile.mkdtemp(), "ck")):
            lines = []
            _, r, _, _ = _fake_search(self.D, _rand(), ub=5, ck=ck, log=lines.append)
            self.assertEqual(r.plant_seed, 11)
            first = [i for i, x in enumerate(lines) if "plant seed 11" in str(x)]
            self.assertEqual(len(first), 1)
            self.assertIn("--plant-seed 11", str(lines[first[0]]))
            rec = _ledger(r)
            self.assertEqual(rec[0]["status"], "START")
            self.assertEqual((rec[0]["plant_seed"], rec[0]["blocks"], rec[0]["blocks_from_checkpoint"]), (11, 5, 0))

    def test_the_seed_is_logged_before_a_void_and_a_resume_records_what_it_took_from_the_checkpoint(self):
        lines = []
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, ck_lines=["done 0 1 1 ok", "done 1 1 1 ok"], log=lines.append,
                                  edit=lambda hits, lo, hi: [])
        self.assertEqual((r.status, r.blocks_done), ("VOID", 2))
        text = "\n".join(map(str, lines))
        self.assertLess(text.index("plant seed 11"), text.index("VOID:"))
        rec = _ledger(r)
        self.assertEqual([x["status"] for x in rec], ["START", "VOID"])
        self.assertEqual(rec[0]["blocks_from_checkpoint"], 2)

    def test_main_turns_os_errors_into_a_message_and_exit_1(self):
        import gpu_setperm
        with tempfile.TemporaryDirectory() as d:
            missing_dir = os.path.join(d, "no", "such", "dir", "ck")
            for path in (d, missing_dir):                                          # a directory; a directory that is missing
                fake = SimpleNamespace(Gpu=SimpleNamespace(get=lambda: object()))
                # main() below is a NON-dry run: it must not carry the real prize address (run_search's default) even
                # though the checkpoint path fails before any engine is built - a self-generated address instead
                with mock.patch.dict(gpu_setperm.run_search.__kwdefaults__, {"prize_addr": _rand()}), \
                        mock.patch.dict(sys.modules, {"sp_gpu": fake}), \
                        mock.patch.object(gpu_setperm, "probe_rate", lambda space, gpu, log: 1e9), \
                        contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(SystemExit) as cm:
                        gpu_setperm.main([os.path.join(HERE, E4), "--checkpoint", path, "--max-hours", "168",
                                          "--signoff", SIGNOFF])
                self.assertIsInstance(cm.exception.code, str, path)                  # sys.exit(message): status 1
                self.assertIn("cannot run", cm.exception.code)
            self.assertEqual(os.listdir(d), [])
            with self.assertRaises(SystemExit) as cm:
                gpu_setperm.main([os.path.join(d, "missing.conf"), "--dry-run"])
            self.assertIsInstance(cm.exception.code, str)
            self.assertIn("cannot read the config", cm.exception.code)

    def test_a_ledger_that_cannot_be_written_never_decides_a_result(self):
        d = tempfile.mkdtemp()
        blocker = os.path.join(d, "f")
        with open(blocker, "w") as fh:
            fh.write("x")
        lines = []
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, ledger=os.path.join(blocker, "runs.jsonl"), log=lines.append)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        self.assertTrue(any("COULD NOT WRITE the ledger" in str(x) for x in lines))


class GuardCapsNoGpu(unittest.TestCase):
    """Fix 7: --max-hours is capped at one week; an assumed rate is for a dry run only."""

    def test_max_hours_above_168_is_refused_by_run_search_and_the_cli(self):
        import gpu_setperm
        from sp_space import Space
        self.assertEqual(gpu_setperm.MAX_HOURS, 168)
        sp = Space.load(E4)

        def boom(*a, **k):
            raise AssertionError("must be refused before any engine or probe")
        with mock.patch.object(gpu_setperm.Engine, "__init__", boom), mock.patch.object(gpu_setperm, "probe_rate", boom):
            for hours in (168.0001, 169, 1000, 1e9):
                for kw in (dict(dry_run=True, assume_rate=300_000), dict(dry_run=True, assume_rate=300_000, force=True),
                           dict(dry_run=True)):
                    with self.assertRaises(ValueError, msg=(hours, kw)):
                        gpu_setperm.run_search(sp, prize_addr=_rand(), max_hours=hours, log=QUIET, **kw)
            for hours in (168, 168.0, 100, 0.5):
                r = gpu_setperm.run_search(sp, prize_addr=_rand(), max_hours=hours, dry_run=True, assume_rate=300_000,
                                           log=QUIET)
                self.assertEqual(r.status, "DRY_RUN", hours)
        with contextlib.redirect_stderr(io.StringIO()):
            for argv in ([E4, "--dry-run", "--max-hours", "169"], [E4, "--max-hours", "169", "--force"],
                         [E4, "--checkpoint", "x", "--max-hours", "1e9"], [E4, "--dry-run", "--max-hours", "168.5"]):
                with self.assertRaises(SystemExit, msg=argv) as cm:
                    gpu_setperm.parse_args(argv)
                self.assertEqual(cm.exception.code, 64, argv)
            self.assertEqual(gpu_setperm.parse_args([E4, "--dry-run", "--max-hours", "168"]).max_hours, 168.0)

    def test_the_cli_exits_64_over_the_cap(self):
        import subprocess
        code = "import sys; sys.modules['pyopencl'] = None; import gpu_setperm; gpu_setperm.main()"
        for args, want in ((["--max-hours", "169"], 64), (["--max-hours", "169", "--force"], 64), (["--max-hours", "168"], 0)):
            p = subprocess.run([sys.executable, "-c", code, E4, "--dry-run", *args], cwd=HERE, capture_output=True,
                               text=True, timeout=300)
            self.assertEqual(p.returncode, want, (args, p.stderr[-300:]))

    def test_an_assumed_rate_outside_a_dry_run_is_refused_before_any_engine_or_probe(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space.load(E4)

        def boom(*a, **k):
            raise AssertionError("must be refused before any engine or probe")
        with tempfile.TemporaryDirectory() as d, mock.patch.object(gpu_setperm.Engine, "__init__", boom), \
                mock.patch.object(gpu_setperm, "probe_rate", boom):
            for kw in (dict(assume_rate=1e9), dict(assume_rate=300_000, max_hours=0.1), dict(assume_rate=1e9, force=True),
                       dict(assume_rate=1e9, dry_run=False)):
                with self.assertRaises(ValueError, msg=kw) as cm:
                    gpu_setperm.run_search(sp, prize_addr=_rand(), log=QUIET, checkpoint=os.path.join(d, "ck"),
                                           ledger=os.path.join(d, "l"), found_path=os.path.join(d, "F"), **kw)
                self.assertIn("dry_run", str(cm.exception))
            self.assertEqual(os.listdir(d), [])

    def test_no_test_hands_run_search_an_assumed_rate_outside_a_dry_run(self):
        """_run / _fake_search / _cpu_run patch gpu_setperm.probe_rate for a fast rate; static check that every
        run_search(...) call in this file that names assume_rate also names dry_run."""
        with open(os.path.join(HERE, "sp_test_run.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        calls = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if name == "run_search":
                    calls += 1
                    names = {k.arg for k in node.keywords}
                    if "assume_rate" in names:
                        self.assertIn("dry_run", names, ast.dump(node)[:160])
        self.assertGreater(calls, 5)


class BannerNoGpu(unittest.TestCase):
    """Fix 10: the NO MATCH banner says what was, and what was not, checked."""

    D = "D_electrum_12_wordlist"

    def test_the_banner_names_every_control_and_the_trusted_checkpoint(self):
        lines = []
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, ck_lines=["done 0 1 1 ok", "done 1 1 1 ok"], log=lines.append)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        self.assertEqual((r.blocks_verified, r.blocks_from_checkpoint), (3, 2))
        for needle in ("5 blocks", "3 verified in this session", "2 taken from the checkpoint on trust",
                       "hand-edited checkpoint is trusted", "combo self-test", "known answers", "own plants found in place", "planted seeds in this session",
                       "target slots rotated", "binomial survivor-count check", "exact 64-bit K1 index sum",
                       "survivor slices checked", "coverage exact"):
            self.assertIn(needle, r.detail, needle)
        banner = [str(x) for x in lines if "NO MATCH (verified)" in str(x)]
        self.assertEqual(len(banner), 1)
        self.assertIn("3 verified in this session", banner[0])
        rest = "\n".join(str(x) for x in lines[lines.index(banner[0]) + 1:])
        self.assertIn("not re-checked at run time", rest)
        self.assertIn("K1 survivor set", rest)
        self.assertIn("statistical controls", rest)

    def test_a_fresh_run_and_a_fully_checkpointed_one_are_told_apart(self):
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5)
        self.assertIn("5 verified in this session, 0 taken from the checkpoint", r.detail)
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, ck_lines=[f"done {b} 1 1 ok" for b in range(5)])
        self.assertIn("0 verified in this session, 5 taken from the checkpoint", r.detail)
        self.assertIn("combo self-test: not run", r.detail)
        self.assertEqual((r.blocks_verified, r.blocks_from_checkpoint), (0, 5))
        # ... and the log header must not claim a verification that did not happen in this session
        lines = []
        _fake_search(self.D, _rand(), ub=5, ck_lines=[f"done {b} 1 1 ok" for b in range(5)], log=lines.append)
        self.assertFalse(any(str(x).startswith("NO MATCH (verified)") for x in lines), lines)
        self.assertTrue(any(str(x).startswith("NO MATCH (trusted from the checkpoint") for x in lines), lines)

    def test_the_ledger_records_the_split(self):
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, ck_lines=["done 0 1 1 ok"])
        rec = [x for x in _ledger(r) if x["status"] == "NO_MATCH"][0]
        self.assertEqual((rec["blocks_verified"], rec["blocks_from_checkpoint"], rec["blocks"]), (4, 1, 5))

    def test_a_dry_run_says_so_on_its_status_line_and_a_no_match_says_verified(self):
        import gpu_setperm
        from sp_space import Space
        lines = []
        r = gpu_setperm.run_search(Space.load(E4), prize_addr=_rand(), dry_run=True, assume_rate=300_000, log=lines.append)
        self.assertEqual((r.status, r.exit_code), ("DRY_RUN", 0))
        self.assertTrue(any(str(x).startswith("DRY RUN") and "NOT a NO MATCH" in str(x) for x in lines), lines)
        self.assertFalse(any("NO MATCH (verified)" in str(x) for x in lines))
        lines = []
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, log=lines.append)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0))
        self.assertTrue(any(str(x).startswith("NO MATCH (verified)") for x in lines))
        self.assertFalse(any(str(x).startswith("DRY RUN") for x in lines))

    def test_the_readme_and_module_docstring_state_the_residual_risks(self):
        import gpu_setperm
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        low = readme.lower()
        for needle in ("residual risks", "k1 survivor set", "sp_test_k1", "statistical controls", "forged or hand-edited",
                       "trusts its own checkpoint", "--dry-run` and no match both exit 0", "status line", "dry run (estimate only)", "168",
                       "combo self-test", "binomial", "64-bit index sum", "takes every slot in turn", "64 plants"):
            self.assertTrue(needle in low, needle)                                  # (not assertIn: it would print the file)
        doc = gpu_setperm.__doc__.lower()
        for needle in ("combo self-test", "binomial", "64-bit index sum", "hand-edited checkpoint", "168",
                       "takes every slot in turn", "64 plants"):
            self.assertTrue(needle in doc, needle)


class ChunkBoundaryNoGpu(unittest.TestCase):
    """Fix 11: a K1 chunk of exactly block_size-1 leaves a 1-candidate tail that a `while cur < end-1` loop drops."""

    def test_scan_block_scans_the_one_candidate_tail(self):
        import gpu_setperm
        calls = []
        eng = _fake_clock_engine(1, lambda s, c: (calls.append((s, c)), (c, c, 0))[1], lambda *a: None,
                                 gpu_setperm.Sizer(99, lo=99, hi=99), gpu_setperm.Sizer(8), [0.0])
        res = eng.scan_block(0, 100)
        self.assertEqual(calls, [(0, 99), (99, 1)])
        self.assertEqual((res.scanned, res.tested), (100, 100))
        for size in (1, 2, 50, 99, 100, 101):                                      # any chunk size covers the block exactly
            calls.clear()
            eng.s1 = gpu_setperm.Sizer(size, lo=size, hi=size)
            res = eng.scan_block(7, 107)
            self.assertEqual(res.scanned, 100, size)
            self.assertEqual(sum(c for _, c in calls), 100, size)
            self.assertEqual(calls[0][0], 7)
            self.assertEqual(calls[-1][0] + calls[-1][1], 107, size)

    def test_a_run_whose_k1_chunk_is_block_size_minus_one_is_exact(self):
        from sp_space import Space
        for name in ("J_bip39_12_dups", "G_bip39_12_k0"):
            text = dict(small_config_texts())[name]
            sp = Space(cfg_from_text(text))
            _, r, made = _cpu_run(text, _rand(), chunk=sp.total - 1, batch=2)
            self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), (name, r.detail))
            tested, valid, _ = sp_ref.ref_survivors(cfg_from_text(text))
            self.assertEqual((r.tested, r.valid), (tested, valid), name)
            self.assertIn((sp.total - 1, 1), made[-1].k1.runs, name)               # the 1-candidate tail chunk really ran

    def test_a_survivor_in_the_one_candidate_tail_is_found(self):
        from sp_space import Space
        text = dict(small_config_texts())["J_bip39_12_dups"]
        sp = Space(cfg_from_text(text))
        last = max(sp_ref.ref_survivors(sp.cfg)[2])
        addr, m = _addr(sp, last, 0, 0, 0)
        _, r, _ = _cpu_run(text, addr, chunk=last, batch=2)                         # chunk [0, last) then the tail [last, ...)
        self.assertEqual((r.status, r.exit_code), ("FOUND", 2), r.detail)
        self.assertIn(last, r.found["indices"])


# ---------------------------------------------------------------- gaps found by the mutation review of the 2nd pass

class MutationReviewGapsNoGpu(unittest.TestCase):
    """Each test kills a mutant of gpu_setperm.py that the rest of the suite let survive (found by mutating the second
    hardening pass): a slice check that only looks at the first batch / first chunk, a block sum compared mod 2**32 in
    scan_block, run_search's own sum compared with a truncated `lo`, a binomial sigma without the (1 - r) factor, a
    real run that never uses its measured rate, and a plant seed logged only after the self-test."""

    def _engine(self):
        """3 K1 chunks of 100 candidates with 23 survivors each; npass 2 and a 7-thread K2 budget make 8 batches of
        3, 3, 3, 3, 3, 3, 3, 2 survivors per chunk: 24 batches in all."""
        import gpu_setperm
        return _fake_clock_engine(2, lambda start, count: (count, count, 23), lambda surv, off, n: None,
                                  gpu_setperm.Sizer(100, lo=100, hi=100), gpu_setperm.Sizer(7, lo=7, hi=7), [0.0])

    def test_a_slice_fault_confined_to_one_later_batch_or_chunk_is_refused(self):
        """The slice check runs for EVERY batch of EVERY chunk. Faults that hit only the second batch of a chunk, its
        last (clipped) batch, the first batch of the second chunk or the very last batch of the block - in K2 only or K3
        only, off+1 or nsurv-1 - are all refused."""
        import gpu_setperm
        calls = []
        eng = self._engine()
        real = eng.k2.launch
        eng.k2.launch = lambda surv, off, n: (calls.append((off, n)), real(surv, off, n))[1]
        eng.scan_block(0, 300)
        self.assertEqual(len(calls), 24)
        self.assertEqual(calls[7], (21, 2))                                       # the clipped last batch of chunk 0
        shifts = {"off+1": lambda off, n: (off + 1, n), "nsurv-1": lambda off, n: (off, n - 1)}
        for k in (1, 7, 8, 23):
            for who in ("K2", "K3"):
                for sname, shift in shifts.items():
                    eng, seen = self._engine(), [-1]

                    def wrap(real, k=k, shift=shift, seen=seen, k3=(who == "K3")):
                        def f(*a):
                            seen[0] += 1
                            if seen[0] != k:
                                return real(*a)
                            head, off, n, tail = (a[:2], a[2], a[3], a[4:]) if k3 else (a[:1], a[1], a[2], ())
                            return real(*head, *shift(off, n), *tail)
                        return f
                    if who == "K2":
                        eng.k2.launch = wrap(eng.k2.launch)
                    else:
                        eng.k3.run = wrap(eng.k3.run)
                    with self.assertRaises(gpu_setperm.EngineError, msg=(k, who, sname)) as cm:
                        eng.scan_block(0, 300)
                    self.assertIn("slice mismatch", str(cm.exception), (k, who, sname))

    def test_a_block_sum_wrong_by_a_multiple_of_2_32_is_refused_by_scan_block(self):
        """Every chunk honest; the block-level expectation is off by 2**32, 2**40 or 3*2**32: a compare of the sums
        mod 2**32 (a uint32 habit) does not see it, the exact compare does."""
        import gpu_setperm
        real = gpu_setperm.expected_idx_sum
        for delta in (1 << 32, 1 << 40, 3 << 32):
            eng = _fake_clock_engine(1, lambda s, c: (c, c, 0), lambda *a: None, gpu_setperm.Sizer(100, lo=100, hi=100),
                                     gpu_setperm.Sizer(8), [0.0])
            with mock.patch.object(gpu_setperm, "expected_idx_sum",
                                   lambda s, c, delta=delta: real(s, c) if c <= 100 else (real(s, c) + delta) % 2 ** 64):
                with self.assertRaises(gpu_setperm.EngineError, msg=delta) as cm:
                    eng.scan_block(0, 300)
            self.assertIn("add up", str(cm.exception), delta)

    def test_run_searchs_own_sum_check_holds_for_blocks_above_2_32(self):
        """e4 has 84.5e9 candidates in blocks of ~1.02e9: block 4 starts below 2**32 and block 5 above it. A run_search
        that compared the sum of [lo, hi) with `lo` cut to 32 bits would VOID an honest run at block 5."""
        with open(os.path.join(HERE, E4), encoding="utf-8") as f:
            text = f.read()
        _, r, _, blocks = _fake_search(text=text, prize=_rand(), max_blocks=6)
        self.assertEqual((r.status, r.exit_code, r.blocks_done, len(blocks)), ("STOPPED", 5, 6, 6), r.detail)
        self.assertLess(blocks[4][0], 2 ** 32)
        self.assertGreater(blocks[5][0], 2 ** 32)

    def test_the_binomial_sigma_includes_the_1_minus_r_factor(self):
        """At r = 1/16 a sigma without (1 - r) is 3.3% too wide: a count 8.03 sigma away is 7.8 'wide' sigma. Judged on
        both sides, at three pass rates, 7.97 sigma passes and 8.03 sigma does not."""
        from gpu_setperm import binomial_problem
        n = 1_000_000
        for r in (1 / 16, 1 / 32, 1 / 256):
            mean, sd = n * r, (n * r * (1 - r)) ** 0.5
            self.assertIsNone(binomial_problem(n, round(mean + 7.97 * sd), r), r)
            self.assertIsNone(binomial_problem(n, round(mean - 7.97 * sd), r), r)
            self.assertIsNotNone(binomial_problem(n, round(mean + 8.03 * sd), r), r)
            self.assertIsNotNone(binomial_problem(n, round(mean - 8.03 * sd), r), r)

    def test_small_configs_big_enough_to_be_judged_pass_with_exact_cpu_counts_and_fail_when_lossy(self):
        """No false VOID on an honest count of any small config with >= 10,000 tested candidates, and a K1 that lost
        every survivor is VOID for each of them (the GPU test of the same name only ever judged configs of >= 100,000
        tested, of which there are none, so its lossy branch never ran)."""
        from gpu_setperm import BINOM_MIN_TESTED, binomial_problem
        from sp_space import Space
        judged = []
        for name, text in small_config_texts():
            cfg = cfg_from_text(text)
            tested, valid, _ = sp_ref.ref_survivors(cfg)
            if tested < BINOM_MIN_TESTED:
                continue
            rate = Space(cfg).estimate()["valid_rate"]
            self.assertIsNone(binomial_problem(tested, valid, rate), name)
            self.assertIsNotNone(binomial_problem(tested, 0, rate), name)
            self.assertIsNotNone(binomial_problem(tested, tested, rate), name)
            judged.append(name)
        self.assertGreaterEqual(len(judged), 3, judged)

    def test_a_real_run_uses_the_rate_it_measured_for_the_banner_and_the_cost_guard(self):
        """run_search takes no assumed rate outside a dry run, so the rate of a real run is the probe's: a slow
        probe refuses an e4 run at the default 1 h (a placeholder rate of 300,000/s would not), and the run's log
        says 'measured'."""
        import gpu_setperm
        from sp_space import Space

        def boom(*a, **k):
            raise AssertionError("refused by the measured rate before any engine")
        with mock.patch.object(gpu_setperm.Engine, "__init__", boom), \
                mock.patch.object(gpu_setperm, "probe_rate", lambda space, gpu, log: 1000.0):
            lines = []
            r = gpu_setperm.run_search(Space.load(E4), prize_addr=_rand(), gpu=object(), log=lines.append,
                                       signoff=SIGNOFF)
        self.assertEqual((r.status, r.exit_code, r.est["rate"]), ("REFUSED", 3, 1000.0))
        self.assertTrue(any("rate 1,000/s (measured)" in str(x) for x in lines), lines[:1])
        lines = []
        _, r, _, _ = _fake_search("D_electrum_12_wordlist", _rand(), ub=5, log=lines.append)     # the stand-in probe: 1e9
        self.assertEqual((r.status, r.est["rate"]), ("NO_MATCH", 1e9))
        self.assertTrue(any("rate 1,000,000,000/s (measured)" in str(x) for x in lines), lines[:1])

    def test_a_block_that_reports_more_or_fewer_scanned_candidates_than_asked_is_void_without_a_gpu(self):
        """run_search's own coverage control (the thread count K1 reported), on the CPU stand-in: the exact index sum
        is left honest, so only the `scanned` compare can see it. (EndToEnd.test_void_when_the_engine_skips_candidates
        needs a GPU; this one holds with no OpenCL.)"""
        for delta in (-1, 1, -100):
            _, r, _, blocks = _fake_search("D_electrum_12_wordlist", _rand(), ub=5,
                                           post=lambda res, lo, hi, d=delta: setattr(res, "scanned", res.scanned + d))
            self.assertEqual((r.status, r.exit_code, r.blocks_done, len(blocks)), ("VOID", 4, 0, 1), delta)
            self.assertIn("scanned", r.detail, delta)

    def test_the_ledger_records_block_size_self_test_answers_and_progress_at_a_void(self):
        from sp_space import Space
        _, r, _, _ = _fake_search("D_electrum_12_wordlist", _rand(), ub=5)
        rec = _ledger(r)
        self.assertEqual([x["status"] for x in rec], ["START", "NO_MATCH"])
        self.assertEqual(rec[0]["block_units"], 5)
        self.assertEqual(rec[1]["selftest_known_answers"], 4)                        # the stand-in self-test's answers
        block = 5 * Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"])).per_unit
        _, r, _, _ = _fake_search("D_electrum_12_wordlist", _rand(), ub=5,
                                  edit=lambda hits, lo, hi: [] if lo == 3 * block else hits)
        void = [x for x in _ledger(r) if x["status"] == "VOID"]
        self.assertEqual(len(void), 1)
        self.assertEqual(void[0]["blocks_done"], 3)                                  # blocks 0..2 finished, block 3 voided
        self.assertIn("block 3", void[0]["reason"])

    def test_a_refused_run_names_the_way_out_and_the_projected_hours(self):
        import gpu_setperm
        from sp_space import Space
        r = gpu_setperm.run_search(Space.load(E4), prize_addr=_rand(), dry_run=True, assume_rate=300_000, max_hours=0.1,
                                   log=QUIET)
        self.assertEqual((r.status, r.exit_code), ("REFUSED", 3))
        self.assertIn("--force", r.detail)
        self.assertIn("--max-hours 0.1", r.detail)

    def test_the_plant_seed_is_logged_before_a_failing_self_test_can_void_the_run(self):
        import gpu_setperm
        lines = []

        def fail(space, engine, seed):
            raise gpu_setperm.EngineError("combo self-test failed at survivor index 5: 1 of 4 expected hit(s) missing")
        _, r, _, blocks = _fake_search("D_electrum_12_wordlist", _rand(), ub=5, selftest=fail, log=lines.append)
        self.assertEqual((r.status, r.exit_code, blocks), ("VOID", 4, []))
        text = "\n".join(map(str, lines))
        self.assertIn("plant seed 11", text)
        self.assertLess(text.index("plant seed 11"), text.index("VOID:"))


# ---------------------------------------------------------------- window audit, slot sweep, tested == scanned, plant placement

def _audit_k1_fault(mode):
    """A `fault(engine)` for the CPU engine: the K1 stand-in's answer to the AUDIT's window (the only K1 launch
    wider than a scan chunk) is corrupted, its answers to the block scans stay exact - so every other control of the
    run holds and only the window audit can see it."""
    def apply(eng):
        real = eng.k1.run

        def run(start, count):
            scanned, tested, valid = real(start, count)
            if count <= eng.s1.n:
                return scanned, tested, valid
            surv = list(eng.k1.surv)
            sset = set(surv)
            other = next(i for i in range(start, start + count) if i not in sset)
            if mode == "lose one":
                eng.k1.surv, valid = surv[:-1], valid - 1
            elif mode == "lose one, valid unchanged":
                eng.k1.surv = surv[:-1]
            elif mode == "gain one":
                eng.k1.surv, valid = surv + [other], valid + 1
            elif mode == "one repeated":
                eng.k1.surv, valid = surv + [surv[0]], valid + 1
            elif mode == "one swapped for a non-survivor":
                eng.k1.surv = surv[:-1] + [other]
            elif mode == "valid lies":
                valid += 1
            else:
                raise AssertionError(mode)
            return scanned, tested, valid
        eng.k1.run = run
    return apply


def _audit_k23_fault(mode, t=0, path_i=0, form=0, byte=0):
    """A `fault(engine)` for the CPU engine that corrupts only what the AUDIT reads: the seeds buffer K2 wrote
    (zeros), the K3 dump (one hash160 flipped), or the slice K2 was handed."""
    def apply(eng):
        if mode == "zero seeds":
            import numpy as np
            eng.gpu = SimpleNamespace(read=lambda buf, dtype, count: np.zeros(count, dtype))
        elif mode == "wrong hash":
            real = eng.k3.dump

            def dump(seeds):
                out = real(seeds).copy()
                tt = min(t, len(out) - 1) if t >= 0 else len(out) + t
                out[tt, path_i, form, byte] ^= 0x01
                return out
            eng.k3.dump = dump
        elif mode == "wrong shape":
            real = eng.k3.dump
            eng.k3.dump = lambda seeds: real(seeds)[:-1]
        elif mode == "slice shifted":
            real = eng._derive_dump
            eng._derive_dump = lambda off, k: real(off - 1 if off else off + 1, k)
        else:
            raise AssertionError(mode)
    return apply


class WindowAuditNoGpu(unittest.TestCase):
    """Engine.k1_spot_check on CPU stand-in kernels: the survivor SET of a pseudo-random window per block and K2/K3
    hash160 parity on a slice of it, against the CPU - the check that the exact K1 survivor set was never checked."""

    A = "A_bip39_12_r3k1"

    def _eng(self, name=None, fault=None, chunk=37, **kw):
        return _selftest_engine(name or self.A, fault=fault, chunk=chunk, **kw)

    def test_an_honest_engine_passes_on_every_small_config_and_reports_its_window(self):
        import gpu_setperm
        for name, text in small_config_texts():
            sp, eng = self._eng(name)
            for seed in (1, 2):
                a = eng.k1_spot_check(0, sp.total, seed)
                w = min(gpu_setperm.SPOT_WIDTH, sp.total)
                self.assertEqual(a.window[1], w, name)
                self.assertTrue(0 <= a.window[0] <= sp.total - w, name)
                want = [i for i in range(a.window[0], a.window[0] + w) if sp.candidate_ok(i)]
                self.assertEqual(a.survivors, len(want), name)
                k = min(len(want), gpu_setperm.SPOT_SURVIVORS)
                self.assertEqual(a.checked, k * len(sp.passes), name)
                self.assertFalse(a.skipped, name)
            self.assertGreater(w, eng.s1.n, name)                    # the stand-in faults key on 'wider than a scan chunk'

    def test_the_window_runs_k1_on_its_own_and_at_most_16_survivors_go_through_k2_and_k3(self):
        import gpu_setperm
        self.assertEqual((gpu_setperm.SPOT_WIDTH, gpu_setperm.SPOT_SURVIVORS), (1 << 15, 16))
        sp, eng = self._eng("C_bip39_15_wordlist")                   # 12,288 candidates, ~384 survivors: window = the block
        a = eng.k1_spot_check(0, sp.total, 3)
        self.assertEqual(eng.k1.runs, [a.window])                     # exactly one K1 launch, over the window
        self.assertEqual(a.window, (0, sp.total))
        self.assertGreater(a.survivors, 100)
        (off, k), = eng.k2.slices                                     # one K2 launch: 16 survivors, from a random offset
        self.assertEqual(k, 16)
        self.assertTrue(0 <= off <= a.survivors - 16)
        self.assertEqual(a.checked, 16)
        offs = set()
        for seed in range(12):
            sp, eng = self._eng("C_bip39_15_wordlist")
            eng.k1_spot_check(0, sp.total, seed)
            offs.add(eng.k2.slices[0][0])
        self.assertGreater(len(offs), 6, offs)                         # a different contiguous slice per seed

    def test_more_survivors_than_candidates_in_the_window_is_a_k1_fault_not_a_skip(self):
        from gpu_setperm import EngineError
        sp, eng = self._eng("C_bip39_15_wordlist")
        real = eng.k1.run

        def liar(start, count):
            scanned, tested, valid = real(start, count)
            return scanned, tested, count + 5                          # cannot survive more candidates than were scanned
        eng.k1.run = liar
        with self.assertRaises(EngineError):
            eng.k1_spot_check(0, sp.total, 1)

    def test_fewer_survivors_than_16_are_all_checked_and_an_empty_window_launches_no_k2(self):
        sp, eng = self._eng("J_bip39_12_dups")                       # 288 raw, ~18 survivors
        a = eng.k1_spot_check(0, sp.total, 1)
        self.assertEqual(a.checked, min(a.survivors, 16))
        sp, eng = self._eng("J_bip39_12_dups")
        sp.candidate_ok = lambda i: False
        a = eng.k1_spot_check(0, sp.total, 1)
        self.assertEqual((a.survivors, a.checked, a.skipped), (0, 0, False))
        self.assertEqual(eng.k2.slices, [])                           # no survivor, no K2 launch, no false alarm

    def test_a_thread_budget_below_16_survivors_shrinks_the_slice_but_never_to_zero(self):
        import gpu_setperm
        text = dict(small_config_texts())[self.A] + "".join(f"PASS more{i}\n" for i in range(70))    # 73 passphrases
        sp, eng = self._eng(text)
        with mock.patch.object(gpu_setperm, "SPOT_MAX_THREADS", 200):
            a = eng.k1_spot_check(0, sp.total, 1)
        self.assertEqual(eng.k2.slices[0][1], 200 // 73)
        self.assertEqual(a.checked, (200 // 73) * 73)
        sp, eng = self._eng(text)
        with mock.patch.object(gpu_setperm, "SPOT_MAX_THREADS", 10):
            eng.k1_spot_check(0, sp.total, 1)
        self.assertEqual(eng.k2.slices[0][1], 1)                      # one whole survivor, all its passphrases
        self.assertEqual(gpu_setperm.SPOT_MAX_THREADS, 1 << 9)        # CPU-time bound: ~2 ms per re-derived thread
        self.assertLessEqual(gpu_setperm.SPOT_MAX_THREADS, gpu_setperm.K2_MAX_THREADS)

    def test_the_window_is_deterministic_per_seed_and_block_and_spread_over_the_block(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space.load(E4)
        sp.candidate_ok = lambda i: False                            # only the window's position is looked at
        eng = _cpu_engine()(sp, [os.urandom(20)])
        lo, hi = 3 * 10 ** 9, 3 * 10 ** 9 + 10 ** 9
        starts = []
        for seed in range(40):
            a = eng.k1_spot_check(lo, hi, seed)
            self.assertEqual(a.window[1], gpu_setperm.SPOT_WIDTH)
            self.assertTrue(lo <= a.window[0] and a.window[0] + a.window[1] <= hi, (seed, a.window))
            starts.append(a.window[0])
            self.assertEqual(eng.k1_spot_check(lo, hi, seed).window, a.window)      # deterministic
        offs = [s - lo for s in starts]
        self.assertGreater(len(set(starts)), 35)                                    # the seed matters
        self.assertGreater(max(offs), 0.7 * (hi - lo))                              # spread over the whole block,
        self.assertLess(min(offs), 0.3 * (hi - lo))                                 # not clustered at either end
        self.assertGreater(sum(o > (hi - lo) // 2 for o in offs), 10)
        self.assertGreater(sum(o < (hi - lo) // 2 for o in offs), 10)
        self.assertNotEqual(eng.k1_spot_check(lo, hi, 5).window[0] - lo, eng.k1_spot_check(hi, hi + hi - lo, 5).window[0] - hi)
        # a block narrower than the window is audited whole
        a = eng.k1_spot_check(1000, 1000 + 5000, 1)
        self.assertEqual(a.window, (1000, 5000))

    def test_it_uses_the_plant_seed_and_the_block_start_exactly_as_documented(self):
        """random.Random(f'{seed}:k1-spot:{lo}'): the window offset is its first draw."""
        import random
        import gpu_setperm
        from sp_space import Space
        sp = Space.load(E4)
        sp.candidate_ok = lambda i: False
        eng = _cpu_engine()(sp, [os.urandom(20)])
        lo, hi = 7 * 10 ** 9, 8 * 10 ** 9
        for seed in (0, 11, 12345):
            want = lo + random.Random(f"{seed}:k1-spot:{lo}").randrange(0, hi - lo - gpu_setperm.SPOT_WIDTH + 1)
            self.assertEqual(eng.k1_spot_check(lo, hi, seed).window[0], want, seed)

    def test_the_window_can_start_at_every_position_including_the_last_one(self):
        """A block d candidates wider than the window admits d + 1 window starts and only the seed picks between them:
        every one must be reachable (an off-by-one in the draw would leave the block's last candidates never audited)."""
        sp, eng = self._eng("D_electrum_12_wordlist")                # 49,152 candidates
        sp.candidate_ok = lambda i: False                            # only the window's position is looked at
        lo, w = 1000, 3000
        for d in (1, 2, 3):
            starts = {eng.k1_spot_check(lo, lo + w + d, seed, width=w).window[0] for seed in range(60)}
            self.assertEqual(starts, set(range(lo, lo + d + 1)), d)

    def test_the_slice_of_survivors_can_start_at_every_offset_including_the_last_one(self):
        """17 survivors, a slice of 16: the K2/K3 slice starts at offset 0 or 1 by the seed alone, both reachable."""
        sp, eng = self._eng("D_electrum_12_wordlist")
        keep = set(range(1000, 1017))
        sp.candidate_ok = lambda i: i in keep
        offs = set()
        for seed in range(16):
            a = eng.k1_spot_check(1000, 4000, seed, width=3000)      # the window is the whole 3000-candidate block
            self.assertEqual((a.window, a.survivors, a.checked), ((1000, 3000), 17, 16))
            self.assertEqual(eng.k2.slices[-1][1], 16)
            offs.add(eng.k2.slices[-1][0])
        self.assertEqual(offs, {0, 1})

    def test_the_audit_leaves_the_blocks_own_state_alone(self):
        """Run between two scans of the same block it changes nothing the block measured: counters, index sum, hits,
        the K3 targets, the launch sizers, the partial result."""
        sp, eng = self._eng()
        targets = [os.urandom(20), os.urandom(20)]
        eng.set_targets(targets)
        before = eng.scan_block(0, sp.total)
        sizes = (eng.s1.n, eng.s2.n)
        partial = eng.partial
        a = eng.k1_spot_check(0, sp.total, 9)
        self.assertGreater(a.survivors, 0)
        self.assertEqual(eng.k3.targets, targets)
        self.assertEqual((eng.s1.n, eng.s2.n), sizes)
        self.assertIs(eng.partial, partial)
        self.assertEqual((before.scanned, before.processed), (sp.total, before.valid))
        again = eng.scan_block(0, sp.total)
        for f in ("scanned", "tested", "valid", "processed", "idx_sum", "hits"):
            self.assertEqual(getattr(again, f), getattr(before, f), f)

    def test_every_kind_of_survivor_set_error_is_refused_and_named(self):
        import gpu_setperm
        for mode in ("lose one", "lose one, valid unchanged", "gain one", "one repeated", "one swapped for a non-survivor",
                     "valid lies"):
            sp, eng = self._eng(fault=_audit_k1_fault(mode))
            with self.assertRaises(gpu_setperm.EngineError, msg=mode) as cm:
                eng.k1_spot_check(0, sp.total, 4)
            self.assertIn("survivor set differs", str(cm.exception), mode)
            self.assertIn("window [", str(cm.exception), mode)
        sp, eng = self._eng(fault=_audit_k1_fault("lose one"))
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            eng.k1_spot_check(0, sp.total, 4)
        self.assertIn("1 missing", str(cm.exception))
        sp, eng = self._eng(fault=_audit_k1_fault("gain one"))
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            eng.k1_spot_check(0, sp.total, 4)
        self.assertIn("1 unexpected", str(cm.exception))
        sp, eng = self._eng(fault=_audit_k1_fault("one repeated"))
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            eng.k1_spot_check(0, sp.total, 4)
        self.assertIn("1 duplicated", str(cm.exception))

    def test_a_k1_that_scans_the_wrong_range_or_reports_the_wrong_index_sum_is_refused(self):
        import gpu_setperm
        sp, eng = self._eng()
        real = eng.k1.run
        eng.k1.run = lambda start, count: (lambda r: (r[0] - 1, r[1], r[2]))(real(start, count))
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            eng.k1_spot_check(0, sp.total, 4)
        self.assertIn("scanned", str(cm.exception))
        sp, eng = self._eng()
        real = eng.k1.run

        def bad_sum(start, count):
            r = real(start, count)
            eng.k1.last_idx_sum += 1
            return r
        eng.k1.run = bad_sum
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            eng.k1_spot_check(0, sp.total, 4)
        self.assertIn("checksum", str(cm.exception))

    def test_wrong_seeds_or_a_wrong_hash160_or_shape_or_slice_from_k2_k3_are_refused_and_named(self):
        import gpu_setperm
        sp, _ = self._eng()
        npass, npaths = len(sp.passes), len(sp.paths)
        self.assertEqual((npass, npaths), (3, 2))
        cases = [("zero seeds", dict(), "K2/K3 parity"), ("wrong hash", dict(), "K2/K3 parity"),
                 ("wrong shape", dict(), "shape"), ("slice shifted", dict(), "slice")]
        for path_i in range(npaths):
            for form in (0, 1):
                cases += [("wrong hash", dict(t=0, path_i=path_i, form=form), "K2/K3 parity"),
                          ("wrong hash", dict(t=-1, path_i=path_i, form=form), "K2/K3 parity")]
        for t in range(3 * npass):                                  # every passphrase, of the first and later survivors
            cases.append(("wrong hash", dict(t=t), "K2/K3 parity"))
        for byte in range(0, 20, 3):                                # a single flipped bit anywhere in the 20 bytes
            cases.append(("wrong hash", dict(t=1, path_i=1, form=1, byte=byte), "K2/K3 parity"))
        cases.append(("wrong hash", dict(t=1, path_i=1, form=1, byte=19), "K2/K3 parity"))
        for mode, kw, needle in cases:
            sp, eng = self._eng(fault=_audit_k23_fault(mode, **kw))
            with self.assertRaises((gpu_setperm.EngineError, ValueError), msg=(mode, kw)) as cm:
                eng.k1_spot_check(0, sp.total, 6)
            self.assertIn(needle, str(cm.exception), (mode, kw))
        # the message names the survivor, the passphrase, the path and the key form that differ
        sp, eng = self._eng(fault=_audit_k23_fault("wrong hash", t=-1, path_i=1, form=1))
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            eng.k1_spot_check(0, sp.total, 6)
        msg = str(cm.exception)
        self.assertIn(f"passphrase #{npass - 1}", msg)
        self.assertIn("path #1", msg)
        self.assertIn("form 1", msg)
        self.assertRegex(msg, r"survivor [\d,]+, passphrase")

    def test_a_window_whose_survivors_overflow_the_buffer_is_skipped_with_a_log_line(self):
        sp, eng = self._eng("C_bip39_15_wordlist")
        lines = []
        eng.log = lines.append
        eng.k1.cap = 8
        a = eng.k1_spot_check(0, sp.total, 3)
        self.assertTrue(a.skipped)
        self.assertEqual(a.checked, 0)
        self.assertEqual(eng.k2.slices, [])
        self.assertTrue(any("window audit skipped" in str(x) and "overflow" in str(x) for x in lines), lines)

    def test_a_tampered_k2_slice_record_in_the_audit_is_refused(self):
        import gpu_setperm
        sp, eng = self._eng()
        real = eng.k2.launch

        def liar(surv, off, nsurv):
            real(surv, off, nsurv)
            eng.k2.last_slice = (off + 1, nsurv)
        eng.k2.launch = liar
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            eng.k1_spot_check(0, sp.total, 3)
        self.assertIn("K2 received the survivor slice", str(cm.exception))


class WindowAuditInRunNoGpu(unittest.TestCase):
    """run_search's use of the audit: once per session block, after the block's own controls, before its `done` line;
    any failure or exception is VOID at that block; a confirmed prize hit still wins."""

    D = "D_electrum_12_wordlist"

    def test_every_session_block_is_audited_once_with_the_plant_seed_and_resumed_blocks_are_not(self):
        _, r, _, blocks = _fake_search(self.D, _rand(), ub=5)
        self.assertEqual(r.spot_calls, [(lo, hi, 11) for lo, hi in blocks])
        _, r, _, blocks = _fake_search(self.D, _rand(), ub=5, ck_lines=["done 0 1 1 ok", "done 1 1 1 ok"])
        self.assertEqual(r.spot_calls, [(lo, hi, 11) for lo, hi in blocks])
        self.assertEqual(len(r.spot_calls), 3)
        _, r, _, blocks = _fake_search(self.D, _rand(), ub=5, ck_lines=[f"done {b} 1 1 ok" for b in range(5)])
        self.assertEqual(r.spot_calls, [])

    def test_the_audit_runs_after_the_blocks_controls_and_before_its_done_line(self):
        ck = os.path.join(tempfile.mkdtemp(), "ck")
        seen = []

        def spot(lo, hi, seed):
            with open(ck, encoding="utf-8") as f:
                seen.append(sum(1 for line in f if line.startswith("done ")))
            return SimpleNamespace(window=(lo, hi - lo), survivors=0, checked=0, skipped=False)
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, ck=ck, spot=spot)
        self.assertEqual(seen, [0, 1, 2, 3, 4])                       # block b is audited while b blocks are done
        # ... and not at all when an earlier control of the block already voided it
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, edit=lambda hits, lo, hi: [])
        self.assertEqual((r.status, r.blocks_done, r.spot_calls), ("VOID", 0, []))
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, post=lambda res, lo, hi: setattr(res, "scanned", 1))
        self.assertEqual((r.status, r.blocks_done, r.spot_calls), ("VOID", 0, []))

    def test_a_failed_audit_is_void_at_its_block_and_that_block_is_not_recorded_done(self):
        import gpu_setperm
        from sp_space import Space
        block = 5 * Space(cfg_from_text(dict(small_config_texts())[self.D])).per_unit
        for k in range(5):
            ck = os.path.join(tempfile.mkdtemp(), "ck")

            def spot(lo, hi, seed, k=k):
                if lo == k * block:
                    raise gpu_setperm.EngineError("K1 survivor set differs from the CPU's on the window [1, 2): 1 missing [7]")
                return SimpleNamespace(window=(lo, hi - lo), survivors=0, checked=0, skipped=False)
            _, r, _, blocks = _fake_search(self.D, _rand(), ub=5, ck=ck, spot=spot)
            self.assertEqual((r.status, r.exit_code, r.blocks_done, len(blocks)), ("VOID", 4, k, k + 1), k)
            self.assertIn(f"block {k}", r.detail)
            self.assertIn("window audit", r.detail)
            self.assertIn("survivor set differs", r.detail)          # what differed is in the VOID
            with open(ck, encoding="utf-8") as f:
                done = [int(line.split()[1]) for line in f if line.startswith("done ")]
            self.assertEqual(done, list(range(k)))                    # the audited block was never recorded
            void = [x for x in _ledger(r) if x["status"] == "VOID"]
            self.assertEqual((len(void), void[0]["block"], void[0]["blocks_done"]), (1, k, k))

    def test_any_exception_in_the_audit_is_void_and_keyboard_interrupt_propagates(self):
        for exc in (KeyError("k"), RuntimeError("driver"), MemoryError(), ValueError("v"), AttributeError("no such method")):
            def boom(lo, hi, seed, exc=exc):
                raise exc
            _, r, _, blocks = _fake_search(self.D, _rand(), ub=5, spot=boom)
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), exc)
            self.assertIn(type(exc).__name__, r.detail)
            self.assertIn("window audit", r.detail)

        def interrupt(lo, hi, seed):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            _fake_search(self.D, _rand(), ub=5, spot=interrupt)

    def test_an_engine_without_the_audit_is_void_never_silently_unaudited(self):
        """The audit is not optional: an engine object that has no k1_spot_check voids the run at block 0."""
        text = dict(small_config_texts())[self.D]
        _, r, _ = _cpu_run(text, _rand())
        self.assertEqual(r.status, "NO_MATCH")
        _, r, _ = _cpu_run(text, _rand(), fault=lambda eng: setattr(eng, "k1_spot_check", None))
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0))
        self.assertIn("window audit", r.detail)

    def test_a_confirmed_prize_hit_is_found_even_if_the_audit_would_fail(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[self.D]))
        good = next(i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i))
        addr, m = _addr(sp, good, 0, 0, 0)

        def boom(lo, hi, seed):
            raise gpu_setperm.EngineError("K1 survivor set differs")
        _, r, _, _ = _fake_search(self.D, addr, ub=5, spot=boom,
                                  edit=lambda hits, lo, hi: hits + [gpu_setperm.Hit(good, 0, 0, 0, 0)])
        self.assertEqual((r.status, r.exit_code, r.found["mnemonic"]), ("FOUND", 2, m))
        self.assertEqual(r.spot_calls, [])                            # the prize was judged before the block's controls

    def test_skipped_audits_are_counted_and_shown_never_hidden(self):
        lines = []
        skip = lambda lo, hi, seed: SimpleNamespace(window=(lo, hi - lo), survivors=99, checked=0, skipped=True)   # noqa: E731
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, spot=skip, log=lines.append)
        self.assertEqual((r.status, r.audits, r.audits_skipped), ("NO_MATCH", 5, 5))
        self.assertIn("0 of 5 blocks audited, 5 skipped", r.detail)
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5)
        self.assertEqual((r.audits, r.audits_skipped), (5, 0))
        self.assertIn("5 of 5 blocks audited, 0 skipped", r.detail)
        rec = [x for x in _ledger(r) if x["status"] == "NO_MATCH"][0]
        self.assertEqual((rec["audits"], rec["audits_skipped"]), (5, 0))

    def test_the_no_match_banner_names_the_audit_and_says_what_it_does_not_prove(self):
        lines = []
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, log=lines.append)
        for needle in ("window audit", "K1 survivor set", "K2/K3 hash160 parity", "sampled window", "tested == scanned",
                       "slot-sweep answers"):
            self.assertIn(needle, r.detail, needle)
        banner = [str(x) for x in lines if "NO MATCH (verified)" in str(x)]
        rest = "\n".join(str(x) for x in lines[lines.index(banner[0]) + 1:])
        self.assertIn("only sampled by the per-block window audit, not proven", rest)
        self.assertIn("outside the audited windows", rest)
        self.assertTrue(any("combo self-test passed" in str(x) and "slot sweep" in str(x) for x in lines), lines)

    def test_faults_only_the_audit_sees_are_void_at_block_0_on_a_real_run_and_pass_without_it(self):
        """The CPU engine with a K1 that lies only to the audit's window (its scan answers stay exact), or a K2/K3
        that returns wrong seeds / a wrong hash160 only to the audit's dump: every other control of the run holds, so
        the run without the audit is a false NO MATCH; with it, VOID in block 0 naming what differed."""
        import gpu_setperm
        text = dict(small_config_texts())["A_bip39_12_r3k1"]
        faults = [("K1 lost a survivor", _audit_k1_fault("lose one"), "survivor set differs"),
                  ("K1 gained a survivor", _audit_k1_fault("gain one"), "survivor set differs"),
                  ("K1 repeated a survivor", _audit_k1_fault("one repeated"), "survivor set differs"),
                  ("K2 wrote zero seeds", _audit_k23_fault("zero seeds"), "K2/K3 parity"),
                  ("K3 returned a wrong hash160", _audit_k23_fault("wrong hash"), "K2/K3 parity")]
        for name, fault, needle in faults:
            _, r, made = _cpu_run(text, _rand(), fault=fault)
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), name)
            self.assertIn("block 0", r.detail, name)
            self.assertIn("window audit", r.detail, name)
            self.assertIn(needle, r.detail, name)
            with mock.patch.object(gpu_setperm.Engine, "k1_spot_check", lambda self, lo, hi, seed, width=None: None):
                _, r, _ = _cpu_run(text, _rand(), fault=fault)
            self.assertEqual((r.status, r.exit_code, r.tested > 0), ("NO_MATCH", 0, True), (name, r.detail))

    def test_the_honest_cpu_engine_audits_every_block_and_its_counts_stay_exact(self):
        import gpu_setperm
        for name in ("A_bip39_12_r3k1", "J_bip39_12_dups", "K_bip39_12_distinct_overlap", "D_electrum_12_wordlist"):
            text = dict(small_config_texts())[name]
            calls = []

            def watch(eng, calls=calls):
                real = eng.k1_spot_check
                eng.k1_spot_check = lambda *a, **k: (calls.append(a), real(*a, **k))[1]
            with mock.patch.object(gpu_setperm, "plan_block_units", lambda space, target_deriv=4e6: max(1, space.units // 4)):
                sp, r, made = _cpu_run(text, _rand(), fault=watch)
            self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), (name, r.detail))
            tested, valid, _ = sp_ref.ref_survivors(cfg_from_text(text))
            self.assertEqual((r.tested, r.valid), (tested, valid), name)
            nblocks = -(-sp.units // max(1, sp.units // 4))
            self.assertEqual([(lo, hi) for lo, hi, _ in calls][:1], [(0, min(sp.total, max(1, sp.units // 4) * sp.per_unit))], name)
            self.assertEqual(len(calls), nblocks, name)
            self.assertEqual(r.audits, nblocks, name)
            self.assertEqual({seed for _, _, seed in calls}, {11}, name)


class SlotSweepNoGpu(unittest.TestCase):
    """The combo self-test's slot sweep: every value of every dimension is searched at K3 slots 0 and 1 and alone."""

    A = "A_bip39_12_r3k1"                                            # 3 passphrases x 2 paths x 2 forms

    @staticmethod
    def _slot_fault(slot, dim, value, alone=None):
        """Drop every K3 report at target slot `slot` for dimension value `value` (dim 1 passphrase, 2 path, 3 form);
        with alone=True only in rounds that search a single target."""
        def apply(eng):
            real = eng.k3.run

            def run(seeds, surv, off, n, npass):
                ntgt = len(eng.k3.targets)
                return [h for h in real(seeds, surv, off, n, npass)
                        if not (h[dim] == value and h[4] == slot and (alone is None or (ntgt == 1) == alone))]
            eng.k3.run = run
        return apply

    def test_every_value_of_every_dimension_is_searched_at_slots_0_and_1_and_alone(self):
        import gpu_setperm
        for name, npass, npaths in (("A_bip39_12_r3k1", 3, 2), ("E_electrum_13_alts", 1, 3), ("D_electrum_12_wordlist", 1, 1)):
            sp, eng = _selftest_engine(name)
            rounds = []
            real = eng.k3.set_targets
            eng.k3.set_targets = lambda t: (rounds.append(list(t)), real(t))[1]
            eng.set_targets = eng.k3.set_targets
            st = gpu_setperm.combo_selftest(sp, eng, seed=5)
            per = len(rounds) // len(st.survivors)
            self.assertEqual(per * len(st.survivors), len(rounds))
            self.assertEqual(sum(len(set(t)) == 1 for t in rounds), st.sweep_rounds, name)   # a sweep round: one hash160
            for s_i, idx in enumerate(st.survivors):
                mine = rounds[s_i * per:(s_i + 1) * per]
                m = " ".join(sp.decode(idx))
                names = {h: (p, q, f) for p, pp in enumerate(sp.passes)
                         for q, f, h in sp_ref.hash160_forms(m, pp, sp.paths, sp.scheme)}
                sweep = [t for t in mine if len(set(t)) == 1]
                self.assertEqual(len(sweep) % 2, 0)
                seen = {(dim, value, slot, len(t)) for t in sweep for slot, h in enumerate(t)
                        for dim, value in zip(("pass", "path", "form"), names[h])}
                for dim, n in (("pass", npass), ("path", npaths), ("form", 2)):
                    for value in range(n):
                        self.assertIn((dim, value, 0, 2), seen, (name, dim, value))     # slot 0 of two targets
                        self.assertIn((dim, value, 1, 2), seen, (name, dim, value))     # slot 1 of two targets
                        self.assertIn((dim, value, 0, 1), seen, (name, dim, value))     # alone
                for t in sweep:                                                          # [h, h] or [h], nothing else
                    self.assertIn(len(t), (1, 2))
            self.assertGreater(st.sweep_answers, 0, name)

    def test_the_cover_is_one_combination_per_new_value_and_never_repeats_a_combination(self):
        import gpu_setperm
        sp, eng = _selftest_engine(self.A)
        rounds = []
        real = eng.k3.set_targets
        eng.k3.set_targets = lambda t: (rounds.append(list(t)), real(t))[1]
        eng.set_targets = eng.k3.set_targets
        st = gpu_setperm.combo_selftest(sp, eng, seed=5)
        self.assertEqual((st.rounds, st.combos), (2, 12))
        sweep_per_survivor = st.sweep_rounds // 2
        firsts = [t[0] for t in rounds[1:1 + sweep_per_survivor]]
        cover = list(dict.fromkeys(firsts))
        self.assertEqual(len(cover), sweep_per_survivor // 2)                            # [h, h] then [h] for each
        self.assertLessEqual(len(cover), 3 + 2 + 2)                                       # at most one per value
        self.assertGreaterEqual(len(cover), 3)                                            # the largest dimension
        # answers: each combination of A has a unique hash160, so [h, h] expects 2 hits and [h] one
        self.assertEqual(st.sweep_answers, 2 * len(cover) * 3)

    def test_a_fault_keyed_on_a_slot_and_one_value_is_void_before_block_0_and_the_sweep_is_what_sees_it(self):
        import gpu_setperm
        slipped = 0
        for name, dims in ((self.A, (("pass", 1, 3), ("path", 2, 2), ("form", 3, 2))),
                           ("E_electrum_13_alts", (("path", 2, 3),))):
            for dim_name, dim, n in dims:
                for value in range(n):
                    for slot in (0, 1):
                        case = (name, dim_name, value, slot)
                        sp, eng = _selftest_engine(name, fault=self._slot_fault(slot, dim, value))
                        with self.assertRaises(gpu_setperm.EngineError, msg=case) as cm:
                            gpu_setperm.combo_selftest(sp, eng, seed=5)
                        self.assertIn("combo self-test", str(cm.exception), case)
                        sp, eng = _selftest_engine(name, fault=self._slot_fault(slot, dim, value))
                        try:                                                             # the main rounds alone
                            with _main_rounds_only():
                                gpu_setperm.combo_selftest(sp, eng, seed=5)
                        except gpu_setperm.EngineError:
                            continue
                        slipped += 1                                                     # ... would have passed:
                        self.assertIn("slot sweep", str(cm.exception), case)             # only the sweep sees it
                        self.assertIn(f"target #{slot}", str(cm.exception), case)
        self.assertGreaterEqual(slipped, 8)                                              # and there are plenty of those

    def test_a_fault_keyed_on_a_single_target_round_and_one_value_is_seen_only_by_the_lone_round(self):
        import gpu_setperm
        for dim, value, name in ((1, 2, "passphrase #2"), (2, 1, "path #1"), (3, 1, "uncompressed key")):
            sp, eng = _selftest_engine(self.A, fault=self._slot_fault(0, dim, value, alone=True))
            with _main_rounds_only():
                gpu_setperm.combo_selftest(sp, eng, seed=5)                              # the main rounds never see it
            sp, eng = _selftest_engine(self.A, fault=self._slot_fault(0, dim, value, alone=True))
            with self.assertRaises(gpu_setperm.EngineError, msg=name) as cm:
                gpu_setperm.combo_selftest(sp, eng, seed=5)
            self.assertIn("slot sweep", str(cm.exception), name)
            self.assertIn("1 target(s)", str(cm.exception), name)
            self.assertIn(name, str(cm.exception), name)

    def test_the_sweep_fault_is_void_before_block_0_on_a_real_run(self):
        text = dict(small_config_texts())[self.A]
        for slot, dim, value in ((1, 1, 2), (1, 2, 1), (0, 1, 2)):
            _, r, made = _cpu_run(text, _rand(), fault=self._slot_fault(slot, dim, value))
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), (slot, dim, value))
            self.assertIn("combo self-test", r.detail)
            self.assertTrue(all(c == 1 for _, c in made[-1].k1.runs), made[-1].k1.runs)   # no block was scanned

    def test_a_sweep_answer_missing_duplicated_or_unexpected_is_refused(self):
        import gpu_setperm

        def only_in_sweep(transform):
            def apply(eng):
                real = eng.k3.run
                eng.k3.run = lambda seeds, surv, off, n, npass: (lambda hits: transform(hits) if len(eng.k3.targets) <= 2 else hits)(
                    list(real(seeds, surv, off, n, npass)))
            return apply
        for name, tf, needle in (("a lost hit", lambda hits: hits[:-1], "missing"),
                                 ("a doubled report", lambda hits: hits + hits[:1], "duplicated"),
                                 ("an extra hit", lambda hits: hits + [(hits[0][0], 0, 0, 0, 7)], "unexpected")):
            sp, eng = _selftest_engine(self.A, fault=only_in_sweep(tf))
            with self.assertRaises(gpu_setperm.EngineError, msg=name) as cm:
                gpu_setperm.combo_selftest(sp, eng, seed=5)
            self.assertIn("slot sweep", str(cm.exception), name)
            self.assertIn(needle, str(cm.exception), name)


class TestedEqualsScannedNoGpu(unittest.TestCase):
    """Without DISTINCT nothing is filtered before `tested` is counted, so tested must equal scanned. The binomial check
    only sees the RATIO valid/tested: a K1 that skips candidates after counting them (tested and valid shrink together)
    passes it."""

    D = "D_electrum_12_wordlist"

    def test_tested_below_scanned_without_distinct_is_void(self):
        for delta in (-3, -1, 1, 3, -10):
            _, r, _, blocks = _fake_search(self.D, _rand(), ub=5,
                                           size=lambda lo, hi, d=delta: (hi - lo + d, round((hi - lo + d) / 256)))
            self.assertEqual((r.status, r.exit_code, r.blocks_done, len(blocks)), ("VOID", 4, 0, 1), delta)
            self.assertIn("block 0", r.detail, delta)
            self.assertIn("tested", r.detail, delta)
            self.assertIn("DISTINCT", r.detail, delta)
            self.assertNotIn("binomial", r.detail, delta)
            self.assertIn(f"{5 * 2048 + delta:,}", r.detail, delta)

    def test_tested_equal_to_scanned_passes_and_a_distinct_config_may_differ(self):
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, size=lambda lo, hi: (hi - lo, round((hi - lo) / 256)))
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        for name in ("B_bip39_12_distinct_alts", "K_bip39_12_distinct_overlap"):       # bip39-12: 1 in 16 pass
            _, r, _, _ = _fake_search(name, _rand(), size=lambda lo, hi: (hi - lo - 3, round((hi - lo - 3) / 16)))
            self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), (name, r.detail))

    def test_the_binomial_check_alone_cannot_see_tested_and_valid_shrinking_together(self):
        from gpu_setperm import binomial_problem
        tested, rate = 4_000_000, 1 / 256
        valid = round(tested * rate)
        self.assertIsNone(binomial_problem(tested, valid, rate))
        self.assertIsNone(binomial_problem(tested - 3, valid - 1, rate))                   # 3 candidates gone: unseen
        self.assertIsNone(binomial_problem(int(tested * 0.99), int(valid * 0.99), rate))   # a whole 1% gone: unseen

    def test_it_is_a_void_after_a_confirmed_prize_hit_not_before(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[self.D]))
        good = next(i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i))
        addr, m = _addr(sp, good, 0, 0, 0)
        _, r, _, _ = _fake_search(self.D, addr, ub=5, size=lambda lo, hi: (hi - lo - 3, 40),
                                  edit=lambda hits, lo, hi: hits + [gpu_setperm.Hit(good, 0, 0, 0, 0)])
        self.assertEqual((r.status, r.found["mnemonic"]), ("FOUND", m))

    def test_a_cpu_engine_whose_k1_tests_fewer_candidates_than_it_scanned_is_void(self):
        text = dict(small_config_texts())["D_electrum_12_wordlist"]

        def fewer_tested(eng):
            real = eng.k1.run

            def run(start, count):
                s, t, v = real(start, count)
                return (s, t - 3, v) if count > 1 else (s, t, v)       # not the 1-wide windows of the combo self-test
            eng.k1.run = run
        _, r, _ = _cpu_run(text, _rand(), fault=fewer_tested)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0))
        self.assertIn("tested", r.detail)
        self.assertIn("DISTINCT", r.detail)


class PlantPlacementNoGpu(unittest.TestCase):
    """Nothing pinned that a block's plant is spread over the block: a plant that always sat in the first 4096
    candidates, or on the block's first survivor, would only ever test that corner of K1/K2/K3."""

    @staticmethod
    def _first_survivor(sp, lo, hi):
        return next((c for c in range(lo, hi) if sp.candidate_ok(c)), None)

    def test_plants_of_a_large_space_are_spread_over_their_blocks(self):
        from gpu_setperm import make_block_plant, plan_block_units
        from sp_space import Space
        e4 = Space.load(E4)
        ub = plan_block_units(e4)
        block, nb = ub * e4.per_unit, -(-e4.units // ub)
        self.assertGreaterEqual(nb, 80)
        offs, first, in_first_4096 = [], 0, 0
        for b in range(60):
            lo, hi = b * block, min(e4.total, (b + 1) * block)
            p = make_block_plant(e4, 7, b, lo, hi)
            self.assertTrue(lo <= p.idx < hi)
            offs.append((p.idx - lo) / (hi - lo))
            in_first_4096 += (p.idx - lo) < (1 << 12)
            first += p.idx == self._first_survivor(e4, lo, min(hi, lo + 100_000))
        self.assertLessEqual(in_first_4096, 3)                         # not all in the first 1<<12 candidates: ~none are
        self.assertLessEqual(first, 3)                                 # and not the block's first survivor
        quarters = [sum(q / 4 <= o < (q + 1) / 4 for o in offs) for q in range(4)]
        self.assertTrue(all(n >= 5 for n in quarters), quarters)       # every quarter of a block is planted in
        self.assertTrue(0.35 < sum(offs) / len(offs) < 0.65, sum(offs) / len(offs))

    def test_the_plant_offset_within_a_block_depends_on_the_seed_and_the_block(self):
        from gpu_setperm import make_block_plant, plan_block_units
        from sp_space import Space
        e4 = Space.load(E4)
        block = plan_block_units(e4) * e4.per_unit
        same_block = {make_block_plant(e4, s, 3, 3 * block, 4 * block).idx - 3 * block for s in range(20)}
        self.assertGreater(len(same_block), 15)
        same_seed = {make_block_plant(e4, 5, b, b * block, (b + 1) * block).idx - b * block for b in range(20)}
        self.assertGreater(len(same_seed), 15)

    def test_a_small_blocks_plant_is_not_always_its_first_or_last_survivor(self):
        from gpu_setperm import make_block_plant
        from sp_space import Space
        d = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        block = d.per_unit                                              # 2048 candidates, ~8 survivors per block
        first = last = total = 0
        ranks = set()
        blocks = []
        for b in range(d.units):
            lo, hi = b * block, (b + 1) * block
            blocks.append((b, lo, hi, [c for c in range(lo, hi) if d.candidate_ok(c)]))
        for seed in range(6):
            for b, lo, hi, surv in blocks:
                if len(surv) < 4:
                    continue
                p = make_block_plant(d, seed, b, lo, hi)
                self.assertIn(p.idx, surv)
                total += 1
                first += p.idx == surv[0]
                last += p.idx == surv[-1]
                ranks.add(surv.index(p.idx) / (len(surv) - 1))
        self.assertGreater(total, 100)
        self.assertLess(first, total * 0.4)                             # ~1/8 expected
        self.assertLess(last, total * 0.4)
        self.assertGreater(first + last, 0)                             # ... but the ends are reachable too
        self.assertGreaterEqual(len(ranks), 5)                          # and the middle is

    def test_a_block_of_two_survivors_is_planted_in_both_of_them_over_the_seeds(self):
        from gpu_setperm import make_block_plant
        from sp_space import Space
        d = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        surv = [c for c in range(d.total) if d.candidate_ok(c)][:2]
        keep = set(surv)
        d.candidate_ok = lambda c: c in keep
        got = {make_block_plant(d, s, 0, 0, surv[1] + 1).idx for s in range(30)}
        self.assertEqual(got, keep)


class RailsGuardNoGpu(unittest.TestCase):
    """No test may reach a real search with the prize address (ADR-0001): setUpModule wraps run_search's search step."""

    def test_a_real_search_with_the_default_prize_is_refused_by_the_test_guard(self):
        import gpu_setperm
        from sp_space import Space

        def boom(*a, **k):
            raise AssertionError("an Engine was built for a real search of the prize address")
        d = tempfile.mkdtemp()
        sp = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        with mock.patch.object(gpu_setperm, "Engine", boom), mock.patch.object(gpu_setperm, "probe_rate", lambda s, g, l: 1e9):
            with self.assertRaises(AssertionError) as cm:
                gpu_setperm.run_search(sp, gpu=object(), log=QUIET, ledger=os.path.join(d, "l"), found_path=os.path.join(d, "F"),
                                       signoff=SIGNOFF)             # signed: the test guard is what must stop it
        self.assertIn("real prize", str(cm.exception))

    def test_the_default_prize_is_still_the_default_outside_the_guard(self):
        import gpu_setperm
        import template_solve
        self.assertEqual(gpu_setperm.run_search.__kwdefaults__["prize_addr"], template_solve.TARGET)


class DocsNoGpu(unittest.TestCase):
    """The docs say what the new controls are, and stay honest about what is still not proven."""

    def _texts(self):
        import gpu_setperm
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as f:
            readme = " ".join(f.read().lower().replace("`", "").split())
        return readme, " ".join(gpu_setperm.__doc__.lower().split())

    def test_readme_and_docstring_describe_the_window_audit_the_sweep_and_the_tested_control(self):
        readme, doc = self._texts()
        for text, label in ((readme, "README"), (doc, "docstring")):
            for needle in ("window audit", "sampled", "not proven", "slot sweep", "tested == scanned", "no distinct"):
                self.assertTrue(needle in text, (label, needle))
        for needle in ("32,768", "16 of", "hash160", "k2/k3", "sp_test_k1"):
            self.assertTrue(needle in readme, needle)

    def test_the_residual_list_is_honest(self):
        readme, doc = self._texts()
        for text, label in ((readme, "README"), (doc, "docstring")):
            self.assertTrue("forged" in text and "checkpoint" in text, label)
            self.assertTrue("joint" in text and "slot" in text and "plants" in text, label)
            self.assertTrue("invisible by design" in text, label)
            self.assertTrue("nsurv" in text, label)
        self.assertTrue("residual" in readme)
        # residual (a) is no longer "believed without a run-time check": it is sampled, not proven
        self.assertNotIn("rests on the sp_test_k1 parity tests against the cpu tool, not on run-time controls", readme)

    def test_bench_exits_0_on_success_like_a_verified_no_match_and_the_docs_say_how_to_tell_them_apart(self):
        readme, doc = self._texts()
        for text, label in ((readme, "README"), (doc, "docstring")):
            self.assertTrue("--bench" in text and "json" in text, label)
            self.assertTrue("successful --bench" in text or "--bench exits 0" in text, label)
        self.assertIn("run_search", doc)
        import gpu_setperm
        with tempfile.TemporaryDirectory() as d:                          # ... and the code really does exit 0 there
            conf = os.path.join(d, "d.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(dict(small_config_texts())["D_electrum_12_wordlist"])
            good = {"plant_found": True, "coverage_exact": True}
            with mock.patch.object(gpu_setperm, "bench", lambda space: good), contextlib.redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(SystemExit) as cm:
                    gpu_setperm.main([conf, "--bench"])
            self.assertEqual(cm.exception.code, 0)
            self.assertIn("plant_found", out.getvalue())                    # the JSON is what tells it from NO MATCH


# ================================================================ fixes from the independent review of 2026-09-29

def _place_fault(every, place):
    """A `fault(engine)` hook for _cpu_run: K3 loses every hit of the survivor at `place` of every `every` survivors
    of the slice it is handed - a hit-path fault tied to a thread's place in a launch, nothing else."""
    def apply(eng):
        real = eng.k3.run

        def run(seeds, surv, off, n, npass):
            lost = {int(x) for k, x in enumerate(list(surv[off:off + n])) if k % every == place}
            return [h for h in real(seeds, surv, off, n, npass) if h[0] not in lost]
        eng.k3.run = run
    return apply


class PlantsPerBlockNoGpu(unittest.TestCase):
    """One plant per block samples the hit path once per block: a K3 fault tied to a thread's place in a launch that
    lost 1 hit in 64 passed every control (the review's injected fault). Every block now carries PLANTS_PER_BLOCK
    plants, each at its own random place with its own random (passphrase, path, key form)."""

    D = "D_electrum_12_wordlist"

    def test_a_block_gets_64_distinct_plants_and_the_first_is_the_single_plant(self):
        import gpu_setperm
        from sp_space import Space
        self.assertEqual(gpu_setperm.PLANTS_PER_BLOCK, 64)
        e4 = Space.load(E4)
        block = gpu_setperm.plan_block_units(e4) * e4.per_unit
        for b in (0, 82):                                               # the last block is a partial one
            lo, hi = b * block, min(e4.total, (b + 1) * block)
            ps = gpu_setperm.make_block_plants(e4, 7, b, lo, hi)
            self.assertEqual(len(ps), 64, b)
            self.assertEqual(ps[0], gpu_setperm.make_block_plant(e4, 7, b, lo, hi))
            self.assertEqual(len({q.idx for q in ps}), 64)
            self.assertEqual(len({q.h160 for q in ps}), 64)
            for q in ps:
                self.assertTrue(lo <= q.idx < hi, (b, q.idx))
                self.assertTrue(e4.candidate_ok(q.idx))
                m = " ".join(e4.decode(q.idx))
                want = sp_ref.hash160_forms(m, e4.passes[q.pass_i], [e4.paths[q.path_i]], e4.scheme)[q.form][2]
                self.assertEqual(q.h160, want)
            self.assertEqual(gpu_setperm.make_block_plants(e4, 7, b, lo, hi), ps)          # deterministic per (seed, b)
            other = gpu_setperm.make_block_plants(e4, 8, b, lo, hi)
            self.assertLess(len({q.idx for q in other} & {q.idx for q in ps}), 3)          # another seed: other places

    def test_the_count_can_be_asked_for_and_is_a_prefix_of_the_full_list(self):
        import gpu_setperm
        from sp_space import Space
        e4 = Space.load(E4)
        block = gpu_setperm.plan_block_units(e4) * e4.per_unit
        full = gpu_setperm.make_block_plants(e4, 3, 5, 5 * block, 6 * block)
        for n in (1, 2, 17):
            self.assertEqual(gpu_setperm.make_block_plants(e4, 3, 5, 5 * block, 6 * block, n), full[:n], n)
        with self.assertRaises(ValueError):
            gpu_setperm.make_block_plants(e4, 3, 5, 5 * block, 6 * block, 0)

    def test_the_plants_of_a_block_are_spread_over_it_and_over_the_combinations(self):
        import gpu_setperm
        from sp_space import Space
        e4 = Space.load(E4)
        block = gpu_setperm.plan_block_units(e4) * e4.per_unit
        lo, hi = 9 * block, 10 * block
        ps = gpu_setperm.make_block_plants(e4, 21, 9, lo, hi)
        offs = [(q.idx - lo) / (hi - lo) for q in ps]
        quarters = [sum(k / 4 <= o < (k + 1) / 4 for o in offs) for k in range(4)]
        self.assertTrue(all(n >= 8 for n in quarters), quarters)                           # ~16 expected in each
        self.assertEqual(len({(q.pass_i, q.path_i, q.form) for q in ps}), 8)               # 4 paths x 2 key forms
        self.assertLessEqual(sum((q.idx - lo) < (1 << 12) for q in ps), 1)

    def test_a_block_with_fewer_survivors_plants_every_seed_it_holds(self):
        import gpu_setperm
        from sp_space import Space
        d = Space(cfg_from_text(dict(small_config_texts())[self.D]))
        block = d.per_unit                                              # 2048 candidates, ~8 survivors
        seen = 0
        for b in range(8):
            lo, hi = b * block, (b + 1) * block
            surv = [c for c in range(lo, hi) if d.candidate_ok(c)]
            ps = gpu_setperm.make_block_plants(d, 3, b, lo, hi)
            self.assertTrue(set(q.idx for q in ps) <= set(surv), b)
            self.assertEqual(len({q.idx for q in ps}), len(ps))
            self.assertEqual(len({q.h160 for q in ps}), len(ps))
            self.assertEqual({tuple(d.decode(q.idx)) for q in ps}, {tuple(d.decode(c)) for c in surv}, b)
            if surv:
                self.assertEqual(ps[0], gpu_setperm.make_block_plant(d, 3, b, lo, hi))
            seen += len(ps)
        self.assertGreater(seen, 30)

    def test_a_block_without_a_survivor_has_no_plant(self):
        import gpu_setperm
        from sp_space import Space
        d = Space(cfg_from_text(dict(small_config_texts())[self.D]))
        d.candidate_ok = lambda c: False
        self.assertEqual(gpu_setperm.make_block_plants(d, 1, 0, 0, 5000), [])

    def test_twin_indices_of_one_seed_never_give_two_plants_with_one_hash160(self):
        import gpu_setperm
        from sp_space import Space
        j = Space(cfg_from_text(dict(small_config_texts())["J_bip39_12_dups"]))            # every candidate has a twin
        surv = [c for c in range(j.total) if j.candidate_ok(c)]
        self.assertLess(len({tuple(j.decode(c)) for c in surv}), len(surv))
        for seed in range(6):
            ps = gpu_setperm.make_block_plants(j, seed, 0, 0, j.total)
            self.assertEqual(len({q.h160 for q in ps}), len(ps), seed)
            self.assertEqual({tuple(j.decode(q.idx)) for q in ps}, {tuple(j.decode(c)) for c in surv}, seed)


class PrizeSlotNoGpu(unittest.TestCase):
    """The K3 target list of a block: PRIZE_COPIES (2) slots hold the prize, the block's plants hold all the others.
    The prize slots move on by one from block to block and start where the plant seed says."""

    def test_the_prize_sits_at_two_slots_half_a_list_apart_and_they_move_with_block_and_seed(self):
        import gpu_setperm
        from gpu_setperm import _logical_tgt, block_targets
        self.assertEqual(gpu_setperm.PRIZE_COPIES, 2)
        prize = b"p" * 20
        for nplants in (5, 6, 64):
            hs = [bytes([k]) * 20 for k in range(1, nplants + 1)]
            n = nplants + 2
            for seed in (0, 1, 11, 4242, 2 ** 32 - 1):
                first = []
                for b in range(n + 3):
                    targets, slots = block_targets(prize, hs, b, seed)
                    self.assertEqual(slots, ((b + seed) % n, (b + seed + n // 2) % n), (nplants, seed, b))
                    self.assertEqual(len(set(slots)), 2)
                    self.assertEqual(targets, _rotated(prize, hs, b, seed, 2))
                    self.assertEqual([t for t, x in enumerate(targets) if x == prize], sorted(slots))
                    self.assertEqual([x for x in targets if x != prize], hs)               # the plants keep their order
                    back = [_logical_tgt(t, slots, nplants) for t in range(n)]
                    self.assertEqual([([prize] + hs)[k] for k in back], targets)           # 0 = prize, 1 + j = plant j
                    self.assertEqual([_logical_tgt(t, slots, nplants) for t in (n, n + 1, -1, 999, None, "1", 1.0, True)],
                                     [n, n + 1, -1, 999, None, "1", 1.0, True])            # no such target: as it is
                    first.append(slots[0])
                self.assertEqual(set(first), set(range(n)))                                # every slot in turn

    def test_a_block_without_plants_searches_the_prize_alone_at_both_slots(self):
        from gpu_setperm import _logical_tgt, block_targets
        prize = b"p" * 20
        for b, seed in ((0, 0), (3, 11), (7, 5)):
            targets, slots = block_targets(prize, [], b, seed)
            self.assertEqual((targets, sorted(slots)), ([prize, prize], [0, 1]))
        self.assertEqual([_logical_tgt(t, (0, 1), 0) for t in (0, 1, 2, 3)], [0, 0, 2, 3])  # 2 names no target: refused

    def test_one_copy_is_the_single_slot_rule(self):
        from gpu_setperm import block_targets
        prize, plant = b"p" * 20, b"q" * 20
        self.assertEqual([block_targets(prize, [plant], b, 0, 1) for b in range(4)],
                         [([prize, plant], (0,)), ([plant, prize], (1,)), ([prize, plant], (0,)), ([plant, prize], (1,))])
        with self.assertRaises(ValueError):
            block_targets(prize, [plant], 0, 0, 0)


class ManyPlantsInRunNoGpu(unittest.TestCase):
    """run_search with several plants in every block: all of them must come back, each at its own place."""

    D = "D_electrum_12_wordlist"

    def _blk(self, ub=5):
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[self.D]))
        return sp, ub * sp.per_unit

    def test_an_honest_run_searches_every_block_for_all_of_its_plants(self):
        sp, r, plants, blocks = _fake_search(self.D, _rand(), ub=5, nplants=5)
        self.assertEqual((r.status, r.exit_code, r.plants_ok), ("NO_MATCH", 0, True), r.detail)
        self.assertEqual(len(blocks), 5)
        for b in range(5):
            self.assertEqual(len(r.plist[b]), 5, b)
            self.assertEqual(len(r.given[b]), 6, b)
            self.assertEqual(r.plist[b][0], plants[b])
        self.assertEqual(r.plants_found, 25)
        self.assertIn("25 planted seeds", r.detail)
        rec = _ledger(r)[-1]
        self.assertEqual((rec["status"], rec["plants_found"]), ("NO_MATCH", 25))
        self.assertEqual(_ledger(r)[0]["plants_per_block"], 5)

    def test_one_missing_plant_of_many_is_void_and_named(self):
        sp, blk = self._blk()
        for j in (0, 2, 4):
            def drop(hits, lo, hi, j=j):
                return [h for h in hits if not (lo == 2 * blk and h.tgt_i == 1 + j)]
            _, r, _, _ = _fake_search(self.D, _rand(), ub=5, nplants=5, edit=drop)
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 2), j)
            q = r.plist[2][j]
            self.assertIn("block 2", r.detail)
            self.assertIn("1 of its 5 plants", r.detail)
            self.assertIn(f"plant #{j} (index {q.idx:,}", r.detail)
            self.assertIn("not found", r.detail)

    def test_every_missing_plant_is_counted(self):
        sp, blk = self._blk()
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, nplants=5,
                                  edit=lambda hits, lo, hi: [h for h in hits if h.tgt_i in (2, 5)])
        self.assertEqual((r.status, r.blocks_done), ("VOID", 0))
        self.assertIn("3 of its 5 plants", r.detail)
        for j in (0, 2, 3):
            self.assertIn(f"plant #{j} ", r.detail)

    def test_a_plant_reported_at_another_plants_place_is_void(self):
        def swap(hits, lo, hi):
            a, b = hits[1], hits[3]
            return [hits[0], a._replace(tgt_i=b.tgt_i), hits[2], b._replace(tgt_i=a.tgt_i), hits[4]]
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, nplants=5, edit=swap)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0))
        self.assertIn("does not confirm", r.detail)

    def test_a_report_naming_a_plant_the_block_does_not_have_is_void(self):
        import gpu_setperm
        H = gpu_setperm.Hit
        for t in (6, 7, 65, -1):
            _, r, _, _ = _fake_search(self.D, _rand(), ub=5, nplants=5,
                                      edit=lambda hits, lo, hi, t=t: hits + [H(hits[0].idx, 0, 0, 0, t)])
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), t)
            self.assertIn("names a target that does not exist", r.detail, t)

    def test_a_prize_hit_is_found_whatever_slot_the_prize_has(self):
        import gpu_setperm
        sp, blk = self._blk()
        H = gpu_setperm.Hit
        slots = set()
        for b in range(5):
            good = next(i for i in range(b * blk, sp.total) if sp.candidate_ok(i))
            self.assertLess(good, min(sp.total, (b + 1) * blk))
            addr, m = _addr(sp, good, 0, 0, b % 2)
            _, r, _, _ = _fake_search(self.D, addr, ub=5, nplants=5,
                                      edit=lambda hits, lo, hi, b=b, good=good: hits + [H(good, 0, 0, b % 2, 0)]
                                      if lo == b * blk else hits)
            self.assertEqual((r.status, r.exit_code, r.found["mnemonic"], r.blocks_done), ("FOUND", 2, m, b), b)
            slots.add(r.given[b].index(hash160_of_address(addr)))
        self.assertEqual(len(slots), 5)                                  # five blocks, five different K3 slots

    def test_with_two_copies_every_block_searches_the_prize_at_two_slots_and_reports_it_once_found(self):
        import gpu_setperm
        sp, blk = self._blk()
        prize = _rand()
        _, r, _, _ = _fake_search(self.D, prize, ub=5, nplants=5, copies=2)
        self.assertEqual((r.status, r.plants_found), ("NO_MATCH", 25), r.detail)
        ph = hash160_of_address(prize)
        for b, t in enumerate(r.given):
            self.assertEqual(len(t), 7)
            self.assertEqual(sorted(k for k, x in enumerate(t) if x == ph), sorted(_prize_slots(5, b, 11, 2)), b)
        H = gpu_setperm.Hit
        good = next(i for i in range(3 * blk, sp.total) if sp.candidate_ok(i))
        addr, m = _addr(sp, good, 0, 0, 1)
        _, r, _, _ = _fake_search(self.D, addr, ub=5, nplants=5, copies=2,
                                  edit=lambda hits, lo, hi: hits + [H(good, 0, 0, 1, 0)] if lo == 3 * blk else hits)
        self.assertEqual((r.status, r.exit_code, r.found["mnemonic"], r.found["indices"]), ("FOUND", 2, m, [good]))

    def test_a_fault_confined_to_one_slot_and_one_place_cannot_lose_a_prize_that_sits_at_two_slots(self):
        """The fault the review injected, against a prize that is really there. K3 loses the hits at ONE raw slot
        for 1 survivor in 8 (by its place in the slice); the prize seed is at such a place, in a block whose first
        prize slot is that slot. No plant of that block sits at the slot, the self-test and the audit cannot see the
        place: with one copy of the prize the run is a FALSE NO MATCH, with two the other copy reports it."""
        import gpu_setperm
        from sp_space import Space
        text = dict(small_config_texts())[self.D]
        sp = Space(cfg_from_text(text))
        surv = [i for i in range(sp.total) if sp.candidate_ok(i)]
        results = {}
        for copies in (1, 2):
            with mock.patch.object(gpu_setperm, "PRIZE_COPIES", copies):
                nplants = len(gpu_setperm.make_block_plants(sp, 11, 0, 0, sp.total))
                slot = _prize_slots(nplants, 0, 11, copies)[0]

                def fault(eng, slot=slot):
                    real = eng.k3.run

                    def run(seeds, surv_, off, n, npass):
                        lost = {int(x) for k, x in enumerate(list(surv_[off:off + n])) if k % 8 == 7}
                        return [h for h in real(seeds, surv_, off, n, npass) if not (h[4] == slot and h[0] in lost)]
                    eng.k3.run = run
                # the 8th survivor of the space: place 7 of the first slice of 8 (one chunk holds every survivor)
                addr, m = _addr(sp, surv[7], 0, 0, 1)
                _, r, _ = _cpu_run(text, addr, chunk=sp.total, batch=8, fault=fault, plant_seed=11)
                results[copies] = (r.status, r.found and r.found["mnemonic"])
        self.assertEqual(results[1], ("NO_MATCH", None))                 # what one prize slot made of it
        self.assertEqual(results[2], ("FOUND", m))

    def test_a_fault_tied_to_the_place_in_a_batch_passes_one_plant_but_not_sixty_four(self):
        """The review's finding as a test. K3 loses the hit of 1 survivor in 8, by its place in the slice. The
        self-test scans 1-wide windows and the window audit reads the dump path, so only plants can see it."""
        import gpu_setperm
        text = dict(small_config_texts())[self.D]
        fault = _place_fault(8, 7)
        passed = []
        with mock.patch.object(gpu_setperm, "PLANTS_PER_BLOCK", 1):
            for seed in range(8):
                _, r, _ = _cpu_run(text, _rand(), chunk=4096, batch=8, fault=fault, plant_seed=seed)
                self.assertIn(r.status, ("NO_MATCH", "VOID"))
                passed += [seed] if r.status == "NO_MATCH" else []
        self.assertGreaterEqual(len(passed), 4, passed)                  # one plant: 7 runs in 8 see nothing
        for seed in passed[:3]:
            _, r, _ = _cpu_run(text, _rand(), chunk=4096, batch=8, fault=fault, plant_seed=seed)
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), seed)
            self.assertIn("not found", r.detail)
            self.assertIn("of its 64 plants", r.detail)
        _, r, _ = _cpu_run(text, _rand(), chunk=4096, batch=8, plant_seed=passed[0])       # no fault: all 64 come back
        self.assertEqual((r.status, r.plants_found), ("NO_MATCH", 64), r.detail)


class SlotReachNoGpu(unittest.TestCase):
    """The self-test's main rounds reach as many K3 slots as the config has combinations (8 on e4) and the sweep slots
    0 and 1, but a block with 64 plants searches 65 targets: the slot-reach round puts a known answer at every one."""

    E = "E_electrum_13_alts"                                         # 1 passphrase x 3 paths x 2 key forms

    @staticmethod
    def _rounds(eng):
        rounds = []
        real = eng.k3.set_targets
        eng.k3.set_targets = lambda t: (rounds.append(list(t)), real(t))[1]
        eng.set_targets = eng.k3.set_targets
        return rounds

    def test_every_slot_a_full_block_uses_holds_a_known_answer(self):
        import gpu_setperm
        sp, eng = _selftest_engine(self.E)
        rounds = self._rounds(eng)
        st = gpu_setperm.combo_selftest(sp, eng, seed=5)
        self.assertEqual((st.reach_rounds, st.reach_answers), (2, 2 * 66))
        wide = [t for t in rounds if len(t) == gpu_setperm.PLANTS_PER_BLOCK + gpu_setperm.PRIZE_COPIES]
        self.assertEqual(len(wide), 2)                                   # one per survivor
        for t in wide:
            self.assertEqual(len(set(t)), 6)                             # the survivor's own combinations, in turn
        self.assertEqual(max(len(t) for t in rounds), 66)

    def test_a_fault_at_a_slot_only_a_full_block_uses_is_refused_before_block_0(self):
        import gpu_setperm
        for slot in (6, 9, 40, 64, 65):
            fault = _k3_fault(lambda hits, slot=slot: [h for h in hits if h[4] != slot])
            sp, eng = _selftest_engine(self.E, fault=fault)
            with self.assertRaises(gpu_setperm.EngineError, msg=slot) as cm:
                gpu_setperm.combo_selftest(sp, eng, seed=5)
            self.assertIn("slot reach", str(cm.exception), slot)
            self.assertIn(f"target #{slot}:", str(cm.exception), slot)
            _, r, made = _cpu_run(dict(small_config_texts())[self.E], _rand(), fault=fault)
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), slot)
            self.assertIn("slot reach", r.detail)

    def test_it_is_split_into_rounds_when_the_hit_buffer_is_small_and_refused_when_one_slot_cannot_fit(self):
        import gpu_setperm
        sp, eng = _selftest_engine(self.E, hit_cap=16)
        rounds = self._rounds(eng)
        st = gpu_setperm.combo_selftest(sp, eng, seed=5)
        self.assertEqual(st.reach_answers, 2 * 66)
        self.assertEqual(st.reach_rounds, 2 * 5)                         # 66 slots, 16 answers a round
        self.assertEqual(sum(len(t) == 66 for t in rounds), 10)
        text = dict(small_config_texts())[self.E] + "PATH m/0/0\nPATH m/0/0\n"            # one hash160, three owners
        sp, eng = _selftest_engine(text, hit_cap=2)
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            gpu_setperm.combo_selftest(sp, eng, seed=5)
        self.assertIn("hit buffer", str(cm.exception))


class SignoffNoGpu(unittest.TestCase):
    """ADR-0001 rail 4 in code: every real run needs the operator's explicit sign-off, whatever its size. The 1-hour
    cost guard alone let a run of 900 million derivations start from a bare command line."""

    D = "D_electrum_12_wordlist"

    @staticmethod
    def _boom(*a, **k):
        raise AssertionError("a run without a sign-off went past the refusal")

    def test_a_real_run_without_a_signoff_is_refused_before_anything_is_touched(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space.load(E4)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(gpu_setperm.Engine, "__init__", self._boom), \
                mock.patch.object(gpu_setperm, "probe_rate", self._boom), mock.patch.object(gpu_setperm, "bench", self._boom):
            common = dict(prize_addr=_rand(), gpu=object(), checkpoint=os.path.join(d, "ck"),
                          ledger=os.path.join(d, "l"), found_path=os.path.join(d, "F"))
            lines = []
            r = gpu_setperm.run_search(sp, log=lines.append, **common)                     # no signoff at all
            self.assertEqual((r.status, r.exit_code), ("REFUSED", 3))
            self.assertIn("sign-off", r.detail)
            self.assertIn("--signoff", r.detail)
            self.assertTrue(any("REFUSED" in str(x) for x in lines))
            for bad in (None, "", "   ", "\n\t", 0, 1, True, b"yes", ["yes"], ("yes",)):
                for extra in ({}, {"force": True}, {"max_hours": 168}):
                    r = gpu_setperm.run_search(sp, log=QUIET, signoff=bad, **common, **extra)
                    self.assertEqual((r.status, r.exit_code), ("REFUSED", 3), (bad, extra))
            self.assertEqual(os.listdir(d), [])

    def test_a_dry_run_needs_no_signoff(self):
        import gpu_setperm
        from sp_space import Space
        with mock.patch.object(gpu_setperm.Engine, "__init__", self._boom), mock.patch.object(gpu_setperm, "probe_rate", self._boom):
            r = gpu_setperm.run_search(Space.load(E4), prize_addr=_rand(), dry_run=True, log=QUIET)
        self.assertEqual((r.status, r.exit_code), ("DRY_RUN", 0))

    def test_the_signoff_is_logged_and_written_to_the_ledger(self):
        lines = []
        text = "  Operator 2026-09-30: T0, the other 61 templates  "
        _, r, _, _ = _fake_search(self.D, _rand(), ub=5, signoff=text, log=lines.append)
        self.assertEqual(r.status, "NO_MATCH", r.detail)
        recs = _ledger(r)
        self.assertEqual([x["status"] for x in recs], ["START", "NO_MATCH"])
        self.assertEqual([x["signoff"] for x in recs], [text.strip()] * 2)
        self.assertTrue(any(f"operator sign-off: {text.strip()}" in str(x) for x in lines), lines[:4])
        self.assertEqual(r.signoff, text.strip())

    def test_the_cli_hands_the_signoff_to_the_run(self):
        import gpu_setperm
        seen = {}

        def fake(space, **kw):
            seen.update(kw)
            return SimpleNamespace(exit_code=0)
        with mock.patch.object(gpu_setperm, "run_search", fake):
            with self.assertRaises(SystemExit) as cm:
                gpu_setperm.main([os.path.join(HERE, E4), "--checkpoint", "x.ck", "--signoff", "Operator: yes, e4"])
        self.assertEqual((cm.exception.code, seen["signoff"]), (0, "Operator: yes, e4"))
        self.assertIsNone(gpu_setperm.parse_args([E4, "--dry-run"]).signoff)

    def test_the_cli_exits_3_on_a_real_run_without_a_signoff(self):
        import gpu_setperm
        with tempfile.TemporaryDirectory() as d:
            # a NON-dry run: it must not carry the real prize address (run_search's default)
            with mock.patch.dict(gpu_setperm.run_search.__kwdefaults__, {"prize_addr": _rand()}), \
                    mock.patch.object(gpu_setperm.Engine, "__init__", self._boom), \
                    mock.patch.object(gpu_setperm, "probe_rate", self._boom), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                for argv in ([], ["--force"], ["--signoff", ""], ["--signoff", "  "]):
                    with self.assertRaises(SystemExit, msg=argv) as cm:
                        gpu_setperm.main([os.path.join(HERE, E4), "--checkpoint", os.path.join(d, "ck"), *argv])
                    self.assertEqual(cm.exception.code, 3, argv)
            self.assertIn("sign-off", out.getvalue())
            self.assertEqual(os.listdir(d), [])

    def test_the_search_step_itself_refuses_without_a_signoff(self):
        """The second line of defence: run_search's dry-run return was once deleted by a mutant and a test became a
        real search. The step that searches checks the sign-off itself, before it loads a checkpoint or builds an
        engine."""
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[self.D]))
        with tempfile.TemporaryDirectory() as d, mock.patch.object(gpu_setperm, "Engine", self._boom), \
                mock.patch.object(gpu_setperm, "combo_selftest", self._boom):
            for bad in (None, "", "  "):
                lines = []
                out = SimpleNamespace(status=None, exit_code=None, detail="")
                ck = gpu_setperm.Ckpt(os.path.join(d, "ck"), "0" * 16, 5)
                r = gpu_setperm._search(sp, out, ck, os.urandom(20), _rand(), "0" * 16, 5, 5, {"derivations": 1.0}, None,
                                        lines.append, object(), os.path.join(d, "l"), os.path.join(d, "F"), 1 << 22,
                                        signoff=bad)
                self.assertIs(r, out)
                self.assertEqual((r.status, r.exit_code), ("REFUSED", 3), bad)
                self.assertIn("sign-off", r.detail)
            self.assertEqual(os.listdir(d), [])

    def test_a_run_refused_by_the_cost_guard_has_run_the_rate_probe_and_nothing_else(self):
        """What the README used to get wrong: a real run measures its rate on the GPU (a dummy target and a planted
        seed, never the prize) BEFORE the cost guard decides. The search engine is never built and no file is
        written."""
        import gpu_setperm
        from sp_space import Space
        probes = []

        def fake_bench(space, **kw):
            probes.append(kw)
            return {"plant_found": True, "coverage_exact": True, "deriv_per_s": 1000.0}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(gpu_setperm.Engine, "__init__", self._boom), \
                mock.patch.object(gpu_setperm, "bench", fake_bench):
            r = gpu_setperm.run_search(Space.load(E4), prize_addr=_rand(), gpu=object(), signoff=SIGNOFF, log=QUIET,
                                       checkpoint=os.path.join(d, "ck"), ledger=os.path.join(d, "l"),
                                       found_path=os.path.join(d, "F"))
            self.assertEqual((r.status, r.exit_code, r.est["rate"]), ("REFUSED", 3, 1000.0))
            self.assertIn("--force", r.detail)
            self.assertEqual(len(probes), 1)
            self.assertEqual(os.listdir(d), [])


class ReviewDocsNoGpu(unittest.TestCase):
    def _texts(self):
        import gpu_setperm
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as f:
            readme = " ".join(f.read().lower().replace("`", "").split())
        return readme, " ".join(gpu_setperm.__doc__.lower().split())

    def test_the_docs_say_what_a_refused_run_touched(self):
        readme, doc = self._texts()
        self.assertNotIn("a refused run never touch", readme)
        self.assertIn("rate probe", readme)
        self.assertIn("before the cost guard decides", readme)
        self.assertIn("dummy target", readme)

    def test_the_docs_describe_the_signoff_and_the_plants(self):
        readme, doc = self._texts()
        for text, label in ((readme, "README"), (doc, "docstring")):
            for needle in ("--signoff", "sign-off", "64 plants", "slot reach", "(block + plant seed)", "two slots"):
                self.assertTrue(needle in text, (label, needle))
        self.assertIn("1 hit in 64", readme)                             # the fault the review injected, and what is left
        self.assertIn("statistical", readme)


# ---------------------------------------------------------------- GPU: the review fixes on the real kernels

def _mutant_k3_kernel(gpu, needle, replacement):
    """A scratch build of sp_derive.cl with one line changed (the real kernels/ file is untouched)."""
    import pyopencl as cl
    import sp_gpu
    import sp_space
    with open(os.path.join(sp_gpu.KDIR, "sp_derive.cl"), encoding="utf-8") as f:
        src = f.read()
    assert src.count(needle) == 1, needle
    parts = []
    for name in sp_gpu.BASE_CRYPTO:
        with open(os.path.join(sp_gpu.KDIR, name + ".cl"), encoding="utf-8") as f:
            parts.append(f.read())
    full = sp_space.cl_defines() + "\n" + "\n".join(parts) + "\n" + src.replace(needle, replacement)
    return cl.Kernel(cl.Program(gpu.ctx, full).build(options="-cl-std=CL1.2"), "sp_derive")


K3_MUTANTS = (
    ("target slot 0 never compared (loop starts at t=1)", "for (uint t = 0; t < ntgt; t++) {",
     "for (uint t = 1; t < ntgt; t++) {"),
    ("last target slot never compared", "for (uint t = 0; t < ntgt; t++) {", "for (uint t = 0; t + 1 < ntgt; t++) {"),
    ("key forms swapped", "rec[4] = j & 1;", "rec[4] = (j & 1) ^ 1;"),
    ("paths swapped", "rec[3] = j >> 1;", "rec[3] = (j >> 1) ^ 1;"),
    ("passphrases swapped", "rec[2] = pi;", "rec[2] = pi ^ 1;"),
    ("last path's uncompressed key never compared", "for (uint j = 0; j < npaths * 2; j++) {",
     "for (uint j = 0; j + 1 < npaths * 2; j++) {"),
)


@require_gpu
class ComboSelftestGpu(unittest.TestCase):
    """Fix 2 on the real kernels: every (passphrase, path, key form) is a known-answer test at the start of the run."""

    def test_it_passes_on_real_configs_with_many_passphrases_paths_and_both_schemes(self):
        import gpu_setperm
        from sp_space import Space
        cases = [(Space(cfg_from_text(dict(small_config_texts())[n])), n) for n in
                 ("A_bip39_12_r3k1", "D_electrum_12_wordlist", "E_electrum_13_alts", "F_electrum_12_freewords",
                  "J_bip39_12_dups", "K_bip39_12_distinct_overlap", "C_bip39_15_wordlist")]
        cases.append((Space.load(E4), "e4 (4 paths)"))
        for sp, name in cases:
            eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
            st = gpu_setperm.combo_selftest(sp, eng, seed=7)
            self.assertEqual(len(st.survivors), 2, name)
            self.assertEqual(st.combos, len(sp.passes) * len(sp.paths) * 2, name)
            self.assertEqual(st.known_answers, 2 * st.combos, name)

    def test_it_passes_beyond_one_round_of_targets(self):
        import gpu_setperm
        from sp_space import Space
        text = dict(small_config_texts())["A_bip39_12_r3k1"] + "".join(f"PASS more{i}\n" for i in range(70))
        sp = Space(cfg_from_text(text))
        eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
        st = gpu_setperm.combo_selftest(sp, eng, seed=3)
        self.assertGreaterEqual(st.rounds, 4)                                       # 292 combinations > 256 targets
        self.assertEqual(st.known_answers, 2 * len(sp.passes) * 2 * 2)

    def test_every_kernel_fault_is_refused_and_the_combination_is_named(self):
        import gpu_setperm
        from sp_gpu import Gpu
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["A_bip39_12_r3k1"]))
        eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
        real = eng.k3.k_hit
        gpu_setperm.combo_selftest(sp, eng, seed=7)                                  # the real kernel passes
        for name, needle, repl in K3_MUTANTS:
            eng.k3.k_hit = _mutant_k3_kernel(Gpu.get(), needle, repl)
            with self.assertRaises(gpu_setperm.EngineError, msg=name) as cm:
                gpu_setperm.combo_selftest(sp, eng, seed=7)
            self.assertIn("combo self-test", str(cm.exception), name)
            self.assertIn("missing", str(cm.exception), name)
        eng.k3.k_hit = real
        gpu_setperm.combo_selftest(sp, eng, seed=7)

    def test_a_kernel_fault_is_void_before_block_0_on_a_real_run(self):
        import gpu_setperm
        from sp_gpu import Gpu
        text = dict(small_config_texts())["A_bip39_12_r3k1"]
        orig = gpu_setperm.Engine.__init__
        name, needle, repl = K3_MUTANTS[0]

        def broken(self, space, targets, **kw):
            orig(self, space, targets, **kw)
            self.k3.k_hit = _mutant_k3_kernel(Gpu.get(), needle, repl)
        scanned = []
        real_scan = gpu_setperm.Engine.scan_block
        with mock.patch.object(gpu_setperm.Engine, "__init__", broken), \
                mock.patch.object(gpu_setperm.Engine, "scan_block",
                                  lambda self, lo, hi, hb=None: (scanned.append(hi - lo), real_scan(self, lo, hi, hb))[1]):
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0))
        self.assertIn("combo self-test", r.detail)
        self.assertIn("target #0", r.detail)
        self.assertTrue(scanned and set(scanned) == {1}, scanned)

    def test_the_plants_alone_now_see_a_fault_confined_to_one_target_slot(self):
        """With the self-test switched off: the reviewers' mutant (K3 loop from t=1) gave 40/40 false NO_MATCH when the
        plant always sat at slot 1. With a block's plants at every slot but the prize's (slot 11 of 48 in block 0
        here: plant seed 11), both that fault and its mirror image (last slot never compared) fail block 0."""
        import gpu_setperm
        from sp_gpu import Gpu
        text = dict(small_config_texts())["D_electrum_12_wordlist"]
        orig = gpu_setperm.Engine.__init__
        for (name, needle, repl), void_at in ((K3_MUTANTS[0], 0), (K3_MUTANTS[1], 0)):
            def broken(self, space, targets, needle=needle, repl=repl, **kw):
                orig(self, space, targets, **kw)
                self.k3.k_hit = _mutant_k3_kernel(Gpu.get(), needle, repl)
            with mock.patch.object(gpu_setperm.Engine, "__init__", broken), \
                    mock.patch.object(gpu_setperm, "plan_block_units", lambda space, target_deriv=4e6: max(1, space.units // 4)), \
                    mock.patch.object(gpu_setperm, "combo_selftest", lambda space, engine, seed=0: SimpleNamespace(
                        survivors=[], combos=0, known_answers=0, rounds=0)):
                _, r = _run(text, prize=address_of_hash160(os.urandom(20)))
            self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, void_at), name)
            self.assertIn(f"block {void_at}", r.detail, name)
            self.assertIn("1 of its", r.detail, name)                  # exactly the plant at the broken slot
            self.assertIn("was not found", r.detail, name)

    def test_a_prize_in_an_odd_block_is_found_on_the_real_engine(self):
        """A real prize hit in a later block (another K3 slot than in block 0) must still come out as FOUND."""
        import gpu_setperm
        from sp_space import Space
        text = dict(small_config_texts())["D_electrum_12_wordlist"]
        sp = Space(cfg_from_text(text))
        units4 = max(1, sp.units // 4)
        block = units4 * sp.per_unit
        idx = next(i for i in range(block, 2 * block) if sp.candidate_ok(i))       # inside block 1
        addr, m = _addr(sp, idx, 0, 0, 1)
        with mock.patch.object(gpu_setperm, "plan_block_units", lambda space, target_deriv=4e6: units4):
            _, r = _run(text, prize=addr)
        self.assertEqual((r.status, r.exit_code, r.found["mnemonic"], r.found["form"]), ("FOUND", 2, m, 1))
        self.assertIn(idx, r.found["indices"])
        self.assertEqual(r.blocks_done, 1)                                          # blocks 0 done, block 1 found it


@require_gpu
class BinomialGpu(unittest.TestCase):
    """Fix 3: an honest engine passes the per-block binomial check on real windows (reviewers measured max |z| 3.1)."""

    def test_honest_e4_shaped_windows_pass(self):
        import random
        from gpu_setperm import binomial_problem
        from sp_gpu import Gpu
        from sp_k1_enum import EnumKernel
        from sp_space import Space
        sp = Space.load(E4)
        k1 = EnumKernel(Gpu.get(), sp, cap=1 << 20)
        rate = sp.estimate()["valid_rate"]
        rng = random.Random(4)
        zs = []
        for _ in range(6):
            start = rng.randrange(0, sp.total - (1 << 21))
            tested = valid = 0
            for off in range(0, 1 << 21, 1 << 19):
                _, t, v = k1.run(start + off, 1 << 19)
                tested, valid = tested + t, valid + v
            self.assertGreaterEqual(tested, 10_000)
            self.assertIsNone(binomial_problem(tested, valid, rate), (start, tested, valid))
            zs.append((valid - tested * rate) / (tested * rate * (1 - rate)) ** 0.5)
        self.assertLess(max(abs(z) for z in zs), 8)

    def test_the_small_configs_pass_and_a_lossy_engine_would_not(self):
        from gpu_setperm import binomial_problem
        from sp_gpu import Gpu
        from sp_k1_enum import EnumKernel
        from sp_space import Space
        from gpu_setperm import BINOM_MIN_TESTED
        judged = 0
        for name, text in small_config_texts():
            sp = Space(cfg_from_text(text))
            k1 = EnumKernel(Gpu.get(), sp)
            _, tested, valid = k1.run(0, sp.total)
            rate = sp.estimate()["valid_rate"]
            self.assertIsNone(binomial_problem(tested, valid, rate), name)
            if tested >= BINOM_MIN_TESTED:                                          # judged blocks only
                loss = int(10 * (tested * rate * (1 - rate)) ** 0.5) + 1            # a >= 10 sigma survivor loss
                if valid > loss:
                    judged += 1
                    self.assertIsNotNone(binomial_problem(tested, valid - loss, rate), name)
        self.assertGreaterEqual(judged, 3)


@require_gpu
class ChunkBoundaryGpu(unittest.TestCase):
    """Fix 11 on the GPU: a K1 chunk of exactly block_size-1 leaves a one-candidate tail chunk."""

    def test_a_run_with_a_chunk_of_block_size_minus_one_is_exact(self):
        from sp_space import Space
        for name in ("J_bip39_12_dups", "G_bip39_12_k0", "D_electrum_12_wordlist"):
            text = dict(small_config_texts())[name]
            sp = Space(cfg_from_text(text))
            with _tiny_launches(chunk=sp.total - 1, batch=3):
                _, r = _run(text, prize=address_of_hash160(os.urandom(20)))
            tested, valid, _ = sp_ref.ref_survivors(cfg_from_text(text))
            self.assertEqual((r.status, r.tested, r.valid), ("NO_MATCH", tested, valid), name)

    def test_a_prize_in_the_one_candidate_tail_is_found(self):
        from sp_space import Space
        text = dict(small_config_texts())["J_bip39_12_dups"]
        sp = Space(cfg_from_text(text))
        last = max(sp_ref.ref_survivors(sp.cfg)[2])
        addr, m = _addr(sp, last, 0, 0, 0)
        with _tiny_launches(chunk=last, batch=3):                                    # chunks [0, last) and [last, ...)
            _, r = _run(text, prize=addr)
        self.assertEqual((r.status, r.found["mnemonic"]), ("FOUND", m))
        self.assertIn(last, r.found["indices"])


@require_gpu
class WrappersRecordSlicesOnTheEngine(unittest.TestCase):
    """Fix 4 with the real wrappers: a consumer-side shift of the slice handed to K2 or K3 is refused on the GPU."""

    def test_a_shifted_slice_is_refused_by_the_real_engine(self):
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())["J_bip39_12_dups"]))
        for who in ("k2", "k3", "both"):
            for name, shift in (("off+1", lambda off, n: (off + 1, n)), ("nsurv-1", lambda off, n: (off, n - 1))):
                with _tiny_launches(chunk=101, batch=3):
                    eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
                if who in ("k2", "both"):
                    real2 = eng.k2.launch
                    eng.k2.launch = lambda surv, off, n, real2=real2, shift=shift: real2(surv, *shift(off, n))
                if who in ("k3", "both"):
                    real3 = eng.k3.run
                    eng.k3.run = lambda seeds, surv, off, n, npass, real3=real3, shift=shift: real3(
                        seeds, surv, *shift(off, n), npass)
                with self.assertRaises((gpu_setperm.EngineError, ValueError), msg=(who, name)):
                    eng.scan_block(0, sp.total)
        # and the honest engine passes the same scan, with recorded slices that match the tiles
        with _tiny_launches(chunk=101, batch=3):
            eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
        res = eng.scan_block(0, sp.total)
        self.assertEqual(res.processed, res.valid)
        self.assertEqual(eng.k2.last_slice, eng.k3.last_slice)


# ---------------------------------------------------------------- GPU: the window audit, the slot sweep, tested == scanned

def _scratch_kernel(gpu, files, source, kernel, needle, replacement):
    """`kernel` of a scratch build of kernels/<source>.cl with one piece of text replaced, after the given kernel files
    (the real kernels/ files are untouched, and so is every engine that is not handed this kernel)."""
    import pyopencl as cl
    import sp_gpu
    import sp_space
    with open(os.path.join(sp_gpu.KDIR, source + ".cl"), encoding="utf-8") as f:
        src = f.read()
    assert src.count(needle) == 1, needle
    parts = []
    for name in files:
        with open(os.path.join(sp_gpu.KDIR, name + ".cl"), encoding="utf-8") as f:
            parts.append(f.read())
    full = sp_space.cl_defines() + "\n" + "\n".join(parts) + "\n" + src.replace(needle, replacement)
    return cl.Kernel(cl.Program(gpu.ctx, full).build(options="-cl-std=CL1.2"), kernel)


K1_FILES = ["common", "sha2", "sp_sha512", "sp_decode"]
K2_FILES = ["sp_sha512", "sp_decode"]
# (name, installer of the scratch kernel on an engine, needle, replacement)
K1_DROPS_MOD8 = ("K1 never emits the survivors of idx % 8 == 0 (and does not count them)",
                 "      if (ok) {\n        atomic_inc(&lc[2]);", "      if (ok && (idx & 7UL) != 0UL) {\n        atomic_inc(&lc[2]);")
K1_SKIPS_MOD512 = ("K1 skips every candidate with idx % 512 == 0 after counting it as scanned (tested and valid shrink together)",
                   "    if (ok) {\n      atomic_inc(&lc[1]);", "    if (ok && (idx & 511UL) != 0UL) {\n      atomic_inc(&lc[1]);")
K2_ZEROES_EVEN = ("K2 writes zero seeds for the survivors with an even index",
                  "seeds[(ulong)gid * 8 + i] = t[i];",
                  "seeds[(ulong)gid * 8 + i] = ((surv[surv_off + si] & 1UL) == 0UL) ? 0UL : t[i];")
K3_DUMP_WRONG = ("K3's dump returns a wrong hash160 (first byte flipped) for every even thread",
                 "for (uint i = 0; i < npaths * 40; i++) out[(ulong)gid * npaths * 40 + i] = h[i];",
                 "for (uint i = 0; i < npaths * 40; i++) out[(ulong)gid * npaths * 40 + i] = "
                 "(i == 0 && (gid & 1u) == 0u) ? (uchar)(h[i] ^ 1) : h[i];")


def _install(which, spec):
    """A `fault(engine)` that swaps one real kernel of the engine for the scratch build with `spec`'s change."""
    def apply(eng):
        _, needle, repl = spec
        if which == "k1":
            eng.k1.knl = _scratch_kernel(eng.gpu, K1_FILES, "sp_enum", "sp_enum", needle, repl)
        elif which == "k2":
            eng.k2.knl = _scratch_kernel(eng.gpu, K2_FILES, "sp_pbkdf2", "sp_pbkdf2", needle, repl)
        elif which == "k3dump":
            import sp_gpu
            eng.k3.k_dump = _scratch_kernel(eng.gpu, sp_gpu.BASE_CRYPTO, "sp_derive", "sp_derive_dump", needle, repl)
        else:
            raise AssertionError(which)
    return apply


def _clean_seed(text, units_per_block, bad, tries=5000):
    """A plant seed for which neither the combo self-test's survivors nor any block's plant is `bad`: a fault confined
    to the `bad` candidates is invisible to those two controls, so a VOID can only come from the audit / new control."""
    import gpu_setperm
    from sp_space import Space
    sp = Space(cfg_from_text(text))
    block = units_per_block * sp.per_unit
    nb = -(-sp.units // units_per_block)
    for seed in range(tries):
        if any(bad(i) for i in gpu_setperm.selftest_survivors(sp, seed)):
            continue
        plants = [gpu_setperm.make_block_plant(sp, seed, b, b * block, min(sp.total, (b + 1) * block)) for b in range(nb)]
        if all(p is None or not bad(p.idx) for p in plants):
            return seed
    raise AssertionError("no clean plant seed found")


@contextlib.contextmanager
def _engine_fault(fault, units_per_block=None, audit=True, plants=None):
    """Every Engine built inside gets `fault(engine)` applied; blocks of `units_per_block` units; with audit=False the
    window audit is switched off (to show what the run's OTHER controls make of the same fault); `plants` sets the
    plants per block (1: the single plant _clean_seed picks a dodging seed for)."""
    import gpu_setperm
    orig = gpu_setperm.Engine.__init__

    def broken(self, space, targets, **kw):
        orig(self, space, targets, **kw)
        fault(self)
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(gpu_setperm.Engine, "__init__", broken))
        if units_per_block:
            stack.enter_context(mock.patch.object(gpu_setperm, "plan_block_units", lambda space, target_deriv=4e6: units_per_block))
        if not audit:
            stack.enter_context(mock.patch.object(gpu_setperm.Engine, "k1_spot_check", lambda self, lo, hi, seed, width=None: None))
        if plants is not None:
            stack.enter_context(mock.patch.object(gpu_setperm, "PLANTS_PER_BLOCK", plants))
        yield


@require_gpu
class WindowAuditGpu(unittest.TestCase):
    """Engine.k1_spot_check on the real kernels: an honest engine passes, and kernel faults that ONLY the audit can see
    (every other control of the run holds for them) are refused and are VOID in the FIRST block."""

    D = "D_electrum_12_wordlist"

    def _sp(self, name=None):
        from sp_space import Space
        return Space(cfg_from_text(dict(small_config_texts())[name or self.D]))

    def test_an_honest_engine_passes_on_real_configs_and_the_audit_leaves_the_engine_alone(self):
        import time
        import gpu_setperm
        from sp_space import Space
        cases = [(self._sp(n), n, None) for n in ("A_bip39_12_r3k1", "C_bip39_15_wordlist", "D_electrum_12_wordlist",
                                                   "E_electrum_13_alts", "F_electrum_12_freewords", "J_bip39_12_dups",
                                                   "K_bip39_12_distinct_overlap")]
        cases.append((Space.load(E4), "e4 (4 paths)", (5 * 10 ** 9, 6 * 10 ** 9)))
        for sp, name, span in cases:
            lo, hi = span or (0, sp.total)
            targets = [os.urandom(20), os.urandom(20)]
            eng = gpu_setperm.Engine(sp, targets, log=QUIET)
            sizes = (eng.s1.n, eng.s2.n)
            t0 = time.perf_counter()
            a = eng.k1_spot_check(lo, hi, 3)
            dt = time.perf_counter() - t0
            w = min(gpu_setperm.SPOT_WIDTH, hi - lo)
            self.assertEqual(a.window[1], w, name)
            self.assertTrue(lo <= a.window[0] and a.window[0] + w <= hi, name)
            want = [i for i in range(a.window[0], a.window[0] + w) if sp.candidate_ok(i)]
            self.assertEqual(a.survivors, len(want), name)
            self.assertGreater(a.survivors, 0, name)
            self.assertEqual(a.checked, min(len(want), 16) * len(sp.passes), name)
            self.assertFalse(a.skipped, name)
            self.assertLess(dt, 10, (name, dt))                          # e4: ~0.1 s of CPU work + three tiny launches
            self.assertEqual((eng.s1.n, eng.s2.n), sizes, name)
            self.assertEqual(eng.k3.ntgt, 2, name)                       # the block's targets are untouched
            end = min(hi, lo + 20_000)
            res = eng.scan_block(lo, end)                                # and the engine still scans exactly afterwards
            self.assertEqual((res.scanned, res.processed), (end - lo, res.valid), name)

    def test_a_k1_that_never_emits_the_survivors_of_idx_mod_8_is_refused_and_void_in_block_0(self):
        import gpu_setperm
        sp = self._sp()
        eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
        eng.k1_spot_check(0, sp.total, 3)                                 # honest: passes
        _install("k1", K1_DROPS_MOD8)(eng)
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            eng.k1_spot_check(0, sp.total, 3)
        self.assertIn("survivor set differs", str(cm.exception))
        self.assertRegex(str(cm.exception), r"[1-9]\d* missing")
        units4 = max(1, sp.units // 4)
        seed = _clean_seed(dict(small_config_texts())[self.D], units4, lambda i: i % 8 == 0)
        text = dict(small_config_texts())[self.D]
        with _engine_fault(_install("k1", K1_DROPS_MOD8), units4, plants=1):       # one plant, and it dodges the fault
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)), plant_seed=seed)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("block 0", r.detail)
        self.assertIn("window audit", r.detail)
        self.assertIn("survivor set differs", r.detail)
        with _engine_fault(_install("k1", K1_DROPS_MOD8), units4, audit=False, plants=1):   # every other control holds: a
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)), plant_seed=seed)    # FALSE NO MATCH
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        with _engine_fault(_install("k1", K1_DROPS_MOD8), units4, audit=False):     # ... with one plant; 64 plants see it too
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)), plant_seed=seed)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("not found", r.detail)

    def test_a_k2_that_writes_zero_seeds_for_even_survivors_is_refused_and_void_in_block_0(self):
        import gpu_setperm
        sp = self._sp()
        eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
        _install("k2", K2_ZEROES_EVEN)(eng)
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            eng.k1_spot_check(0, sp.total, 3)
        self.assertIn("K2/K3 parity", str(cm.exception))
        self.assertIn("passphrase #0", str(cm.exception))
        units4 = max(1, sp.units // 4)
        text = dict(small_config_texts())[self.D]
        seed = _clean_seed(text, units4, lambda i: i % 2 == 0)
        with _engine_fault(_install("k2", K2_ZEROES_EVEN), units4, plants=1):      # one plant, and it dodges the fault
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)), plant_seed=seed)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("block 0", r.detail)
        self.assertIn("window audit", r.detail)
        self.assertIn("K2/K3 parity", r.detail)
        with _engine_fault(_install("k2", K2_ZEROES_EVEN), units4, audit=False, plants=1):
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)), plant_seed=seed)
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        with _engine_fault(_install("k2", K2_ZEROES_EVEN), units4, audit=False):   # ... with one plant; 64 plants see it too
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)), plant_seed=seed)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("not found", r.detail)

    def test_a_k3_dump_that_returns_a_wrong_hash160_is_refused_and_void_in_block_0(self):
        import gpu_setperm
        sp = self._sp()
        eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
        _install("k3dump", K3_DUMP_WRONG)(eng)
        with self.assertRaises(gpu_setperm.EngineError) as cm:
            eng.k1_spot_check(0, sp.total, 3)
        self.assertIn("K2/K3 parity", str(cm.exception))
        self.assertIn("path #0", str(cm.exception))
        self.assertIn("form 0", str(cm.exception))
        units4 = max(1, sp.units // 4)
        text = dict(small_config_texts())[self.D]
        with _engine_fault(_install("k3dump", K3_DUMP_WRONG), units4):
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("block 0", r.detail)
        self.assertIn("K2/K3 parity", r.detail)
        with _engine_fault(_install("k3dump", K3_DUMP_WRONG), units4, audit=False):
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)

    def test_every_multi_pass_multi_path_config_is_audited_against_all_its_hash160s(self):
        """A (3 passphrases x 2 paths x 2 forms) and E (3 paths): the audit compares every (passphrase, path, form)."""
        import gpu_setperm
        for name in ("A_bip39_12_r3k1", "E_electrum_13_alts"):
            sp = self._sp(name)
            eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
            a = eng.k1_spot_check(0, sp.total, 8)
            self.assertEqual(a.checked, min(a.survivors, 16) * len(sp.passes), name)
            real = eng.k3.dump

            def flipped(seeds, real=real):
                out = real(seeds).copy()
                out[-1, -1, 1, 19] ^= 0x80                                 # the LAST thread, LAST path, uncompressed key
                return out
            eng.k3.dump = flipped
            with self.assertRaises(gpu_setperm.EngineError, msg=name) as cm:
                eng.k1_spot_check(0, sp.total, 8)
            self.assertIn(f"passphrase #{len(sp.passes) - 1}", str(cm.exception), name)
            self.assertIn(f"path #{len(sp.paths) - 1}", str(cm.exception), name)
            self.assertIn("form 1", str(cm.exception), name)

    def test_a_k1_that_skips_candidates_after_counting_them_is_void_by_tested_equals_scanned_not_by_the_audit(self):
        """tested and valid shrink together (~0.2% of the candidates are never evaluated): the binomial check sees only
        their ratio, the audit's one window rarely holds a survivor among the skipped ones; tested == scanned sees it."""
        sp = self._sp()
        units4 = max(1, sp.units // 4)
        text = dict(small_config_texts())[self.D]
        seed = _clean_seed(text, units4, lambda i: i % 512 == 0)
        with _engine_fault(_install("k1", K1_SKIPS_MOD512), units4):
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)), plant_seed=seed)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("block 0", r.detail)
        self.assertIn("tested", r.detail)
        self.assertIn("DISTINCT", r.detail)
        self.assertNotIn("binomial", r.detail)


K3_SWEEP_MUTANTS = (
    # name, needle -> replacement, does the main round alone (every combination at ONE slot) already see it?
    ("slot 1 never reports passphrase #2", "for (uint t = 0; t < ntgt; t++) {",
     "for (uint t = 0; t < ntgt; t++) { if (t == 1 && pi == 2) continue;"),
    ("slot 1 never reports path #1", "for (uint t = 0; t < ntgt; t++) {",
     "for (uint t = 0; t < ntgt; t++) { if (t == 1 && (j >> 1) == 1) continue;"),
    ("slot 0 never reports passphrase #1", "for (uint t = 0; t < ntgt; t++) {",
     "for (uint t = 0; t < ntgt; t++) { if (t == 0 && pi == 1) continue;"),
    ("a single target never reports passphrase #2", "for (uint t = 0; t < ntgt; t++) {",
     "for (uint t = 0; t < ntgt; t++) { if (ntgt == 1 && pi == 2) continue;"),
    ("slot 1 never reports the uncompressed key", "for (uint t = 0; t < ntgt; t++) {",
     "for (uint t = 0; t < ntgt; t++) { if (t == 1 && (j & 1) == 1) continue;"),
)


@require_gpu
class SlotSweepGpu(unittest.TestCase):
    """The slot sweep on the real kernels: kernel faults keyed on (target slot or count, one value of one dimension)."""

    A = "A_bip39_12_r3k1"

    def test_the_sweep_passes_on_real_configs_and_covers_every_value(self):
        import gpu_setperm
        from sp_space import Space
        cases = [(Space(cfg_from_text(dict(small_config_texts())[n])), n) for n in
                 ("A_bip39_12_r3k1", "D_electrum_12_wordlist", "E_electrum_13_alts", "J_bip39_12_dups")]
        cases.append((Space.load(E4), "e4 (4 paths)"))
        for sp, name in cases:
            eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
            st = gpu_setperm.combo_selftest(sp, eng, seed=7)
            most = max(len(sp.passes), len(sp.paths), 2)
            self.assertGreaterEqual(st.sweep_rounds, 2 * 2 * most, name)         # >= 2 rounds per value, 2 survivors
            self.assertGreater(st.sweep_answers, 0, name)

    def test_every_slot_keyed_kernel_fault_is_refused_before_block_0_and_the_sweep_is_what_names_it(self):
        import gpu_setperm
        from sp_gpu import Gpu
        from sp_space import Space
        sp = Space(cfg_from_text(dict(small_config_texts())[self.A]))
        eng = gpu_setperm.Engine(sp, [os.urandom(20)], log=QUIET)
        real = eng.k3.k_hit
        sweep_only = 0
        for name, needle, repl in K3_SWEEP_MUTANTS:
            eng.k3.k_hit = _mutant_k3_kernel(Gpu.get(), needle, repl)
            with self.assertRaises(gpu_setperm.EngineError, msg=name) as cm:
                gpu_setperm.combo_selftest(sp, eng, seed=7)
            self.assertIn("combo self-test", str(cm.exception), name)
            try:
                with _main_rounds_only():
                    gpu_setperm.combo_selftest(sp, eng, seed=7)
            except gpu_setperm.EngineError:
                continue                                                     # the main rounds already see this one
            sweep_only += 1
            self.assertIn("slot sweep", str(cm.exception), name)             # the main rounds alone would have passed
        self.assertGreaterEqual(sweep_only, 4, sweep_only)
        eng.k3.k_hit = real
        gpu_setperm.combo_selftest(sp, eng, seed=7)                          # and the real kernel still passes

    def test_a_slot_keyed_kernel_fault_is_void_before_block_0_on_a_real_run(self):
        import gpu_setperm
        from sp_gpu import Gpu
        text = dict(small_config_texts())[self.A]
        name, needle, repl = K3_SWEEP_MUTANTS[0]
        orig = gpu_setperm.Engine.__init__

        def broken(self, space, targets, **kw):
            orig(self, space, targets, **kw)
            self.k3.k_hit = _mutant_k3_kernel(Gpu.get(), needle, repl)
        scanned = []
        real_scan = gpu_setperm.Engine.scan_block
        with mock.patch.object(gpu_setperm.Engine, "__init__", broken), \
                mock.patch.object(gpu_setperm.Engine, "scan_block",
                                  lambda self, lo, hi, hb=None: (scanned.append(hi - lo), real_scan(self, lo, hi, hb))[1]):
            _, r = _run(text, prize=address_of_hash160(os.urandom(20)))
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("combo self-test", r.detail)
        self.assertIn("slot sweep", r.detail)
        self.assertTrue(scanned and set(scanned) == {1}, scanned)            # no block was scanned


if __name__ == "__main__":
    unittest.main()


@require_gpu
class ManyPlantsGpu(unittest.TestCase):
    """The review's fix on the real kernels: 64 plants in every block, the prize slot rotating with block and seed."""

    D = "D_electrum_12_wordlist"
    QUARTER = staticmethod(lambda space, target_deriv=4e6: max(1, space.units // 4))

    def test_an_honest_engine_finds_every_plant_of_every_block(self):
        import gpu_setperm
        for name in (self.D, "A_bip39_12_r3k1", "J_bip39_12_dups", "K_bip39_12_distinct_overlap"):
            text = dict(small_config_texts())[name]
            with mock.patch.object(gpu_setperm, "plan_block_units", self.QUARTER):
                sp, r = _run(text, prize=_rand())
            self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), (name, r.detail))
            self.assertGreaterEqual(r.blocks_verified, 4, name)
            block = self.QUARTER(sp) * sp.per_unit
            want = sum(len(gpu_setperm.make_block_plants(sp, 11, b, lo, min(sp.total, lo + block)))
                       for b, lo in enumerate(range(0, sp.total, block)))
            self.assertEqual(r.plants_found, want, name)                 # every plant the CPU made, none skipped
            self.assertGreaterEqual(want, 9, name)
            self.assertGreater(r.selftest.reach_answers, 0, name)

    def test_a_hit_kernel_fault_tied_to_the_thread_is_void(self):
        """K3 loses the hits of every fourth thread. The self-test passes (its windows are 1 wide: thread 0) and so
        does the window audit (it reads the dump kernel); the plants see it in the first block."""
        import gpu_setperm
        from sp_gpu import Gpu
        text = dict(small_config_texts())[self.D]
        orig = gpu_setperm.Engine.__init__

        def broken(self, space, targets, **kw):
            orig(self, space, targets, **kw)
            self.k3.k_hit = _mutant_k3_kernel(Gpu.get(), "      if (eq) {", "      if (eq && (gid & 3u) != 3u) {")
        with mock.patch.object(gpu_setperm.Engine, "__init__", broken), \
                mock.patch.object(gpu_setperm, "plan_block_units", self.QUARTER):
            _, r = _run(text, prize=_rand())
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("not found", r.detail)
        self.assertIsNotNone(r.selftest)                                 # the self-test ran and passed

    def test_a_fault_confined_to_one_slot_cannot_lose_a_prize_that_sits_at_two_slots(self):
        """On the real kernels: K3 never reports a hit at ONE target slot in a launch of more than one thread. The
        self-test passes (1-wide windows of one thread), the window audit passes (dump kernel), and the broken slot
        is the prize's first slot, so no plant of this one-block run sits there. With one copy of the prize that is a
        FALSE NO MATCH over a seed that is really there; with two, the other slot reports it."""
        import gpu_setperm
        from sp_gpu import Gpu
        from sp_space import Space
        text = dict(small_config_texts())[self.D]
        sp = Space(cfg_from_text(text))
        planted = {q.idx for q in gpu_setperm.make_block_plants(sp, 5, 0, 0, sp.total)}
        idx = next(i for i in range(sp.total // 2, sp.total) if sp.candidate_ok(i) and i not in planted)
        addr, m = _addr(sp, idx, 0, 0, 1)
        orig = gpu_setperm.Engine.__init__
        got = {}
        for copies in (1, 2):
            with mock.patch.object(gpu_setperm, "PRIZE_COPIES", copies):
                slot = _prize_slots(len(planted), 0, 5, copies)[0]

                def broken(self, space, targets, slot=slot, **kw):
                    orig(self, space, targets, **kw)
                    self.k3.k_hit = _mutant_k3_kernel(Gpu.get(), "      if (eq) {",
                                                      f"      if (eq && !(t == {slot}u && nthreads > 1u)) {{")
                with mock.patch.object(gpu_setperm.Engine, "__init__", broken):
                    _, r = _run(text, prize=addr, plant_seed=5)
                got[copies] = (r.status, r.found and r.found["mnemonic"], r.blocks_verified)
        self.assertEqual(got[1], ("NO_MATCH", None, 1))                  # every control of the run held
        self.assertEqual(got[2], ("FOUND", m, 0))

    def test_a_prize_is_found_at_every_slot_it_takes(self):
        import gpu_setperm
        from sp_space import Space
        text = dict(small_config_texts())[self.D]
        sp = Space(cfg_from_text(text))
        block = self.QUARTER(sp) * sp.per_unit
        for b in range(4):
            idx = next(i for i in range(b * block, (b + 1) * block) if sp.candidate_ok(i))
            addr, m = _addr(sp, idx, 0, 0, b % 2)
            with mock.patch.object(gpu_setperm, "plan_block_units", self.QUARTER):
                _, r = _run(text, prize=addr, plant_seed=20 + b)
            self.assertEqual((r.status, r.exit_code, r.found["mnemonic"], r.found["form"]), ("FOUND", 2, m, b % 2), b)
            self.assertEqual(r.blocks_done, b)


# ---------------------------------------------------------------- bip39raw: no filter, so an EXACT survivor-count check

# 13 words with upper-case tokens (TOWER, MOON, ABANDON), non-BIP39 ones (breathe, quixotic) and case variants that are
# different tokens (TOWER|tower, Zoo/zoo); 2 passphrases besides '' (MOON and moon: two seeds) and 2 paths. 4 free
# slots, REQUIRE 2, a 2-way FIX alternative, k=2 over 6 tokens: 864 candidates, every one derived
RAW_E2E = make_text("bip39raw", 13, [(1, "TOWER|tower")] + list(zip((3, 4, 6, 7, 8, 10, 11, 12),
                    ("zoo", "wrong", "abandon", "about", "zoo", "wrong", "zoo", "x"))), ["MOON", "breathe"],
                    ["Zoo zoo quixotic wrong abandon ABANDON"], passes=("MOON", "moon"),
                    paths=("m/44'/0'/0'/0/0", "m/0/0"))
# small enough for the CPU stand-in engine, which derives every candidate on the CPU: 3 free, REQUIRE 1, k=2 over 5: 75
RAW_TINY = make_text("bip39raw", 13, list(zip((1, 3, 4, 6, 7, 8, 10, 11, 12, 13),
                     ("TOWER", "zoo", "wrong", "abandon", "about", "zoo", "wrong", "zoo", "x", "y"))), ["MOON"],
                     ["Zoo zoo breathe quixotic ABANDON"])
# larger than a deliberately small survivor buffer: r=0, k=2 over 70 tokens, one passphrase, one path: 4,900
RAW_BIG = make_text("bip39raw", 12, [(s, "zoo") for s in range(1, 13) if s not in (3, 8)], [],
                    [" ".join(WORDS[100:169] + ["MOON"])])


class RawRunNoGpu(unittest.TestCase):
    """bip39raw has no filter: the binomial check (whose sigma is 0 at a pass rate of 1) is replaced by an EXACT one,
    survivors == tested in every block whatever its size; the banner says which check ran, the filtered schemes'
    banner is unchanged."""

    L = "L_raw_13_case_free"

    def test_an_honest_raw_run_is_a_no_match_whose_banner_names_the_exact_check(self):
        _, r, _, _ = _fake_search(self.L, _rand())
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        self.assertIn("exact survivor-count check", r.detail)
        self.assertNotIn("binomial", r.detail)
        _, r, _, _ = _fake_search("D_electrum_12_wordlist", _rand(), ub=5)
        self.assertIn("slots), binomial survivor-count check, tested == scanned where there is no DISTINCT,", r.detail)
        self.assertNotIn("exact survivor-count", r.detail)

    def test_survivors_other_than_tested_are_void_by_the_exact_check_even_in_a_block_too_small_to_judge(self):
        from gpu_setperm import BINOM_MIN_TESTED
        for delta in (-1, 1, -7):
            _, r, _, blocks = _fake_search(self.L, _rand(), size=lambda lo, hi, d=delta: (hi - lo, hi - lo + d))
            self.assertLess(blocks[0][1] - blocks[0][0], BINOM_MIN_TESTED)      # the binomial check never judged it
            self.assertEqual((r.status, r.exit_code, r.blocks_done, len(blocks)), ("VOID", 4, 0, 1), (delta, r.detail))
            for needle in ("block 0", "exact check", "no filter"):
                self.assertIn(needle, r.detail, delta)
            self.assertNotIn("binomial", r.detail, delta)
        # with DISTINCT, tested may fall short of scanned, but every tested candidate must still survive
        m = "M_raw_12_nochecksum_distinct"
        _, r, _, _ = _fake_search(m, _rand(), size=lambda lo, hi: (hi - lo - 3, hi - lo - 3))
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        _, r, _, _ = _fake_search(m, _rand(), size=lambda lo, hi: (hi - lo - 3, hi - lo - 4))
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("exact check", r.detail)

    def test_the_cpu_engine_gives_an_exact_no_match_and_a_k1_that_drops_a_survivor_is_void(self):
        from sp_space import Space
        sp = Space(cfg_from_text(RAW_TINY))
        _, r, _ = _cpu_run(RAW_TINY, _rand())
        self.assertEqual((r.status, r.exit_code, r.plants_ok), ("NO_MATCH", 0, True), r.detail)
        self.assertEqual((r.tested, r.valid), (sp.total, sp.total))
        self.assertIn("exact survivor-count check", r.detail)

        def drop_one(eng):
            real = eng.k1.run

            def run(start, count):
                s, t, v = real(start, count)
                return (s, t, v - 1) if count > 1 and v else (s, t, v)     # not the combo self-test's 1-wide windows
            eng.k1.run = run
        _, r, _ = _cpu_run(RAW_TINY, _rand(), fault=drop_one)
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("exact check", r.detail)


@require_gpu
class RawRunGpu(unittest.TestCase):
    """bip39raw on the real kernels (K1 in mode 0: no filter): every candidate survives and goes through K2 and K3.
    Every target is self-generated, never the prize."""

    def test_a_hit_is_found_with_its_exact_text_passphrase_path_and_key_form(self):
        from sp_space import Space
        sp = Space(cfg_from_text(RAW_E2E))
        self.assertEqual((sp.mode, sp.total, sp.passes, sp.paths_txt),
                         (0, 864, ["", "MOON", "moon"], ["m/44'/0'/0'/0/0", "m/0/0"]))
        for form, idx in ((0, sp.total // 2 + 7), (1, sp.total - 5)):
            addr, m = _addr(sp, idx, 1, 1, form)
            self.assertTrue({"MOON", "breathe"} <= set(m.split()), m)
            _, r = _run(RAW_E2E, prize=addr)
            self.assertEqual((r.status, r.exit_code), ("FOUND", 2), r.detail)
            self.assertEqual((r.found["mnemonic"], r.found["passphrase"], r.found["path"], r.found["form"],
                              r.found["scheme"], r.found["indices"]), (m, "MOON", "m/0/0", form, "bip39raw", [idx]))

    def test_a_target_outside_the_space_is_a_verified_no_match_with_every_candidate_tested_and_valid(self):
        from sp_space import Space
        sp = Space(cfg_from_text(RAW_E2E))
        m = " ".join(sp.decode(sp.total // 3)).replace("MOON", "Moon")        # one token's case changed: not in the space
        near = address_of_hash160(sp_ref.hash160_forms(m, "MOON", [sp.paths[0]], "bip39raw")[0][2])
        for target in (near, _rand()):
            _, r = _run(RAW_E2E, prize=target)
            self.assertEqual((r.status, r.exit_code, r.plants_ok), ("NO_MATCH", 0, True), r.detail)
            self.assertEqual((r.tested, r.valid), (sp.total, sp.total))
            self.assertEqual(r.plants_found, 64)
            self.assertEqual((r.audits, r.audits_skipped), (r.blocks_verified, 0))
            self.assertGreater(r.selftest.known_answers, 0)
            self.assertIn("exact survivor-count check", r.detail)

    def test_a_space_larger_than_a_small_survivor_buffer_is_covered_exactly_and_no_k1_launch_overflows(self):
        """Without a filter every scanned candidate survives: K1's launches must stay within the survivor buffer
        (an overflowing launch is redone smaller, again and again; below the sizer's 64-candidate floor it was an
        EngineError). The window audit's own window is larger than such a buffer: skipped, and counted."""
        import gpu_setperm
        import sp_k1_enum
        from sp_space import Space
        sp = Space(cfg_from_text(RAW_BIG))
        real_scan, real_run = gpu_setperm.Engine.scan_block, sp_k1_enum.EnumKernel.run
        for cap in (32, 1000):
            launches, inside = [], [False]

            def scan(self, start, end, heartbeat=None):
                inside[0] = True
                try:
                    return real_scan(self, start, end, heartbeat)
                finally:
                    inside[0] = False

            def run(self, start, count):
                out = real_run(self, start, count)
                if inside[0]:
                    launches.append((count, out[2], self.cap))
                return out
            with mock.patch.object(gpu_setperm.Engine, "scan_block", scan), \
                    mock.patch.object(sp_k1_enum.EnumKernel, "run", run):
                _, r = _run(RAW_BIG, prize=_rand(), surv_cap=cap)
            self.assertEqual((r.status, r.exit_code, r.plants_ok), ("NO_MATCH", 0, True), (cap, r.detail))
            self.assertEqual((r.tested, r.valid), (sp.total, sp.total), cap)
            self.assertTrue(launches and all(c == cap for _, _, c in launches), cap)
            self.assertEqual([x for x in launches if x[1] > x[2]], [], cap)        # no launch overflowed: none was redone
            self.assertGreater(sum(x[0] for x in launches), sp.total, cap)       # the block + the self-test's windows
            self.assertEqual(r.audits_skipped, r.audits, cap)

    def test_the_rate_probe_works_without_a_filter_and_a_run_that_measures_its_own_rate_completes(self):
        """A real run measures its rate with bench() (a dummy target and a planted seed, never the prize) before the
        cost guard decides: for bip39raw every scanned candidate is a survivor the probe derives."""
        import gpu_setperm
        from sp_space import Space
        sp = Space(cfg_from_text(RAW_E2E))
        b = gpu_setperm.bench(sp, log=QUIET)
        self.assertTrue(b["plant_found"] and b["coverage_exact"], b)
        self.assertEqual((b["scheme"], b["tested"], b["valid"], b["prize_loaded"]),
                         ("bip39raw", b["scanned"], b["scanned"], False), b)
        self.assertGreater(b["deriv_per_s"], 0)
        _, r = _run(RAW_E2E, prize=_rand(), probe=None)                       # the real probe, then the run
        self.assertEqual((r.status, r.exit_code), ("NO_MATCH", 0), r.detail)
        self.assertNotEqual(r.est["rate"], gpu_setperm.DEFAULT_ASSUME_RATE)

    def test_k1_reporting_fewer_survivors_than_it_tested_is_void_by_the_exact_check(self):
        import sp_k1_enum
        real = sp_k1_enum.EnumKernel.run

        def lossy(self, start, count):
            s, t, v = real(self, start, count)
            return (s, t, v - 1) if count > 1 and v else (s, t, v)          # the combo self-test's 1-wide windows stay honest
        with mock.patch.object(sp_k1_enum.EnumKernel, "run", lossy):
            _, r = _run(RAW_E2E, prize=_rand())
        self.assertEqual((r.status, r.exit_code, r.blocks_done), ("VOID", 4, 0), r.detail)
        self.assertIn("block 0", r.detail)
        self.assertIn("exact check", r.detail)
