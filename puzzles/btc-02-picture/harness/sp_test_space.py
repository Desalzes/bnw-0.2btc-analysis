import hashlib
import hmac
import itertools
import math
import struct
import unittest

import numpy as np

import setperm_solve as sps
import sp_ref
import sp_space
from sp_space import Space, SpaceError
from sp_testutil import cfg_from_text, make_text, small_config_texts
from template_solve import WORDS, address_of_hash160, parse_path

# Self-generated stand-in target (never the prize address); count_only runs never compare against it.
DUMMY_TARGET = address_of_hash160(bytes(range(20)))


class Sha512Ref(unittest.TestCase):
    def test_constants_and_compress_match_hashlib(self):
        self.assertEqual(sp_ref.SHA512_IV[0], 0x6A09E667F3BCC908)
        self.assertEqual(sp_ref.SHA512_K[0], 0x428A2F98D728AE22)
        self.assertEqual(sp_ref.SHA512_K[79], 0x6C44198C4A475817)
        for msg in (b"", b"abc", b"x" * 111, b"y" * 112, b"z" * 239):
            got = sp_ref.sha512_finish_py(sp_ref.SHA512_IV, msg, 0)
            self.assertEqual(got, hashlib.sha512(msg).digest(), len(msg))

    def test_midstates_reproduce_hmac(self):
        for key in (b"Seed version", b"k", b"K" * 128, b"L" * 200):
            mids = sp_ref.hmac_midstates(key)
            for msg in (b"", b"cycle rocket west magnet", b"m" * 150):
                self.assertEqual(sp_ref.hmac_via_midstates(mids, msg),
                                 hmac.new(key, msg, hashlib.sha512).digest())

    def test_salt_block_layout(self):
        key = b"cycle rocket west magnet parrot shuffle foot correct salt library feed song"
        for scheme, pp in (("bip39", b""), ("bip39", b"TREZOR"), ("electrum", b"x" * 99),
                           ("bip39", "café ёж".encode())):
            words = sp_ref.salt_block_words(scheme, pp)
            self.assertEqual(len(words), 16)
            ist, ost = sp_ref.hmac_midstates(key)
            inner = b"".join(x.to_bytes(8, "big") for x in sp_ref.sha512_compress(ist, struct.pack(">16Q", *words)))
            outer = sp_ref.sha512_finish_py(ost, inner, 128)
            salt = (b"electrum" if scheme == "electrum" else b"mnemonic") + pp + b"\x00\x00\x00\x01"
            self.assertEqual(outer, hmac.new(key, salt, hashlib.sha512).digest())

    def test_salt_block_refuses_100_byte_passphrase(self):
        with self.assertRaises(ValueError):
            sp_ref.salt_block_words("bip39", b"x" * 100)


class Hash160Forms(unittest.TestCase):
    def test_known_vectors_both_forms_and_confirm(self):
        ab = " ".join(["abandon"] * 11 + ["about"])
        (p0, f0, h0), (p1, f1, h1) = sp_ref.hash160_forms(ab, "", [parse_path("m/44'/0'/0'/0/0")], "bip39")
        self.assertEqual((p0, f0, p1, f1), (0, 0, 0, 1))
        self.assertEqual(address_of_hash160(h0), "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA")
        self.assertNotEqual(h0, h1)
        self.assertTrue(sp_ref.confirm(ab, "", "m/44'/0'/0'/0/0", 0, "bip39", h0))
        self.assertTrue(sp_ref.confirm(ab, "", "m/44'/0'/0'/0/0", 1, "bip39", h1))
        self.assertFalse(sp_ref.confirm(ab, "", "m/44'/0'/0'/0/0", 1, "bip39", h0))
        ev = "cycle rocket west magnet parrot shuffle foot correct salt library feed song"
        h = sp_ref.hash160_forms(ev, "", [parse_path("m/0/0")], "electrum")[0][2]
        self.assertEqual(address_of_hash160(h), "1NNkttn1YvVGdqBW4PR6zvc3Zx3H5owKRf")


class SpaceCore(unittest.TestCase):
    def test_sizes_and_decode_match_cpu_enumeration_order(self):
        for name, text in small_config_texts():
            cfg = cfg_from_text(text)
            sp = Space(cfg)
            walk = [s for _, s in sp_ref.ref_walk(cfg)]
            self.assertEqual(sp.total, len(walk), name)
            step = max(1, len(walk) // 400)
            for i in list(range(0, len(walk), step)) + [len(walk) - 1]:
                self.assertEqual(sp.decode(i), walk[i], (name, i))

    def test_reference_counts_equal_unmodified_setperm_count_only(self):
        for name, text in small_config_texts():
            cfg = cfg_from_text(text)
            sp = Space(cfg)
            tested, valid, surv = sp_ref.ref_survivors(cfg)
            self.assertEqual(len(surv), valid)
            cpu_tested, cpu_derived = sps.run(cfg, DUMMY_TARGET, 1, quiet=True, count_only=True)
            self.assertEqual((tested, valid * len(sp.passes)), (cpu_tested, cpu_derived), name)
            for i in surv[:50]:
                self.assertTrue(sp.candidate_ok(i), (name, i))

    def test_full_permutation_space_needs_no_table(self):
        w12 = WORDS[100:112]
        sp = Space(cfg_from_text(make_text("bip39", 12, [], w12, [])))
        self.assertEqual(sp.total, 479001600)
        self.assertEqual(sp.decode(0), w12)
        self.assertEqual(sp.decode(sp.total - 1), list(reversed(w12)))

        def lehmer(i, items):
            items, out = list(items), []
            for j in range(len(items), 0, -1):
                s, i = divmod(i, math.factorial(j - 1))
                out.append(items.pop(s))
            return out

        for i in (1, 12345, 123456789, 479001599 - 7):
            slots = [None] * 12
            for j, pos in enumerate(lehmer(i, range(12))):
                slots[pos] = w12[j]
            self.assertEqual(sp.decode(i), slots, i)
        first3 = [s for _, s in itertools.islice(sp_ref.ref_walk(sp.cfg), 3)]
        self.assertEqual([sp.decode(i) for i in range(3)], first3)

    def test_rejections(self):
        base = dict(scheme="bip39", n=12, fix=[(s, "abandon") for s in range(1, 12)])
        with self.assertRaises(SpaceError):          # empty pool with an unknown slot
            Space(cfg_from_text(make_text(base["scheme"], 12, base["fix"], [], [])))
        with self.assertRaises(SpaceError):          # passphrase over 99 bytes
            Space(cfg_from_text(make_text("bip39", 12, base["fix"], [], ["@WORDLIST"], passes=("p" * 100,))))
        with self.assertRaises(SpaceError):          # bip39 needs 12/15/18/21/24 words
            Space(cfg_from_text(make_text("bip39", 13, [(s, "abandon") for s in range(1, 13)], [], ["@WORDLIST"])))
        deep = "m/" + "/".join(["0"] * 9)
        with self.assertRaises(SpaceError):          # path deeper than SP_MAXD
            Space(cfg_from_text(make_text("bip39", 12, base["fix"], [], ["@WORDLIST"], paths=(deep,))))

    def test_non_ascii_passphrase_is_nfkd_utf8(self):
        sp = Space(cfg_from_text(make_text("bip39", 12, [(s, "abandon") for s in range(1, 12)], [],
                                           ["@WORDLIST"], passes=("café ёж",))))
        self.assertIn(sps.normalize_pass("café ёж", "bip39"), sp.passes)
        self.assertLessEqual(max(len(p.encode()) for p in sp.passes), 99)


class Tables(unittest.TestCase):
    def test_descriptor_token_salt_and_path_tables(self):
        cfg = cfg_from_text(dict(small_config_texts())["F_electrum_12_freewords"])
        sp = Space(cfg)
        d = sp.descriptor()
        self.assertEqual(d.dtype, np.uint64)
        self.assertEqual(len(d), sp_space.SP_D_WORDS)
        L = sp_space.LAYOUT
        self.assertEqual(int(d[L["SP_D_N"]]), sp.n)
        self.assertEqual(int(d[L["SP_D_TOTAL"]]), sp.total)
        self.assertEqual(int(d[L["SP_D_MODE"]]), 2)
        self.assertEqual(int(d[L["SP_D_MODE"]]), sp.mode)
        self.assertEqual(int(sp.descriptor(0)[L["SP_D_MODE"]]), 0)
        mids = sp_ref.hmac_midstates(b"Seed version")
        self.assertEqual([int(x) for x in d[L["SP_O_MID"]:L["SP_O_MID"] + 16]], mids[0] + mids[1])
        tokb, tokl = sp.token_tables()
        self.assertEqual(len(tokl), len(sp.tokens))
        t = sp.tok_id["breathe"]
        self.assertGreaterEqual(t, 2048)
        self.assertEqual(bytes(tokb[t * sp_space.SP_TOKW:t * sp_space.SP_TOKW + int(tokl[t])]), b"breathe")
        salts = sp.salt_words()
        self.assertEqual(salts.shape, (len(sp.passes), 16))
        pt = sp_space.path_table([parse_path(p) for p in ("m/0/0", "m/0/1", "m/1/0", "m/1/0", "m/1/0/5", "m")])
        self.assertEqual(pt.dtype, np.uint32)
        self.assertEqual([tuple(map(int, r[:2])) for r in pt], [(2, 0), (2, 1), (2, 0), (2, 2), (3, 2), (0, 0)])
        self.assertEqual(list(map(int, pt[4][2:5])), [1, 0, 5])

    def test_every_descriptor_section_reads_back_and_salts_use_the_scheme(self):
        """A swapped offset, an inverted flag or a hard-coded salt scheme would survive the
        decode tests on the CPU side; read each section back against the Space attribute."""
        L = sp_space.LAYOUT
        for name, text in small_config_texts():
            sp = Space(cfg_from_text(text))
            d = sp.descriptor()

            def sect(key, vals):
                return [int(x) for x in d[L[key]:L[key] + len(vals)]], [int(v) for v in vals]

            for got, want in (sect("SP_O_FREE", sp.free), sect("SP_O_PW", sp.place_w), sect("SP_O_REQ", sp.req_ids),
                              sect("SP_O_ALTSLOT", sp.alt_slot), sect("SP_O_ALTRAD", sp.alt_rad),
                              sect("SP_O_ALTOFF", sp.alt_off), sect("SP_O_ALTTOK", sp.alt_tok),
                              sect("SP_O_POOL", sp.pool_ids)):
                self.assertEqual(got, want, name)
            scalars = {"SP_D_N": sp.n, "SP_D_R": sp.r, "SP_D_M": sp.m, "SP_D_K": sp.k,
                       "SP_D_NALT": len(sp.alt_slot), "SP_D_NPOOL": sp.npool, "SP_D_PERUNIT": sp.per_unit,
                       "SP_D_TOTAL": sp.total, "SP_D_SPAN": sp.span,
                       "SP_D_DISTINCT": 1 if sp.cfg["distinct"] else 0}
            for key, want in scalars.items():
                self.assertEqual(int(d[L[key]]), want, (name, key))
            for i, pp in enumerate(sp.passes):
                self.assertEqual([int(x) for x in sp.salt_words()[i]],
                                 sp_ref.salt_block_words(sp.scheme, pp.encode("utf-8")), (name, i))
        # the two schemes must give different salt blocks for the same passphrase
        self.assertNotEqual(sp_ref.salt_block_words("bip39", b""), sp_ref.salt_block_words("electrum", b""))

    def test_estimate(self):
        sp = Space(cfg_from_text(dict(small_config_texts())["D_electrum_12_wordlist"]))
        e = sp.estimate()
        self.assertAlmostEqual(e["valid_rate"], 1 / 256)
        self.assertAlmostEqual(e["derivations"], sp.total / 256 * len(sp.passes))
        e4 = Space.load("templates/e4_pins_only.conf").estimate()
        self.assertEqual(e4["raw"], 20160 * 2048 ** 2)


if __name__ == "__main__":
    unittest.main()
