import os
import subprocess
import sys
import unittest
from unittest import mock

import numpy as np

import setperm_solve as sps
import sp_ref
from sp_testutil import cfg_from_text, make_text, require_gpu, small_config_texts
from template_solve import WORDS, address_of_hash160

DUMMY_TARGET = address_of_hash160(bytes(range(20)))          # count_only never compares; still, never the prize


def _k1(text, mode=None, cap=1 << 22):
    from sp_gpu import Gpu
    from sp_k1_enum import EnumKernel
    from sp_space import Space
    cfg = cfg_from_text(text)
    sp = Space(cfg)
    return cfg, sp, EnumKernel(Gpu.get(), sp, cap=cap, mode=mode)


@require_gpu
class EnumParity(unittest.TestCase):
    def test_counts_and_survivor_sets_equal_the_cpu_tool(self):
        for name, text in small_config_texts():
            cfg, sp, k1 = _k1(text)
            tested, valid, surv = sp_ref.ref_survivors(cfg)
            scanned, gtested, gvalid = k1.run(0, sp.total)
            self.assertEqual((scanned, gtested, gvalid), (sp.total, tested, valid), name)
            self.assertEqual(sorted(int(x) for x in k1.survivors(gvalid)), surv, name)
            cpu_tested, cpu_derived = sps.run(cfg, DUMMY_TARGET, 1, quiet=True, count_only=True)
            self.assertEqual((gtested, gvalid * len(sp.passes)), (cpu_tested, cpu_derived), name)

    def test_decode_without_filter_lists_every_distinct_candidate_in_cpu_order(self):
        for name, text in small_config_texts():
            cfg, sp, k1 = _k1(text, mode=0)
            expect = [i for i, s in sp_ref.ref_walk(cfg) if not cfg["distinct"] or len(set(s)) == len(s)]
            scanned, tested, valid = k1.run(0, sp.total)
            self.assertEqual((scanned, tested, valid), (sp.total, len(expect), len(expect)), name)
            self.assertEqual(sorted(int(x) for x in k1.survivors(valid)), expect, name)

    def test_arbitrary_subranges_add_up(self):
        cfg, sp, k1 = _k1(dict(small_config_texts())["D_electrum_12_wordlist"])
        tested, valid, surv = sp_ref.ref_survivors(cfg)
        cuts = [0, 1, 255, 256, 257, 10007, sp.total // 2, sp.total - 3, sp.total]
        tot = [0, 0, 0]
        for a, b in zip(cuts, cuts[1:]):
            s, t, v = k1.run(a, b - a)
            self.assertEqual(s, b - a)
            got = sorted(int(x) for x in k1.survivors(v))
            self.assertEqual(got, [i for i in surv if a <= i < b])
            tot = [tot[0] + s, tot[1] + t, tot[2] + v]
        self.assertEqual(tot, [sp.total, tested, valid])

    def test_tiny_and_unaligned_counts(self):
        cfg, sp, k1 = _k1(dict(small_config_texts())["G_bip39_12_k0"])     # total is a few dozen
        for count in (1, 2, 3, 7):
            self.assertEqual(k1.run(0, count)[0], count)

    def test_survivor_overflow_reports_the_true_count(self):
        cfg, sp, k1 = _k1(dict(small_config_texts())["C_bip39_15_wordlist"], cap=8)
        tested, valid, surv = sp_ref.ref_survivors(cfg)
        self.assertGreater(valid, 8)
        scanned, gtested, gvalid = k1.run(0, sp.total)
        self.assertEqual((gtested, gvalid), (tested, valid))                   # exact even though only 8 were stored
        stored = [int(x) for x in k1.survivors(8)]
        self.assertTrue(set(stored) <= set(surv))

    def test_nothing_is_written_beyond_cap(self):
        # give the kernel a survivor buffer much larger than `cap`, pre-filled with a sentinel:
        # after an overflowing run only the first `cap` slots may have been touched
        import pyopencl as cl
        cfg, sp, k1 = _k1(dict(small_config_texts())["C_bip39_15_wordlist"], cap=8)
        tested, valid, surv = sp_ref.ref_survivors(cfg)
        self.assertGreater(valid, 64)
        gpu = k1.gpu
        sentinel = np.full(64, 0xFFFFFFFFFFFFFFFF, np.uint64)
        k1.surv = cl.Buffer(gpu.ctx, cl.mem_flags.READ_WRITE | cl.mem_flags.COPY_HOST_PTR, hostbuf=sentinel)
        scanned, gtested, gvalid = k1.run(0, sp.total)
        self.assertEqual((gtested, gvalid), (tested, valid))
        words = gpu.read(k1.surv, np.uint64, 64)
        self.assertTrue(np.all(words[8:] == 0xFFFFFFFFFFFFFFFF))              # nothing past cap
        stored = [int(x) for x in words[:8]]
        self.assertEqual(len(set(stored)), 8)                                  # 8 distinct real survivors
        self.assertTrue(set(stored) <= set(surv))

    def test_zero_survivors_and_empty_count(self):
        cfg, sp, k1 = _k1(dict(small_config_texts())["G_bip39_12_k0"])
        self.assertEqual(k1.run(0, 0), (0, 0, 0))
        self.assertEqual(len(k1.survivors(0)), 0)

    def test_exact_2048_128_count(self):
        # setperm's own selftest case: 11 x 'abandon' + the last slot over the wordlist -> exactly 128 valid
        text = make_text("bip39", 12, [(i, "abandon") for i in range(1, 12)], [], ["@WORDLIST"])
        cfg, sp, k1 = _k1(text)
        self.assertEqual(k1.run(0, sp.total), (2048, 2048, 128))

    def test_full_permutation_space_decodes_on_gpu(self):
        text = make_text("bip39", 12, [], WORDS[100:112], [])
        cfg, sp, k1 = _k1(text, mode=0)
        for start in (0, 479001600 - 500, 123456789):
            scanned, tested, valid = k1.run(start, 400)
            self.assertEqual((scanned, tested, valid), (400, 400, 400))
            got = sorted(int(x) for x in k1.survivors(400))
            self.assertEqual(got, list(range(start, start + 400)))


@require_gpu
class EnumHoleClosers(unittest.TestCase):
    """Found by mutation review: bip39 checksum width for 18/21/24 words, Electrum mnemonics that
    reach the two-block SHA-512 path (>= 112 bytes), survivors() past the cap, range validation."""

    def test_bip39_18_21_24_words(self):
        import random
        rng = random.Random(7)
        for n, free in ((18, {2, 9, 18}), (21, {1, 10, 21}), (24, {5, 12, 24})):
            fix = [(s, rng.choice(WORDS)) for s in range(1, n + 1) if s not in free]
            rng.shuffle(fix)
            text = make_text("bip39", n, fix, rng.sample(WORDS, 2), ["@WORDLIST"])
            cfg, sp, k1 = _k1(text)
            tested, valid, surv = sp_ref.ref_survivors(cfg)
            self.assertGreater(valid, 0, n)
            self.assertEqual(k1.run(0, sp.total), (sp.total, tested, valid), n)
            self.assertEqual(sorted(int(x) for x in k1.survivors(valid)), surv, n)

    def test_electrum_messages_across_the_one_and_two_block_boundary(self):
        import random
        rng = random.Random(11)
        for target_len in (100, 109, 110, 111, 112, 113, 126, 127, 128, 129, 150, 200, 222, 238, 239):
            n, lp = 17, 2                                  # 17 words, 2 unknown slots holding 2-byte tokens
            fixed_total = target_len - (n - 1) - 2 * lp
            lens = [1] * 15
            rem = fixed_total - 15
            i = 0
            while rem > 0:
                add = min(15, rem)
                lens[i % 15] += add
                rem -= add
                i += 1
            slots = [s for s in range(1, n + 1) if s not in (4, 13)]
            fix = [(s, "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(L)))
                   for s, L in zip(slots, lens)]
            pool = [f"{i:02d}" for i in range(64)]
            text = make_text("electrum", n, fix, [], [" ".join(pool)], freewords=True)
            cfg, sp, k1 = _k1(text)
            self.assertLessEqual(sp.max_mnemonic, 239)
            tested, valid, surv = sp_ref.ref_survivors(cfg)
            self.assertGreater(valid, 0, target_len)
            self.assertEqual(k1.run(0, sp.total), (sp.total, tested, valid), target_len)
            self.assertEqual(sorted(int(x) for x in k1.survivors(valid)), surv, target_len)

    def test_survivors_never_returns_more_than_cap(self):
        cfg, sp, k1 = _k1(dict(small_config_texts())["C_bip39_15_wordlist"], cap=8)
        k1.run(0, sp.total)
        self.assertEqual(len(k1.survivors(10 ** 9)), 8)

    def test_out_of_range_launches_are_refused(self):
        cfg, sp, k1 = _k1(dict(small_config_texts())["G_bip39_12_k0"])
        self.assertEqual(k1.run(0, sp.total)[0], sp.total)
        for start, count in ((0, sp.total + 1), (sp.total - 1, 2), (-1, 3), (0, -1), (sp.total + 5, 1)):
            with self.assertRaises(ValueError):
                k1.run(start, count)


class IndexChecksumNoGpu(unittest.TestCase):
    """K1's index accumulator is the exact uint64 sum of the indices its threads scanned (a uint32 sum was blind to
    a shift k with count*k = 0 mod 2**32 and to a start truncated to 32 bits); the host compares it with this."""

    def test_expected_idx_sum_is_the_uint64_sum_of_exactly_that_range(self):
        from sp_k1_enum import expected_idx_sum
        for start, count in ((0, 0), (0, 1), (5, 7), (0, 256), (1, 257), (2 ** 32 - 3, 10), (2 ** 32, 1000),
                             (2 ** 33 + 17, 4097), (2 ** 62 + 5, 300), (123456789, 65536), (2 ** 32 - 1, 2),
                             (2 ** 63 - 5, 2 ** 31), (2 ** 40, (1 << 32) - 1)):
            brute = sum(range(start, start + count)) if count <= 1 << 20 else (2 * start + count - 1) * count // 2
            self.assertEqual(expected_idx_sum(start, count), brute % 2 ** 64, (start, count))
        self.assertGreater(expected_idx_sum(2 ** 40, 1000), 2 ** 32)          # really wider than 32 bits
        for start, count in ((0, 1000), (2 ** 33, 257), (7, 1 << 25)):     # a range shifted by one is always different
            self.assertNotEqual(expected_idx_sum(start, count), expected_idx_sum(start + 1, count))

    def test_shifts_a_uint32_sum_cannot_see_are_different_here(self):
        """count*k = 0 mod 2**32: a shift of exactly `count` with count a power of two >= 2**16, a shift of 2**32,
        a start truncated to 32 bits. The uint32 sum of (uint)idx is identical for each; the uint64 sum is not."""
        from sp_k1_enum import expected_idx_sum
        for start in (0, 12345, 2 ** 33 + 9):
            for count in (1 << 16, 1 << 20, 1 << 31):
                shifted = expected_idx_sum(start + count, count)
                self.assertEqual(shifted % 2 ** 32, expected_idx_sum(start, count) % 2 ** 32, (start, count))  # blind
                self.assertNotEqual(shifted, expected_idx_sum(start, count), (start, count))
            for k in (1 << 32, 3 << 32):
                self.assertNotEqual(expected_idx_sum(start + k, 1000), expected_idx_sum(start, 1000))
                self.assertEqual(expected_idx_sum(start + k, 1000) % 2 ** 32, expected_idx_sum(start, 1000) % 2 ** 32)
        for start in (2 ** 32, 2 ** 33 + 5, 2 ** 40 + 1):
            self.assertNotEqual(expected_idx_sum(start & 0xFFFFFFFF, 1000), expected_idx_sum(start, 1000))

    def test_sp_k1_enum_imports_without_opencl(self):
        """The checksum formula is needed by tests that must pass with pyopencl absent."""
        here = os.path.dirname(os.path.abspath(__file__))
        code = ("import sys; sys.modules['pyopencl'] = None; from sp_k1_enum import expected_idx_sum; "
                "assert expected_idx_sum(2**32, 3) == 3 * 2**32 + 3")
        p = subprocess.run([sys.executable, "-c", code], cwd=here, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr[-400:])


@require_gpu
class EnumIndexChecksum(unittest.TestCase):
    """Found by review: 'scanned == count' only counts threads, so a kernel scanning [start+1, start+count+1)
    passed every coverage check. K1 now also sums (uint)idx and EnumKernel.run refuses a wrong sum."""

    BIG = make_text("bip39", 15, [], WORDS[300:315], [])                  # 15! ~ 1.3e12 candidates, no filter in mode 0

    def test_starts_above_2_32_and_2_33_with_counts_off_the_work_group_size(self):
        from sp_k1_enum import NCTR, expected_idx_sum
        cfg, sp, k1 = _k1(self.BIG, mode=0)
        self.assertGreater(sp.total, 2 ** 34)
        for start in (2 ** 32 - 100, 2 ** 32, 2 ** 32 + 7, 2 ** 33 - 1, 2 ** 33 + 12345, sp.total - 1000):
            for count in (1, 255, 257, 1000):
                self.assertEqual(k1.run(start, count), (count, count, count), (start, count))
                self.assertEqual(sorted(int(x) for x in k1.survivors(count)), list(range(start, start + count)))
                c = k1.gpu.read(k1.ctr, np.uint32, NCTR)
                self.assertEqual(int(k1.gpu.read(k1.isum, np.uint64, 1)[0]), expected_idx_sum(start, count),
                                 (start, count))                                  # the device's own exact uint64 sum
                self.assertEqual(k1.last_idx_sum, expected_idx_sum(start, count), (start, count))   # ... and exposed
                self.assertGreaterEqual(k1.last_idx_sum, start)
                self.assertEqual(int(c[3]), count)                              # the slot counter agrees with valid

    def _mutant_kernel(self, k1, needle, replacement):
        """A scratch build of sp_enum.cl with one line changed (the real kernels/ file is untouched)."""
        import pyopencl as cl
        import sp_gpu
        import sp_space
        with open(os.path.join(sp_gpu.KDIR, "sp_enum.cl"), encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(src.count(needle), 1)
        parts = []
        for name in ("common", "sha2", "sp_sha512", "sp_decode"):
            with open(os.path.join(sp_gpu.KDIR, name + ".cl"), encoding="utf-8") as f:
                parts.append(f.read())
        full = sp_space.cl_defines() + "\n" + "\n".join(parts) + "\n" + src.replace(needle, replacement)
        prog = cl.Program(k1.gpu.ctx, full).build(options="-cl-std=CL1.2")
        return cl.Kernel(prog, "sp_enum")

    def test_a_kernel_that_scans_a_shifted_range_is_refused(self):
        cfg, sp, k1 = _k1(dict(small_config_texts())["D_electrum_12_wordlist"])
        cases = ((1000, 500), (0, 257), (3 * 2048, 2048), (40000, 1))
        for start, count in cases:
            k1.run(start, count)                                               # the real kernel passes
        # threads scan [start+1, start+count+1): the same thread count
        k1.knl = self._mutant_kernel(k1, "ulong idx = start + gid;", "ulong idx = start + gid + 1;")
        for start, count in cases:
            with self.assertRaises(RuntimeError, msg=(start, count)):
                k1.run(start, count)
            c = k1.gpu.read(k1.ctr, np.uint32, 4)
            self.assertEqual(int(c[0]), count)                                 # scanned alone cannot see the shift

    def test_a_survivor_cursor_that_disagrees_with_valid_is_refused(self):
        cfg, sp, k1 = _k1(dict(small_config_texts())["D_electrum_12_wordlist"])
        tested, valid, _ = sp_ref.ref_survivors(cfg)
        self.assertGreater(valid, 0)
        k1.knl = self._mutant_kernel(k1, "uint slot = atomic_inc(&ctr[3]);",
                                     "uint slot = atomic_inc(&ctr[3]); if (slot == 0) atomic_inc(&ctr[3]);")
        with self.assertRaises(RuntimeError):
            k1.run(0, sp.total)
        self.assertEqual(k1.run(0, 1), (1, 1, 0))                             # a range with no survivor still passes

    def test_a_wrong_expected_sum_is_refused(self):
        import sp_k1_enum
        cfg, sp, k1 = _k1(dict(small_config_texts())["D_electrum_12_wordlist"])
        real = sp_k1_enum.expected_idx_sum
        with mock.patch.object(sp_k1_enum, "expected_idx_sum", lambda s, c: real(s + 1, c)):
            with self.assertRaises(RuntimeError):
                k1.run(1000, 500)
        self.assertEqual(k1.run(1000, 500)[0], 500)

    def test_the_sum_is_exposed_after_every_launch_and_is_not_stale_after_a_refusal(self):
        from sp_k1_enum import expected_idx_sum
        cfg, sp, k1 = _k1(dict(small_config_texts())["D_electrum_12_wordlist"])
        self.assertIsNone(k1.last_idx_sum)
        for start, count in ((0, 1000), (1000, 1), (5000, 4096), (0, 0)):
            k1.run(start, count)
            self.assertIs(type(k1.last_idx_sum), int)
            self.assertEqual(k1.last_idx_sum, expected_idx_sum(start, count), (start, count))
        with self.assertRaises(ValueError):
            k1.run(sp.total, 1)
        self.assertIsNone(k1.last_idx_sum)                                       # a refused launch leaves no stale sum

    def test_shifts_and_truncations_a_uint32_sum_could_not_see_are_refused(self):
        """Mutants of the kernel's index: a shift of exactly `count` (count = 2**16, a power of two, so count*count is
        0 mod 2**32), a shift of 2**32, a start cut to 32 bits, and a 32-bit-only accumulator. The old uint32 sum
        was blind to the first three; each is refused now (and is shown to be invisible mod 2**32 here)."""
        from sp_k1_enum import expected_idx_sum
        cfg, sp, k1 = _k1(self.BIG, mode=0)
        count = 1 << 16
        starts = (2 ** 33 + 12345, 2 ** 34 + 99, 2 ** 36 + 7)
        for start in starts:
            k1.run(start, count)                                                   # the real kernel passes
        real = k1.knl
        cases = (("ulong idx = start + gid;", "ulong idx = start + gid + count;", True),
                 ("ulong idx = start + gid;", "ulong idx = start + gid + 4294967296UL;", True),
                 ("ulong idx = start + gid;", "ulong idx = (start & 0xFFFFFFFFUL) + gid;", True),
                 ("atom_add(&lsum, idx);", "atom_add(&lsum, (ulong)(uint)idx);", False),
                 ("atom_add(isum, lsum);", "atom_add(isum, lsum & 0xFFFFFFFFUL);", False))
        for needle, repl, blind32 in cases:
            k1.knl = self._mutant_kernel(k1, needle, repl)
            for start in starts:
                with self.assertRaises(RuntimeError, msg=(repl, start)):
                    k1.run(start, count)
                if blind32:
                    self.assertEqual(k1.last_idx_sum % 2 ** 32, expected_idx_sum(start, count) % 2 ** 32, repl)
                self.assertNotEqual(k1.last_idx_sum, expected_idx_sum(start, count), repl)
        k1.knl = real
        self.assertEqual(k1.run(starts[0], count), (count, count, count))

    def test_an_exact_sum_survives_a_wrap_of_the_64_bit_accumulator(self):
        """count*start above 2**64: the kernel's ulong sum and the host's `% 2**64` wrap the same way."""
        from sp_k1_enum import expected_idx_sum
        cfg, sp, k1 = _k1(make_text("electrum", 20, [], WORDS[100:120], []), mode=0)   # 20! ~ 2.4e18 candidates
        start, count = sp.total - (1 << 20), 1 << 20
        self.assertGreater(count * start, 2 ** 64)
        k1.run(start, count)
        self.assertEqual(k1.last_idx_sum, sum(range(start, start + count)) % 2 ** 64)
        self.assertLess(k1.last_idx_sum, count * start)                             # it really wrapped


if __name__ == "__main__":
    unittest.main()
