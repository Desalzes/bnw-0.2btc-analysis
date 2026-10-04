import hashlib
import os
import unittest

import numpy as np

import setperm_solve as sps
import sp_ref
from sp_testutil import cfg_from_text, make_text, require_gpu
from template_solve import address_of_hash160, parse_path

PATH_SETS = {
    "electrum": ["m/0/0", "m/0/1", "m/0/2", "m/1/0"],
    "bip44": ["m/44'/0'/0'/0/0", "m/44'/0'/0'/0/1", "m/44'/0'/0'/0/10", "m/0'/0/0", "m/0/0"],
    "nested": ["m/0", "m/0/0", "m/0/0", "m/0/0/5", "m/1'/2", "m"],
}


def _space_for(paths, targets_ok=True):
    from sp_space import Space
    fix = [(s, "abandon") for s in range(1, 12)]
    return Space(cfg_from_text(make_text("bip39", 12, fix, [], ["@WORDLIST"], paths=tuple(paths))))


def _seed_words(seed):
    return np.frombuffer(seed, dtype=">u8").astype(np.uint64)


@require_gpu
class DeriveDump(unittest.TestCase):
    def test_hash160_both_forms_match_cpu_for_every_path_set(self):
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        rng = np.random.default_rng(3)
        seeds = [bytes(rng.integers(0, 256, 64, dtype=np.uint8)) for _ in range(40)]
        for name, paths in PATH_SETS.items():
            sp = _space_for(paths)
            k3 = DeriveKernel(Gpu.get(), sp, [os.urandom(20)])
            got = k3.dump(np.stack([_seed_words(s) for s in seeds]))
            for si, seed in enumerate(seeds):
                for pi, form, h in sp_ref.hash160_forms_seed(seed, sp.paths):
                    self.assertEqual(bytes(got[si, pi, form]), h, (name, si, paths[pi], form))

    def test_known_addresses(self):
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        ab = " ".join(["abandon"] * 11 + ["about"])
        cases = [(sps.seed_of(ab, "", "bip39"), "m/44'/0'/0'/0/0", "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"),
                 (sps.seed_of("cycle rocket west magnet parrot shuffle foot correct salt library feed song", "",
                              "electrum"), "m/0/0", "1NNkttn1YvVGdqBW4PR6zvc3Zx3H5owKRf")]
        for seed, path, addr in cases:
            sp = _space_for([path])
            got = DeriveKernel(Gpu.get(), sp, [os.urandom(20)]).dump(_seed_words(seed)[None, :])
            self.assertEqual(address_of_hash160(bytes(got[0, 0, 0])), addr)      # compressed
            uncompressed = sp_ref.hash160_forms_seed(seed, [parse_path(path)])[1][2]
            self.assertEqual(bytes(got[0, 0, 1]), uncompressed)                   # uncompressed


@require_gpu
class DeriveHits(unittest.TestCase):
    def test_hits_report_index_pass_path_form_and_target(self):
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        gpu = Gpu.get()
        sp = _space_for(PATH_SETS["bip44"])
        rng = np.random.default_rng(5)
        seeds = [bytes(rng.integers(0, 256, 64, dtype=np.uint8)) for _ in range(6)]   # 3 survivors x 2 passes
        surv = np.array([1000, 2000, 3000], dtype=np.uint64)
        forms = {si: sp_ref.hash160_forms_seed(seed, sp.paths) for si, seed in enumerate(seeds)}
        t_comp = forms[3][2][2]        # thread 3 -> survivor 1, pass 1; path 1, compressed
        t_unc = forms[4][7][2]         # thread 4 -> survivor 2, pass 0; path 3, uncompressed
        k3 = DeriveKernel(gpu, sp, [os.urandom(20), t_comp, t_unc])
        sbuf = gpu.ro(np.stack([_seed_words(s) for s in seeds]))
        vbuf = gpu.ro(surv)
        hits = sorted(k3.run(sbuf, vbuf, 0, 3, 2))
        self.assertEqual(hits, sorted([(2000, 1, 1, 0, 1), (3000, 0, 3, 1, 2)]))
        # a slice with an offset: seeds[4:] are local threads 0..1 of survivor slice [2:3] = {3000}
        sbuf2 = gpu.ro(np.stack([_seed_words(s) for s in seeds[4:]]))
        self.assertEqual(k3.run(sbuf2, vbuf, 2, 1, 2), [(3000, 0, 3, 1, 2)])
        # seeds[2:] with survivor slice [1:3]: local threads 0..3 = original threads 2..5
        sbuf3 = gpu.ro(np.stack([_seed_words(s) for s in seeds[2:]]))
        self.assertEqual(sorted(k3.run(sbuf3, vbuf, 1, 2, 2)), sorted([(2000, 1, 1, 0, 1), (3000, 0, 3, 1, 2)]))
        self.assertEqual(k3.run(sbuf, vbuf, 0, 0, 2), [])

    def test_hit_buffer_overflow_is_an_error_not_truncation(self):
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        gpu = Gpu.get()
        sp = _space_for(["m/0/0"])
        seed = bytes(range(64))
        h = sp_ref.hash160_forms_seed(seed, sp.paths)[0][2]
        k3 = DeriveKernel(gpu, sp, [h], hit_cap=4)
        n = 10
        sbuf = gpu.ro(np.stack([_seed_words(seed)] * n))
        vbuf = gpu.ro(np.arange(n, dtype=np.uint64))
        with self.assertRaises(RuntimeError):
            k3.run(sbuf, vbuf, 0, n, 1)


@require_gpu
class DeriveHitEdges(unittest.TestCase):
    """Hole-closing tests found by mutation review: near-miss targets, indices above 2**32,
    duplicate targets, the exact hit_cap boundary."""

    def test_near_miss_targets_never_hit(self):
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        gpu = Gpu.get()
        sp = _space_for(["m/0/0"])
        seed = bytes(range(64))
        sbuf, vbuf = gpu.ro(_seed_words(seed)[None, :]), gpu.ro(np.array([7], dtype=np.uint64))
        for form in (0, 1):
            h = sp_ref.hash160_forms_seed(seed, sp.paths)[form][2]
            self.assertEqual(DeriveKernel(gpu, sp, [h]).run(sbuf, vbuf, 0, 1, 1), [(7, 0, 0, form, 0)])
            for i in range(20):                                   # every single-byte difference is a miss
                bad = bytearray(h)
                bad[i] ^= 1
                self.assertEqual(DeriveKernel(gpu, sp, [bytes(bad)]).run(sbuf, vbuf, 0, 1, 1), [], (form, i))

    def test_survivor_index_above_2_32_and_duplicate_targets(self):
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        gpu = Gpu.get()
        sp = _space_for(["m/0/0", "m/0/1"])
        seed = bytes(range(64))
        t = sp_ref.hash160_forms_seed(seed, sp.paths)[1][2]
        big = (1 << 40) + 12345
        k3 = DeriveKernel(gpu, sp, [t, os.urandom(20), t])
        got = sorted(k3.run(gpu.ro(_seed_words(seed)[None, :]), gpu.ro(np.array([big], dtype=np.uint64)), 0, 1, 1))
        self.assertEqual(got, [(big, 0, 0, 1, 0), (big, 0, 0, 1, 2)])

    def test_hit_cap_boundary(self):
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        gpu = Gpu.get()
        sp = _space_for(["m/0/0"])
        seed = bytes(range(64))
        h = sp_ref.hash160_forms_seed(seed, sp.paths)[0][2]
        k3 = DeriveKernel(gpu, sp, [h], hit_cap=4)

        def go(n):
            return k3.run(gpu.ro(np.stack([_seed_words(seed)] * n)),
                          gpu.ro(np.arange(100, 100 + n, dtype=np.uint64)), 0, n, 1)

        self.assertEqual(sorted(x[0] for x in go(3)), [100, 101, 102])
        self.assertEqual(sorted(x[0] for x in go(4)), [100, 101, 102, 103])     # exactly the cap is not an overflow
        with self.assertRaises(RuntimeError):
            go(5)

    def test_empty_target_list_is_refused(self):
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        with self.assertRaises(ValueError):
            DeriveKernel(Gpu.get(), _space_for(["m/0/0"]), [])

    def test_set_targets_replaces_the_list_and_keeps_the_hit_cap(self):
        """The run sets [prize, this block's plant] before every block: the old list must be gone entirely,
        the new indices must be reported, a refused list must leave the previous one, and hit_cap stays."""
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        gpu = Gpu.get()
        sp = _space_for(["m/0/0", "m/0/1"])
        seed = bytes(range(64))
        forms = {(p, f): h for p, f, h in sp_ref.hash160_forms_seed(seed, sp.paths)}
        sbuf, vbuf = gpu.ro(_seed_words(seed)[None, :]), gpu.ro(np.array([7], dtype=np.uint64))
        k3 = DeriveKernel(gpu, sp, [forms[(0, 0)]], hit_cap=3)
        self.assertEqual(k3.run(sbuf, vbuf, 0, 1, 1), [(7, 0, 0, 0, 0)])
        k3.set_targets([os.urandom(20), forms[(1, 1)]])
        self.assertEqual((k3.ntgt, k3.hit_cap), (2, 3))
        self.assertEqual(k3.run(sbuf, vbuf, 0, 1, 1), [(7, 0, 1, 1, 1)])      # (0,0) is gone; (1,1) is target 1
        for bad in ([], [forms[(0, 0)][:19]], [forms[(0, 0)], b"x" * 21], [forms[(0, 0)]] * 3 + [b""]):
            with self.assertRaises(ValueError):
                k3.set_targets(bad)
            self.assertEqual(k3.ntgt, 2, bad)
            self.assertEqual(k3.run(sbuf, vbuf, 0, 1, 1), [(7, 0, 1, 1, 1)], bad)   # a refused list changed nothing
        k3.set_targets([forms[(0, 1)]] * 3)
        self.assertEqual(sorted(k3.run(sbuf, vbuf, 0, 1, 1)), [(7, 0, 0, 1, t) for t in range(3)])   # exactly the cap
        k3.set_targets([forms[(0, 1)]] * 4)
        with self.assertRaises(RuntimeError):                                  # the cap still holds after a swap
            k3.run(sbuf, vbuf, 0, 1, 1)

    def test_run_records_the_slice_it_was_handed(self):
        """Same contract as K2: scan_block compares the slice the wrapper RECEIVED with the tile it meant."""
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        gpu = Gpu.get()
        sp = _space_for(["m/0/0"])
        seed = bytes(range(64))
        k3 = DeriveKernel(gpu, sp, [os.urandom(20)])
        self.assertIsNone(k3.last_slice)
        sbuf, vbuf = gpu.ro(np.stack([_seed_words(seed)] * 4)), gpu.ro(np.arange(10, 20, dtype=np.uint64))
        k3.run(sbuf, vbuf, 3, 2, 2)
        self.assertEqual(k3.last_slice, (3, 2))
        k3.run(sbuf, vbuf, np.int64(1), np.uint32(1), 1)
        self.assertEqual(k3.last_slice, (1, 1))
        self.assertTrue(all(type(v) is int for v in k3.last_slice))
        self.assertEqual(k3.run(sbuf, vbuf, 5, 0, 2), [])
        self.assertEqual(k3.last_slice, (5, 0))                                  # an empty run is recorded too

    def test_set_targets_reads_the_uploaded_list_back_and_refuses_a_difference(self):
        """Found by review: nothing checked that the device holds the targets that were asked for."""
        from sp_gpu import Gpu
        from sp_k3_derive import DeriveKernel
        gpu = Gpu.get()
        sp = _space_for(["m/0/0", "m/0/1"])
        seed = bytes(range(64))
        forms = {(p, f): h for p, f, h in sp_ref.hash160_forms_seed(seed, sp.paths)}
        sbuf, vbuf = gpu.ro(_seed_words(seed)[None, :]), gpu.ro(np.array([7], dtype=np.uint64))

        class Upload:                       # a device that stores the wrong bytes
            def __init__(self, g, flip):
                self.g, self.flip = g, flip

            def __getattr__(self, name):
                return getattr(self.g, name)

            def ro(self, arr):
                arr = np.array(arr, copy=True)
                if self.flip and arr.dtype == np.uint8 and arr.size % 20 == 0:
                    arr[self.flip[0]] ^= self.flip[1]
                return self.g.ro(arr)

        class Readback:                     # a readback that returns other bytes than were stored
            def __init__(self, g):
                self.g = g

            def __getattr__(self, name):
                return getattr(self.g, name)

            def read(self, buf, dtype, count):
                out = np.array(self.g.read(buf, dtype, count), copy=True)
                if dtype == np.uint8 and count % 20 == 0:
                    out[-1] ^= 0x80
                return out
        for flip in ((0, 1), (19, 0x80), (20, 4), (39, 1)):                      # any byte of either target
            with self.assertRaises(RuntimeError, msg=flip):
                DeriveKernel(Upload(gpu, flip), sp, [forms[(0, 0)], forms[(1, 1)]])
        with self.assertRaises(RuntimeError):
            DeriveKernel(Readback(gpu), sp, [forms[(0, 0)]])
        k3 = DeriveKernel(gpu, sp, [forms[(0, 0)]])                              # the honest device is unaffected
        self.assertEqual(k3.run(sbuf, vbuf, 0, 1, 1), [(7, 0, 0, 0, 0)])
        k3.gpu = Upload(gpu, (3, 1))                                             # a refused swap leaves the old list
        with self.assertRaises(RuntimeError):
            k3.set_targets([os.urandom(20), forms[(1, 1)]])
        k3.gpu = gpu
        self.assertEqual(k3.ntgt, 1)
        self.assertEqual(k3.run(sbuf, vbuf, 0, 1, 1), [(7, 0, 0, 0, 0)])
        k3.set_targets([os.urandom(20), forms[(1, 1)]])
        self.assertEqual(k3.run(sbuf, vbuf, 0, 1, 1), [(7, 0, 1, 1, 1)])


if __name__ == "__main__":
    unittest.main()
