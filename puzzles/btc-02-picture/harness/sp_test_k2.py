import hashlib
import unittest

import numpy as np

import setperm_solve as sps
from sp_testutil import cfg_from_text, make_text, require_gpu
from template_solve import WORDS


def _expect(sp, idx, pp):
    m = " ".join(sp.decode(idx))
    salt = ("electrum" if sp.scheme == "electrum" else "mnemonic") + pp
    return hashlib.pbkdf2_hmac("sha512", m.encode(), salt.encode(), 2048)


def _pbkdf2_case(test, text, indices, expect_passes=None):
    from sp_gpu import Gpu
    from sp_k2_pbkdf2 import Pbkdf2Kernel
    from sp_space import Space
    sp = Space(cfg_from_text(text))
    got = Pbkdf2Kernel(Gpu.get(), sp).run_np(np.array(indices, dtype=np.uint64))
    npass = len(sp.passes)
    test.assertEqual(got.shape, (len(indices) * npass, 64))
    for si, idx in enumerate(indices):
        for pi, pp in enumerate(sp.passes):
            test.assertEqual(bytes(got[si * npass + pi]), _expect(sp, idx, pp), (si, pi, sp.decode(idx)))
    return sp


def _text_of_length(total_len, n=15, passes=("a",)):
    """An electrum FREEWORDS config whose joined mnemonic is exactly `total_len` bytes for every
    index: n-1 fixed tokens (1..16 bytes each) plus one free slot with two 1-byte pool tokens."""
    body = total_len - (n - 1) - 1                     # token bytes left for the n-1 fixed tokens
    assert n - 1 <= body <= 16 * (n - 1), (total_len, n)
    base, extra = divmod(body, n - 1)
    lens = [base + (1 if i < extra else 0) for i in range(n - 1)]
    fixed = [(i + 1, chr(97 + i) * l) for i, l in enumerate(lens)]
    return make_text("electrum", n, fixed, [], ["y", "z"], passes=passes, freewords=True)


@require_gpu
class Pbkdf2(unittest.TestCase):
    def test_bip39_trezor_vector(self):
        ab = ["abandon"] * 11 + ["about"]
        text = make_text("bip39", 12, [(i + 1, w) for i, w in enumerate(ab[:11])], [], [ab[11]],
                         passes=("TREZOR",))
        from sp_gpu import Gpu
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        from sp_space import Space
        sp = Space(cfg_from_text(text))
        got = Pbkdf2Kernel(Gpu.get(), sp).run_np(np.array([0], dtype=np.uint64))
        self.assertEqual(sp.passes, ["", "TREZOR"])
        self.assertEqual(bytes(got[1]).hex(),
                         "c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e53495531f09a6987599d18264c1e1c92f2cf141630c7a3c4ab7c81b2f001698e7463b04")

    def test_many_survivors_times_many_passphrases(self):
        text = make_text("bip39", 12, [(i, WORDS[100 + i]) for i in range(1, 11)], [], ["@WORDLIST"],
                         passes=("moon", "MOON", "café ёж", "p" * 99))
        _pbkdf2_case(self, text, [0, 1, 2, 77, 2046])

    def test_electrum_13_and_12(self):
        for n in (12, 13):
            text = make_text("electrum", n, [(i, WORDS[300 + i]) for i in range(1, n - 1)], [], ["@WORDLIST"],
                             passes=("breathe",))
            _pbkdf2_case(self, text, [0, 5, 2047])

    def test_long_mnemonics_hit_every_key_length_regime(self):
        # FREEWORDS electrum: joined mnemonic lengths straddling 111/112 (message), 128/129 (HMAC key) and 2 blocks
        long_tokens = ["abcdefghijklmnop", "qrstuvwxyzabcdef"]      # 16 bytes each
        for n_fix_long in range(0, 11):
            fixed = [(i + 1, long_tokens[i % 2] if i < n_fix_long else "cat") for i in range(11)]
            text = make_text("electrum", 12, fixed, [], ["dog", "horse"], freewords=True)
            sp = _pbkdf2_case(self, text, [0, 1])
            self.assertLessEqual(sp.max_mnemonic, 239)
        m = " ".join(sp.decode(0))
        self.assertGreater(len(m.encode()), 128)                    # the last case exercised the pre-hash path

    def test_slice_offset_and_thread_mapping(self):
        from sp_gpu import Gpu
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        from sp_space import Space
        text = make_text("bip39", 12, [(i, WORDS[500 + i]) for i in range(1, 11)], [], ["@WORDLIST"],
                         passes=("a", "b"))
        sp = Space(cfg_from_text(text))
        gpu = Gpu.get()
        k2 = Pbkdf2Kernel(gpu, sp)
        surv = np.array([10, 20, 30, 40], dtype=np.uint64)
        sbuf = gpu.ro(surv)
        k2.launch(sbuf, 2, 2)                                          # survivors 30, 40 x passes ["", "a", "b"]
        # seeds[] holds 8 ulong WORD VALUES per thread (big-endian interpretation of the 64 seed bytes);
        # read them as uint64 and serialise big-endian, exactly as run_np does (a raw uint8 read would see
        # the host's little-endian memory order)
        words = gpu.read(k2.seeds, np.uint64, 2 * 3 * 8).reshape(6, 8)
        got = np.frombuffer(words.astype(">u8").tobytes(), dtype=np.uint8).reshape(6, 64)
        for si, idx in enumerate((30, 40)):
            for pi, pp in enumerate(sp.passes):
                self.assertEqual(bytes(got[si * 3 + pi]), _expect(sp, idx, pp))

    # ---- additions beyond the plan's five tests (none of the plan's assertions is changed) ----

    def test_hmac_key_length_boundaries_exact(self):
        # The plan's lengths (49, 62, ..., 127, 138/140, ...) never land ON 128/129 or the 239 maximum.
        # Sweep the exact bytes around the RFC 2104 key-length switch, the 111/112 padding switch and the cap.
        for total_len in (110, 111, 112, 113, 127, 128, 129, 130, 200, 238, 239):
            text = _text_of_length(total_len)
            sp = _pbkdf2_case(self, text, [0, 1])
            for idx in (0, 1):
                self.assertEqual(len(" ".join(sp.decode(idx)).encode()), total_len, (total_len, idx))

    def test_passphrase_limit_99_bytes_multibyte_and_electrum(self):
        pp99 = "é" * 33                                               # NFKD: e + U+0301 = 3 bytes each -> 99
        self.assertEqual(len(sps.normalize_pass(pp99, "bip39").encode("utf-8")), 99)
        text = make_text("bip39", 12, [(i, WORDS[700 + i]) for i in range(1, 11)], [], ["@WORDLIST"],
                         passes=(pp99, "ёж" * 16 + "x" * 3))         # NFKD: ёж = 6 bytes; 16*6 + 3 = 99
        sp = _pbkdf2_case(self, text, [3, 2000])
        self.assertEqual(sorted(len(p.encode("utf-8")) for p in sp.passes), [0, 99, 99])
        self.assertEqual(max(len(p.encode("utf-8")) for p in sp.passes), 99)
        text = make_text("electrum", 12, [(i, WORDS[800 + i]) for i in range(1, 11)], [], ["@WORDLIST"],
                         passes=("p" * 99, "Café  Ёж"))                # electrum: lower-cased, accents dropped
        _pbkdf2_case(self, text, [1, 2047])

    def test_thread_count_not_a_multiple_of_the_work_group(self):
        # 7 survivors x 3 passes = 21 threads: padded launch, tail threads must not write past nthreads
        from sp_gpu import Gpu
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        from sp_space import Space
        text = make_text("bip39", 12, [(i, WORDS[900 + i]) for i in range(1, 11)], [], ["@WORDLIST"],
                         passes=("u", "v"))
        sp = Space(cfg_from_text(text))
        gpu = Gpu.get()
        k2 = Pbkdf2Kernel(gpu, sp, max_threads=64)
        surv = np.array([5, 6, 7, 8, 9, 10, 11], dtype=np.uint64)
        sentinel = np.full(64 * 8, 0xDEADBEEFDEADBEEF, dtype=np.uint64)
        import pyopencl as cl
        cl.enqueue_copy(gpu.q, k2.seeds, sentinel)
        k2.launch(gpu.ro(surv), 0, 7)
        words = gpu.read(k2.seeds, np.uint64, 64 * 8).reshape(64, 8)
        self.assertTrue((words[21:] == 0xDEADBEEFDEADBEEF).all())       # nothing written beyond thread 20
        got = np.frombuffer(words[:21].astype(">u8").tobytes(), dtype=np.uint8).reshape(21, 64)
        for si, idx in enumerate(surv):
            for pi, pp in enumerate(sp.passes):
                self.assertEqual(bytes(got[si * 3 + pi]), _expect(sp, int(idx), pp))

    def test_launch_refuses_more_threads_than_the_buffer_holds(self):
        from sp_gpu import Gpu
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        from sp_space import Space
        text = make_text("bip39", 12, [(i, WORDS[950 + i]) for i in range(1, 11)], [], ["@WORDLIST"],
                         passes=("u",))
        sp = Space(cfg_from_text(text))
        gpu = Gpu.get()
        k2 = Pbkdf2Kernel(gpu, sp, max_threads=3)                       # 2 passes -> 1 survivor = 2 threads
        sbuf = gpu.ro(np.array([1, 2], dtype=np.uint64))
        with self.assertRaises(ValueError):
            k2.launch(sbuf, 0, 2)                                       # 4 threads > 3
        with self.assertRaises(ValueError):
            k2.launch(sbuf, 1, 2)                                       # slice runs off the survivor buffer
        self.assertEqual(k2.launch(sbuf, 0, 0), 0.0)                    # nothing to do
        k2.launch(sbuf, 0, 1)                                           # 2 threads fit

    def test_launch_records_the_slice_it_was_handed(self):
        """scan_block checks the slice K2 RECEIVED against the tile it meant to hand over: a consumer-side fault
        (off+1, nsurv-1) must be visible in the wrapper, not only in the caller's own loop variables."""
        from sp_gpu import Gpu
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        from sp_space import Space
        text = make_text("bip39", 12, [(i, WORDS[500 + i]) for i in range(1, 11)], [], ["@WORDLIST"],
                         passes=("a", "b"))
        sp = Space(cfg_from_text(text))
        gpu = Gpu.get()
        k2 = Pbkdf2Kernel(gpu, sp)
        self.assertIsNone(k2.last_slice)
        sbuf = gpu.ro(np.array([10, 20, 30, 40], dtype=np.uint64))
        k2.launch(sbuf, 2, 2)
        self.assertEqual(k2.last_slice, (2, 2))
        k2.launch(sbuf, np.int64(1), np.uint32(3))
        self.assertEqual(k2.last_slice, (1, 3))
        self.assertTrue(all(type(v) is int for v in k2.last_slice))              # plain ints, whatever came in
        k2.launch(sbuf, 0, 0)
        self.assertEqual(k2.last_slice, (0, 0))                                  # an empty launch is recorded too
        with self.assertRaises(ValueError):
            k2.launch(sbuf, 3, 2)
        self.assertEqual(k2.last_slice, (3, 2))                                  # what it was handed, even if refused
        k2.launch(sbuf, 1, 1)
        got = _words_to_bytes(gpu.read(k2.seeds, np.uint64, 3 * 8).reshape(3, 8))
        for pi, pp in enumerate(sp.passes):                                      # ... and it is what the kernel ran
            self.assertEqual(bytes(got[pi]), _expect(sp, 20, pp))


def _words_to_bytes(words):
    return np.frombuffer(words.astype(">u8").tobytes(), dtype=np.uint8).reshape(len(words), 64)


@require_gpu
class Pbkdf2HoleClosers(unittest.TestCase):
    """Found by mutation review: global-vs-local ids, the exact max_threads bound, 16-bit truncation."""

    def test_more_than_one_work_group(self):
        from sp_gpu import Gpu
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        from sp_space import Space
        text = make_text("bip39", 12, [(i, WORDS[100 + i]) for i in range(1, 11)], [], ["@WORDLIST"],
                         passes=("moon", "MOON"))
        sp = Space(cfg_from_text(text))
        surv = np.array([(i * 20) % 2048 for i in range(100)], dtype=np.uint64)     # 100 x 3 passes = 300 threads
        k2 = Pbkdf2Kernel(Gpu.get(), sp)
        self.assertGreater(len(surv) * k2.npass, Gpu.get().local_size(k2.knl))
        got = k2.run_np(surv)
        for si, idx in enumerate(surv):
            for pi, pp in enumerate(sp.passes):
                self.assertEqual(bytes(got[si * 3 + pi]), _expect(sp, int(idx), pp), (si, pi))

    def test_launch_accepts_exactly_max_threads(self):
        from sp_gpu import Gpu
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        from sp_space import Space
        text = make_text("bip39", 12, [(i, WORDS[950 + i]) for i in range(1, 11)], [], ["@WORDLIST"], passes=("u",))
        sp = Space(cfg_from_text(text))
        gpu = Gpu.get()
        k2 = Pbkdf2Kernel(gpu, sp, max_threads=4)                       # 2 passes x 2 survivors == 4 == max
        k2.launch(gpu.ro(np.array([1, 2], dtype=np.uint64)), 0, 2)
        got = _words_to_bytes(gpu.read(k2.seeds, np.uint64, 4 * 8).reshape(4, 8))
        for si, idx in enumerate((1, 2)):
            for pi, pp in enumerate(sp.passes):
                self.assertEqual(bytes(got[si * 2 + pi]), _expect(sp, idx, pp))

    def test_survivor_offset_beyond_16_bits(self):
        from sp_gpu import Gpu
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        from sp_space import Space
        text = make_text("bip39", 12, [(i, WORDS[500 + i]) for i in range(1, 11)], [], ["@WORDLIST"], passes=("a",))
        sp = Space(cfg_from_text(text))
        gpu = Gpu.get()
        k2 = Pbkdf2Kernel(gpu, sp, max_threads=16)
        surv = np.random.default_rng(7).integers(0, 2048, 70000).astype(np.uint64)   # not periodic mod 2**16
        self.assertNotEqual(list(surv[66000:66003]), list(surv[66000 - 65536:66003 - 65536]))
        k2.launch(gpu.ro(surv), 66000, 3)
        got = _words_to_bytes(gpu.read(k2.seeds, np.uint64, 6 * 8).reshape(6, 8))
        for si in range(3):
            for pi, pp in enumerate(sp.passes):
                self.assertEqual(bytes(got[si * 2 + pi]), _expect(sp, int(surv[66000 + si]), pp))


if __name__ == "__main__":
    unittest.main()
