"""bip39raw - Electrum's "BIP39 seed" import of ANY text - and its alias NOCHECKSUM.

Electrum 3.3.8 keystore.bip39_to_seed derives seed = PBKDF2-HMAC-SHA512(NFKD(' '.join(text.split())),
'mnemonic' + NFKD(passphrase), 2048): no word-count, wordlist or checksum test, no lowercasing. The known answers are
the committed file artifacts/external-reports-2026-10-01/finders/L5-files/known_answer_vectors.txt, which
kav_independent.py re-derives with code that shares nothing with the harness (30 of 30 addresses). They are ALSO
written out below as literals, and the two must agree: a missing or edited file FAILS here, it never skips."""
import hashlib
import math
import os
import re
import unicodedata
import unittest

import setperm_solve as sps
import sp_ref
import sp_space
from sp_space import Space, SpaceError
from sp_testutil import cfg_from_text, make_text, require_gpu, small_config_texts
from template_solve import IDX, WORDS, address_of_hash160, parse_path

HERE = os.path.dirname(os.path.abspath(__file__))
KAV_FILE = os.path.join(HERE, "..", "artifacts", "external-reports-2026-10-01", "finders", "L5-files",
                        "known_answer_vectors.txt")
DUMMY_TARGET = address_of_hash160(bytes(range(20)))          # count_only runs never compare; still, never the prize
BIP44 = "m/44'/0'/0'/0/0"

ZW = "zoo wrong zoo wrong zoo wrong zoo wrong zoo wrong zoo"   # 11 words
TEXT = {"V0": " ".join(["abandon"] * 11 + ["about"]),         # the public BIP39 vector (checksum-valid): the control
        "V1": ZW + " wrong zoo",                               # 13 words: BIP39 forbids the length
        "V2": (ZW + " wrong zoo").upper(),                     # V1 in upper case: the raw text keeps its case
        "V3": ZW + " breathe",                                 # 12 words, checksum-invalid, a non-BIP39 token
        "V4": ZW + " action",                                  # an Electrum-native seed ('01' version prefix)
        "V5": " ".join(["abandon"] * 11 + ["about"])}          # V0's text with a Cyrillic passphrase
# V5's passphrase 'чёрный день' in NFC (11 code points), as the vector file writes it
V5_PASS = "\u0447\u0451\u0440\u043d\u044b\u0439 \u0434\u0435\u043d\u044c"
# (vector, the passphrase or Electrum extension as typed - for V5 the form it was derived from -, path)
#   -> (compressed P2PKH address, uncompressed or None)
KAV = {
    ("V0", "", BIP44): ("1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA", "18LhnLKXjcTw5xJFiTxntnKit2Gd63eWFm"),
    ("V1", "", BIP44): ("155JeWn6UiXMftaD2nHzPsYHA1rnZJMhjx", "1aLPxUUYfJfQPw6UfZXnPzGyKveFuMsvW"),
    ("V1", "", "m/44'/0'/0'/0/10"): ("18TjqBVpjqgyAx9dP1TyXvVXPtUSa13aCz", "1PLmszXc2WJjGopdr5S6SE9SdQ3vXMxVyP"),
    ("V1", "", "m/0'/0/0"): ("1B5YHBpJCmNiei1JWbDAK7NfVBy2Ao8vzU", "1om45ChsrAmm7PWRk3Kn63J27iHkxfPmq"),
    ("V1", "", "m/0/0"): ("13ePFtqF7HKvcCGfrZFJpsp2fxMCDdcrnP", "15jVniiw1cTcrTY9mmXQJTa1GGT1Myw37c"),
    ("V1", "moon", BIP44): ("1Gz25UhhoN3gGxCMPZjQksQS4Gekxmi5R7", "15tafztYVqqT1KwZSLom9QwfFQxCbzJryU"),
    ("V1", "MOON", BIP44): ("1CddC4oefuiErwFM1FYPobsgwEXzds9nb", "1PU4VLtfVuZusPjxwKb3rv6UuhSAevBFSs"),
    ("V2", "", BIP44): ("1AXkr6uEyJSTu8DTQJGvFaowrs8ismdZ87", "1B1BEAXW3jGZdmrwGsTLyowuphi7wAV4zG"),
    ("V3", "", BIP44): ("16NbGKRBg85nfq5WmziBD5Nr7ZLrTrmFaU", "19NmzDdAQ4nBys48CW96GCWD1vxwW3s6Uo"),
    ("V4", "", "m/0/0"): ("1HZWPkxs72HrFVu3h9ePKvgAiX9PEYMbra", "17uinhLXiqj1omdgit2pt8hmXzLivNrf3B"),
    ("V4", "", "m/0/10"): ("1GvXwNQmxk1CnAhBWTJoGwNfjrUpBppCwd", "174UHa6adjfSzcmFDVsax4PpSwG6AzMHyE"),
    ("V4", "", "m/1/0"): ("1GuzH2zpiVNcpmeVXQihEuVuoV7HRXM2Gz", "13mhwTCdh6fxUg4NojwCJXqBkA9eaZymxK"),
    ("V4", "moon", "m/0/0"): ("1BaMtToafc7yT7yQfj5ELfyRFV2BdazHZi", "1H2JeMSNcpHFEjGjprVwXQv21wKD16KJzR"),
    ("V4", "MOON", "m/0/0"): ("1BaMtToafc7yT7yQfj5ELfyRFV2BdazHZi", "1H2JeMSNcpHFEjGjprVwXQv21wKD16KJzR"),
    ("V5", "NFKD", BIP44): ("1BGKQaECAZnFHfKUXmD4cdDVswyfS7G1p5", None),
    ("V5", "NFC", BIP44): ("1KRP27R9gtsTSEnA6Pbev5zKbC1Cym6uhE", None),   # the NFC bytes un-normalised: never ours
}


def _vectors_in_file():
    """The vector file in KAV's shape. Line grammar as kav_independent.py reads it."""
    line_re = re.compile(r"^(V\d)\s+(.*?)\s+(m(?:/\d+'?)*)\s+C:(\S+)(?:\s+U:(\S+))?\s*$")
    out = {}
    with open(KAV_FILE, encoding="utf-8") as f:
        for raw in f:
            m = line_re.match(raw.rstrip("\n"))
            if not m:
                continue
            vid, note, path, c, u = m.groups()
            if vid == "V5":
                key = "NFKD" if "NFKD" in note else "NFC"
            else:
                q = re.search(r"(?:pass|ext)='([^']*)'", note)
                key = q.group(1) if q else ""
            if (vid, key, path) in out:
                raise AssertionError(f"vector line twice: {(vid, key, path)}")
            out[(vid, key, path)] = (c, u)
    return out


def _addresses(text, pp, paths, scheme):
    return [address_of_hash160(h) for _, _, h in sp_ref.hash160_forms(text, pp, [parse_path(p) for p in paths], scheme)]


class KnownAnswers(unittest.TestCase):
    def test_the_literals_are_the_committed_vector_file(self):
        self.assertTrue(os.path.isfile(KAV_FILE), f"the known-answer vector file is missing: {KAV_FILE}")
        got = _vectors_in_file()
        self.assertEqual(got, KAV)
        self.assertEqual(sum(1 for pair in got.values() for a in pair if a), 30)     # every address the file holds

    def test_raw_vectors_in_both_key_forms_through_sp_ref(self):
        """V0-V3 under bip39raw: salt 'mnemonic' + NFKD(passphrase) with the case kept (V1's 'moon' and 'MOON' are two
        wallets), any word count (V1: 13), any token (V3: 'breathe'), no checksum (V1-V3 all fail BIP39's)."""
        self.assertNotIn("breathe", IDX)
        n = 0
        for (vid, pp, path), (c, u) in KAV.items():
            if vid in ("V0", "V1", "V2", "V3"):
                got = _addresses(TEXT[vid], sps.normalize_pass(pp, "bip39raw"), [path], "bip39raw")
                self.assertEqual(got, [c, u], (vid, pp, path))
                n += 1
        self.assertEqual(n, 9)
        self.assertNotEqual(KAV[("V1", "moon", BIP44)], KAV[("V1", "MOON", BIP44)])
        self.assertNotEqual(KAV[("V1", "", BIP44)], KAV[("V2", "", BIP44)])

    def test_the_passphrase_is_nfkd_normalised_so_its_nfc_form_gives_the_nfkd_address(self):
        nfkd = unicodedata.normalize("NFKD", V5_PASS)
        self.assertEqual(V5_PASS, unicodedata.normalize("NFC", V5_PASS))
        self.assertEqual((len(V5_PASS), len(nfkd)), (11, 13))                     # e-diaeresis and i-breve decompose
        self.assertEqual(sps.normalize_pass(V5_PASS, "bip39raw"), nfkd)
        self.assertEqual(sps.normalize_pass(nfkd, "bip39raw"), nfkd)
        want = KAV[("V5", "NFKD", BIP44)][0]
        self.assertEqual(_addresses(TEXT["V5"], sps.normalize_pass(V5_PASS, "bip39raw"), [BIP44], "bip39raw")[0], want)
        # the same passphrase as raw NFC bytes is another wallet - the address a non-normalising tool would give
        self.assertEqual(_addresses(TEXT["V5"], V5_PASS, [BIP44], "bip39raw")[0], KAV[("V5", "NFC", BIP44)][0])
        self.assertNotEqual(want, KAV[("V5", "NFC", BIP44)][0])

    def test_the_electrum_native_vector_and_its_lowercased_extension(self):
        self.assertTrue(sps.electrum_standard(TEXT["V4"]))
        n = 0
        for (vid, ext, path), (c, u) in KAV.items():
            if vid == "V4":
                self.assertEqual(_addresses(TEXT["V4"], sps.normalize_pass(ext, "electrum"), [path], "electrum"), [c, u],
                                 (ext, path))
                n += 1
        self.assertEqual(n, 5)
        self.assertEqual(KAV[("V4", "moon", "m/0/0")], KAV[("V4", "MOON", "m/0/0")])     # Electrum lowercases it
        self.assertNotEqual(KAV[("V4", "", "m/0/0")], KAV[("V4", "moon", "m/0/0")])

    def test_the_vectors_come_out_of_a_bip39raw_config_the_way_a_run_derives_them(self):
        """The chain a run uses: load_config -> Space (passphrases normalised there) -> decode -> sp_ref with the
        Space's own passphrases, paths and scheme. V3 is written as SCHEME bip39 + NOCHECKSUM, V5's passphrase in NFC;
        the decoded text must be the vector's text."""
        for vid in ("V0", "V1", "V2", "V3", "V5"):
            rows = {(key, path): pair for (v, key, path), pair in KAV.items() if v == vid}
            sp = Space(cfg_from_text(_vector_config(vid)))
            self.assertEqual((sp.scheme, sp.total, sp.mode), ("bip39raw", 1, sp_space.MODE_NONE), vid)
            self.assertTrue(sp.candidate_ok(0), vid)
            m = " ".join(sp.decode(0))
            self.assertEqual(m, TEXT[vid], vid)
            got = {(pi, path_i, form): address_of_hash160(h) for pi, pp in enumerate(sp.passes)
                   for path_i, form, h in sp_ref.hash160_forms(m, pp, sp.paths, sp.scheme)}
            for (key, path), (c, u) in rows.items():
                if key == "NFC":
                    self.assertNotIn(c, got.values(), vid)                          # the engine never derives it
                    continue
                pi = sp.passes.index(unicodedata.normalize("NFKD", V5_PASS) if key == "NFKD" else key)
                path_i = sp.paths_txt.index(path)
                self.assertEqual(got[(pi, path_i, 0)], c, (vid, key, path))
                if u:
                    self.assertEqual(got[(pi, path_i, 1)], u, (vid, key, path))
        one = Space(cfg_from_text(make_text("bip39raw", 1, [], [], ["x"], passes=("moon", "MOON"))))
        self.assertEqual(one.passes, ["", "moon", "MOON"])                     # case kept: two passphrases


# ---------------------------------------------------------------- the config grammar

def _body(n=13):
    """A bip39raw config body without its SCHEME line: an upper-case FIX alternative, a non-BIP39 REQUIRE word, a
    POOL of case variants, a passphrase and two paths."""
    fix = [(s, "zoo" if s % 2 else "wrong") for s in range(1, n + 1) if s not in (2, 5, 9)]
    fix[0] = (1, "TOWER|tower|Tower")
    text = make_text("bip39raw", n, fix, ["MOON", "breathe"], ["zoo Zoo ZOO quixotic"], passes=("TREZOR",),
                     paths=("m/0/0", BIP44))
    return text.split("\n", 1)[1]


def _refusal(test, text):
    with test.assertRaises(SystemExit) as cm:
        cfg_from_text(text)
    return str(cm.exception.code)


class RawGrammar(unittest.TestCase):
    def test_nochecksum_is_exactly_scheme_bip39raw_in_either_order(self):
        body = _body()
        want = cfg_from_text("SCHEME bip39raw\n" + body)
        self.assertEqual(want["scheme"], "bip39raw")
        sp0 = Space(want)
        variants = {"SCHEME bip39, then NOCHECKSUM": "SCHEME bip39\nNOCHECKSUM\n" + body,
                    "NOCHECKSUM, then SCHEME bip39": "NOCHECKSUM\nSCHEME bip39\n" + body,
                    "NOCHECKSUM as the last line": "SCHEME bip39\n" + body + "NOCHECKSUM\n",
                    "no SCHEME line (bip39 is the default)": "NOCHECKSUM\n" + body,
                    "SCHEME bip39raw and NOCHECKSUM": "SCHEME bip39raw\nNOCHECKSUM\n" + body,
                    "any case": "scheme BIP39\nnochecksum   # a comment\n" + body,
                    "SCHEME BIP39RAW": "SCHEME BIP39RAW\n" + body}
        for name, text in variants.items():
            cfg = cfg_from_text(text)
            self.assertEqual(cfg, want, name)
            self.assertEqual(sps.run_signature(cfg, DUMMY_TARGET, sp0.paths_txt, sp0.passes),
                             sps.run_signature(want, DUMMY_TARGET, sp0.paths_txt, sp0.passes), name)
            sp = Space(cfg)
            self.assertEqual((sp.scheme, sp.mode, sp.total, sp.units, sp.per_unit, sp.span, sp.tokens, sp.passes),
                             (sp0.scheme, sp0.mode, sp0.total, sp0.units, sp0.per_unit, sp0.span, sp0.tokens, sp0.passes),
                             name)
        # ... and it is not plain bip39: that refuses 13 words and the free tokens
        self.assertIn("not in wordlist", _refusal(self, "SCHEME bip39\n" + body))

    def test_nochecksum_with_electrum_is_refused(self):
        body = (_body().replace("MOON breathe", "abandon ability").replace("TOWER|tower|Tower", "tower")
                .replace("zoo Zoo ZOO quixotic", "zoo wrong"))                    # wordlist words only
        self.assertEqual(cfg_from_text("SCHEME electrum\n" + body)["scheme"], "electrum")   # fine for electrum ...
        for head in ("SCHEME electrum\nNOCHECKSUM\n", "NOCHECKSUM\nSCHEME electrum\n"):
            msg = _refusal(self, head + body)                                    # ... but not with NOCHECKSUM
            self.assertIn("NOCHECKSUM", msg)
            self.assertIn("electrum", msg)

    def test_freewords_is_refused_with_bip39raw_which_needs_none(self):
        for head in ("SCHEME bip39raw\nFREEWORDS\n", "FREEWORDS\nSCHEME bip39\nNOCHECKSUM\n"):
            msg = _refusal(self, head + _body())
            self.assertIn("FREEWORDS", msg)
            self.assertIn("bip39raw needs no FREEWORDS", msg)
        self.assertIn("SCHEME electrum", _refusal(self, "SCHEME bip39\nFREEWORDS\n" + _body()))   # unchanged for bip39

    def test_an_unknown_scheme_is_still_refused(self):
        with self.assertRaises((AssertionError, SystemExit)):
            cfg_from_text("SCHEME bip39rawx\n" + _body())

    def test_tokens_keep_their_case_need_no_wordlist_and_are_nfkd_normalised(self):
        cafe_nfc, cafe_nfkd = "caf\u00e9", "cafe\u0301"
        black = "\u0447\u0451\u0440\u043d\u044b\u0439"                          # 'чёрный' (NFC), 16 bytes as NFKD
        fix = [(1, "MOON"), (2, "moon"), (3, "TOWER|tower|\uff34\uff4f\uff57\uff45\uff52"), (4, cafe_nfc)]
        fix += [(s, "zoo") for s in range(5, 12)]
        text = make_text("bip39raw", 13, fix, ["breathe", black], ["Zoo zoo ZOO \ufb01x " + cafe_nfkd + " " + cafe_nfc])
        cfg = cfg_from_text(text)
        self.assertEqual(cfg["fix"][1], "MOON")                                  # case kept: MOON != moon
        self.assertEqual(cfg["fix"][2], "moon")
        self.assertEqual(cfg["fix"][3], "TOWER|tower|Tower")                     # fullwidth -> ASCII under NFKD
        self.assertEqual(cfg["fix"][4], cafe_nfkd)
        self.assertEqual(cfg["require"], ["breathe", unicodedata.normalize("NFKD", black)])
        self.assertEqual(cfg["pool"], ["Zoo", "zoo", "ZOO", "fix", cafe_nfkd])  # ligature split; NFC = NFD: one token
        self.assertNotIn("breathe", IDX)
        self.assertNotIn("MOON", IDX)
        sp = Space(cfg)
        self.assertNotEqual(sp.tok_id["MOON"], sp.tok_id["moon"])
        self.assertEqual(len(sp.tok_bytes[sp.tok_id[unicodedata.normalize("NFKD", black)]]), 16)

    def test_pool_wordlist_still_expands_to_the_2048_words(self):
        cfg = cfg_from_text(make_text("bip39raw", 3, [(1, "MOON"), (2, "breathe")], [], ["@WORDLIST Zoo"]))
        self.assertEqual(cfg["pool"], WORDS + ["Zoo"])
        self.assertEqual(Space(cfg).total, 2049)

    def test_an_empty_or_whitespace_holding_token_is_refused(self):
        diaeresis = "\u00a8"                                                    # NFKD: a space + U+0308
        self.assertNotEqual(unicodedata.normalize("NFKD", diaeresis).split(), [unicodedata.normalize("NFKD", diaeresis)])
        fix = [(s, "zoo") for s in range(2, 13)]
        cases = {"an empty FIX alternative": make_text("bip39raw", 12, [(1, "a||b")] + fix, [], []),
                 "a FIX alternative left empty at the end": make_text("bip39raw", 12, [(1, "a|")] + fix, [], []),
                 "NFKD gives a space (FIX)": make_text("bip39raw", 12, [(1, "a" + diaeresis)] + fix, [], []),
                 "NFKD gives a space (REQUIRE)": make_text("bip39raw", 12, fix, [diaeresis + "b"], []),
                 "NFKD gives a space (POOL)": make_text("bip39raw", 12, fix, [], ["ok x" + diaeresis]),
                 # U+FF5C (fullwidth |) becomes '|' under NFKD, the FIX alternative separator: refused, never split
                 "NFKD gives a '|' inside a FIX alternative": make_text("bip39raw", 12, [(1, "a\uff5cb")] + fix, [], [])}
        for name, text in cases.items():
            msg = _refusal(self, text)
            self.assertIn("bip39raw", msg, name)
        # a '|' inside a REQUIRE or POOL token is just a character: Electrum would derive it as written
        cfg = cfg_from_text(make_text("bip39raw", 12, fix, ["a|b"], ["c\uff5cd"]))
        self.assertEqual((cfg["require"], cfg["pool"]), (["a|b"], ["c|d"]))


# ---------------------------------------------------------------- the GPU engine's Space

class RawSpace(unittest.TestCase):
    def test_any_word_count_from_1_to_24_and_plain_bip39_keeps_its_lengths(self):
        for n in (1, 2, 11, 12, 13, 17, 23, 24):
            sp = Space(cfg_from_text(make_text("bip39raw", n, [(s, "zoo") for s in range(2, n + 1)], [],
                                               ["MOON breathe"])))
            self.assertEqual((sp.n, sp.total, sp.mode), (n, 2, sp_space.MODE_NONE), n)
            self.assertEqual(" ".join(sp.decode(1)), " ".join(["breathe"] + ["zoo"] * (n - 1)), n)
        for n in (0, 25):
            with self.assertRaises(SpaceError, msg=n):
                Space(cfg_from_text(make_text("bip39raw", n, [], [], ["zoo"])))
        text13 = make_text("bip39", 13, [(s, "zoo") for s in range(2, 14)], [], ["abandon ability"])
        with self.assertRaises(SpaceError):                                      # plain bip39 keeps 12/15/18/21/24
            Space(cfg_from_text(text13))
        self.assertEqual(Space(cfg_from_text(text13.replace("SCHEME bip39\n", "SCHEME bip39raw\n"))).total, 2)

    def test_a_token_over_16_bytes_is_refused_and_16_bytes_fit(self):
        black = "\u0447\u0451\u0440\u043d\u044b\u0439"                          # NFKD: 8 code points, 16 bytes
        fix = [(s, "zoo") for s in range(2, 13)]
        for tok in (black, "ABCDEFGHIJKLMNOP"):
            Space(cfg_from_text(make_text("bip39raw", 12, fix, [], [tok])))
        for tok in (black + "\u0439", "ABCDEFGHIJKLMNOPQ"):                      # 20 and 17 bytes
            with self.assertRaises(SpaceError, msg=tok):
                Space(cfg_from_text(make_text("bip39raw", 12, fix, [], [tok])))

    def test_mode_none_every_candidate_survives_and_the_rate_is_one(self):
        text = make_text("bip39raw", 13, [(1, "TOWER|tower")] + [(s, "zoo") for s in (3, 4, 6, 7, 8, 10, 11, 12)],
                         ["MOON", "breathe"], ["Zoo zoo wrong quixotic"], passes=("TREZOR",), paths=("m/0/0", BIP44))
        cfg = cfg_from_text(text)
        sp = Space(cfg)
        self.assertEqual(sp.mode, sp_space.MODE_NONE)
        self.assertEqual(sp_space.MODE_NONE, 0)
        self.assertEqual(int(sp.descriptor()[sp_space.LAYOUT["SP_D_MODE"]]), 0)
        self.assertEqual(sp.total, 4 * 3 * 2 * 4 ** 2)                           # placements x alternatives x pool^2
        self.assertTrue(all(sp.candidate_ok(i) for i in range(sp.total)))
        e = sp.estimate()
        self.assertEqual(e["valid_rate"], 1)
        self.assertEqual((e["valid"], e["derivations"]), (sp.total, sp.total * 2))
        self.assertEqual(sp_ref.ref_survivors(cfg), (sp.total, sp.total, list(range(sp.total))))
        self.assertEqual(sps.run(cfg, DUMMY_TARGET, 1, quiet=True, count_only=True), (sp.total, sp.total * 2))
        for scheme in ("bip39", "electrum"):                                     # the filtered schemes are unchanged
            other = Space(cfg_from_text(make_text(scheme, 12, [(s, "zoo") for s in range(2, 13)], [], ["@WORDLIST"])))
            self.assertEqual(other.mode, sp_space.MODE_BIP39 if scheme == "bip39" else sp_space.MODE_ELECTRUM)

    def test_with_distinct_the_survivors_are_exactly_the_distinct_candidates(self):
        fix = [(s, w) for s, w in zip((1, 3, 4, 6, 7, 8, 10, 11, 12), ("Zoo", "a", "b", "c", "d", "e", "f", "g", "h"))]
        text = make_text("bip39raw", 12, fix, ["MOON"], ["Zoo zoo MOON moon x y"], distinct=True)
        cfg = cfg_from_text(text)
        sp = Space(cfg)
        want = [i for i in range(sp.total) if len(set(sp.decode(i))) == sp.n]
        self.assertTrue(0 < len(want) < sp.total)
        self.assertEqual([i for i in range(sp.total) if sp.candidate_ok(i)], want)
        self.assertEqual(sp_ref.ref_survivors(cfg), (len(want), len(want), want))
        self.assertEqual(sps.run(cfg, DUMMY_TARGET, 1, quiet=True, count_only=True), (len(want), len(want)))

    def test_space_refuses_raw_tokens_that_load_config_would_have_normalised_or_refused(self):
        """Space is the GPU engine's entry: a hand-built config that bypassed load_config must not reach the kernels
        with a token whose text Electrum would change (not NFKD, or holding whitespace), nor with an unknown scheme."""
        good = cfg_from_text(make_text("bip39raw", 12, [(s, "zoo") for s in range(2, 13)], [], ["MOON"]))
        Space(good)
        for field, bad in (("pool", ["caf\u00e9"]), ("pool", ["a b"]), ("require", [" x"]), ("pool", [""])):
            cfg = dict(good, **{field: bad})
            with self.assertRaises(SpaceError, msg=(field, bad)):
                Space(cfg)
        with self.assertRaises(SpaceError):
            Space(dict(good, scheme="bip39rawx"))


class SmallConfigs(unittest.TestCase):
    # sha256 over (name, text) of the eleven configs A-K exactly as master's sp_testutil built them before bip39raw
    A_TO_K = "4ea8529091b305d925b30efa3c952b8259409e60ab93956f2271b0d51f619245"

    def test_the_raw_configs_are_appended_and_the_eleven_before_them_are_unchanged(self):
        pairs = small_config_texts()
        h = hashlib.sha256()
        for name, text in pairs[:11]:
            h.update(f"{name}\0{text}\0".encode("utf-8"))
        self.assertEqual(h.hexdigest(), self.A_TO_K)            # the configs every older test was written against
        raw = dict(pairs[11:])
        self.assertEqual(list(raw), ["L_raw_13_case_free", "M_raw_12_nochecksum_distinct", "N_raw_7_r0_unicode",
                                     "O_raw_24_wordlist"])
        for name, text in raw.items():
            cfg = cfg_from_text(text)
            sp = Space(cfg)
            tested, valid, _ = sp_ref.ref_survivors(cfg)
            self.assertEqual((cfg["scheme"], sp.mode), ("bip39raw", sp_space.MODE_NONE), name)
            self.assertEqual(tested, valid, name)
            self.assertTrue(0 < tested < 10_000, (name, tested))     # never judged by the tests that presume a filter
            self.assertGreater(sp.total, 37, name)                   # WindowAuditNoGpu needs more than one 37-wide chunk
        self.assertIn("NOCHECKSUM\n", raw["M_raw_12_nochecksum_distinct"])
        self.assertTrue(cfg_from_text(raw["M_raw_12_nochecksum_distinct"])["distinct"])
        toks = Space(cfg_from_text(raw["L_raw_13_case_free"])).tokens
        self.assertTrue({"MOON", "quixotic", "TOWER", "Tower", "tower", "BREATHE", "breathe", "Zoo"} <= set(toks))
        self.assertEqual(Space(cfg_from_text(raw["N_raw_7_r0_unicode"])).r, 0)
        o = Space(cfg_from_text(raw["O_raw_24_wordlist"]))
        self.assertEqual((o.n, o.passes, len(o.paths)), (24, ["", "TREZOR"], 2))


# ---------------------------------------------------------------- PASSFILE

def _load_in_dir(conf_text, files=(), cwd=None, relative=False):
    """load_config on `conf_text` saved as <tmp>/conf/x.conf, after writing `files` ({path under <tmp>/conf: bytes});
    '@DIR@' in the text is <tmp>/conf. `cwd`: a directory to run from (it holds a decoy p.txt); `relative`: hand
    load_config the path 'conf/x.conf' from <tmp> instead of the absolute one."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "conf")
        os.makedirs(base)
        for rel, data in dict(files).items():
            p = os.path.join(base, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
        conf = os.path.join(base, "x.conf")
        with open(conf, "w", encoding="utf-8") as f:
            f.write(conf_text.replace("@DIR@", base))
        old = os.getcwd()
        try:
            if relative:
                os.chdir(d)
            elif cwd:
                os.chdir(cwd)
            return sps.load_config("conf/x.conf" if relative else conf)
        finally:
            os.chdir(old)


RAW_BASE = make_text("bip39raw", 3, [(1, "MOON")], ["breathe"], ["zoo"])          # no PASS line: passes [''] + file


class PassFile(unittest.TestCase):
    def test_lines_are_verbatim_blank_lines_skipped_the_bom_dropped_and_crlf_handled(self):
        data = ("\ufeff" + "  lead and trail  \r\n" + "with # a hash\n" + "\n" + "\r\n" + "   \n" + "MOON\r\n"
                + "moon\n" + "tab\there\n" + "a\rb\n" + "c\r\r\n" + "\ufeffinner bom\n" + "last, no newline")
        cfg = _load_in_dir(RAW_BASE + "PASSFILE p.txt\n", {"p.txt": data.encode("utf-8")})
        self.assertEqual(cfg["pass"], ["", "  lead and trail  ", "with # a hash", "   ", "MOON", "moon", "tab\there",
                                       "a\rb", "c\r", "\ufeffinner bom", "last, no newline"])

    def test_it_adds_to_the_pass_list_like_pass_lines_deduplicated_and_it_repeats(self):
        # (no trailing comment on the PASSFILE line any more: a '#' there is refused, see HashOnATokenLine)
        cfg = _load_in_dir(RAW_BASE + "PASS moon\nPASSFILE a.txt\nPASS late\nPASSFILE sub/b.txt\n",
                           {"a.txt": b"MOON\nmoon\nlate\n", "sub/b.txt": b"late\nnew\nMOON\n"})
        self.assertEqual(cfg["pass"], ["", "moon", "MOON", "late", "new"])
        same = cfg_from_text(RAW_BASE + "".join(f"PASS {p}\n" for p in ("moon", "MOON", "late", "new")))
        self.assertEqual(cfg, same)                                          # exactly what the PASS lines give
        self.assertEqual(sps.run_signature(cfg, DUMMY_TARGET, [BIP44], cfg["pass"]),
                         sps.run_signature(same, DUMMY_TARGET, [BIP44], same["pass"]))

    def test_a_relative_path_is_the_config_directorys_and_an_absolute_path_is_taken_as_given(self):
        import tempfile
        files = {"p.txt": b"rel\n", "sp ace/q.txt": b"spaced\n", "abs.txt": b"absolute\n"}
        with tempfile.TemporaryDirectory() as cwd:
            with open(os.path.join(cwd, "p.txt"), "w", encoding="utf-8") as f:
                f.write("DECOY\n")                                           # the working directory's p.txt: never read
            cfg = _load_in_dir(RAW_BASE + "PASSFILE p.txt\nPASSFILE sp ace/q.txt\nPASSFILE @DIR@/abs.txt\n", files,
                               cwd=cwd)
        self.assertEqual(cfg["pass"], ["", "rel", "spaced", "absolute"])
        cfg = _load_in_dir(RAW_BASE + "PASSFILE p.txt\nPASSFILE ../conf/sp ace/q.txt\n", files, relative=True)
        self.assertEqual(cfg["pass"], ["", "rel", "spaced"])                 # a relative config path works the same

    def test_a_missing_unreadable_or_non_utf8_file_is_a_clean_refusal(self):
        cases = {"missing": (RAW_BASE + "PASSFILE nope.txt\n", {}),
                 "a directory": (RAW_BASE + "PASSFILE sub\n", {"sub/x.txt": b"x\n"}),
                 "not UTF-8": (RAW_BASE + "PASSFILE bad.txt\n", {"bad.txt": b"ok\n\xff\xfe\n"}),
                 "no file name": (RAW_BASE + "PASSFILE\n", {})}
        for name, (text, files) in cases.items():
            with self.assertRaises(SystemExit, msg=name) as cm:
                _load_in_dir(text, files)
            self.assertIsInstance(cm.exception.code, str, name)              # sys.exit(message): exit status 1
            self.assertIn("PASSFILE", cm.exception.code, name)
        with self.assertRaises(SystemExit) as cm:
            _load_in_dir(RAW_BASE + "PASSFILE nope.txt\n")
        self.assertIn("nope.txt", cm.exception.code)

    def test_its_passphrases_are_normalised_by_the_scheme_like_pass_lines(self):
        files = {"p.txt": "caf\u00e9\ncafe\u0301\nBREATHE\n".encode("utf-8")}
        raw = Space(_load_in_dir(RAW_BASE + "PASSFILE p.txt\n", files))
        self.assertEqual(raw.passes, ["", "cafe\u0301", "BREATHE"])           # NFKD (NFC and NFD are one), case kept
        el = Space(_load_in_dir(make_text("electrum", 12, [(s, "zoo") for s in range(2, 13)], [], ["@WORDLIST"])
                                + "PASSFILE p.txt\n", files))
        self.assertEqual(el.passes, ["", "cafe", "breathe"])                 # Electrum: lowercased, accents dropped

    def test_the_4096_passphrase_and_99_byte_caps_still_hold(self):
        import gpu_setperm
        quiet = lambda *_: None                                              # noqa: E731
        lines = lambda n: "".join(f"p{i}\n" for i in range(n)).encode()     # noqa: E731
        at_limit = Space(_load_in_dir(RAW_BASE + "PASSFILE p.txt\n", {"p.txt": lines(4095)}))     # + '' = 4096
        self.assertEqual(len(at_limit.passes), 4096)
        r = gpu_setperm.run_search(at_limit, prize_addr=DUMMY_TARGET, dry_run=True, assume_rate=1e9, log=quiet)
        self.assertEqual(r.status, "DRY_RUN")
        over = Space(_load_in_dir(RAW_BASE + "PASSFILE p.txt\n", {"p.txt": lines(4096)}))
        self.assertEqual(len(over.passes), 4097)
        with self.assertRaises(SpaceError) as cm:
            gpu_setperm.run_search(over, prize_addr=DUMMY_TARGET, dry_run=True, assume_rate=1e9, log=quiet)
        self.assertIn("4096", str(cm.exception))
        with self.assertRaises(gpu_setperm.EngineError):                    # refused before any OpenCL import
            gpu_setperm.Engine(over, [bytes(20)], log=quiet)
        with self.assertRaises(SpaceError):
            Space(_load_in_dir(RAW_BASE + "PASSFILE p.txt\n", {"p.txt": b"p" * 100 + b"\n"}))
        Space(_load_in_dir(RAW_BASE + "PASSFILE p.txt\n", {"p.txt": b"p" * 99 + b"\n"}))


# ---------------------------------------------------------------- the survivor-count check without a filter

def _binomial_problem_before(tested, valid, rate):
    """gpu_setperm.binomial_problem as it was before bip39raw (master 207a235), verbatim but for the constants' import:
    the filtered schemes must get exactly these answers still."""
    from gpu_setperm import BINOM_MIN_TESTED, BINOM_SIGMAS
    if tested < BINOM_MIN_TESTED:
        return None
    mean = tested * rate
    sd = math.sqrt(tested * rate * (1 - rate))
    if abs(valid - mean) <= BINOM_SIGMAS * sd:
        return None
    return (f"{valid:,} valid of {tested:,} tested where {mean:,.1f} +- {sd:,.1f} are expected at a pass rate of "
            f"{rate:.6g}: {(valid - mean) / sd:+.1f} sigma (limit {BINOM_SIGMAS})")


class ExactSurvivorCheck(unittest.TestCase):
    def test_at_rate_one_every_tested_candidate_must_survive_at_any_block_size(self):
        """No filter: survivors == tested exactly, judged at ANY size (below BINOM_MIN_TESTED too) and never a
        ZeroDivisionError (the binomial sigma is 0 there)."""
        from gpu_setperm import BINOM_MIN_TESTED, binomial_problem
        for tested in (0, 1, 2, 37, BINOM_MIN_TESTED - 1, BINOM_MIN_TESTED, 10 ** 6, 4 * 10 ** 9):
            for rate in (1, 1.0):
                self.assertIsNone(binomial_problem(tested, tested, rate), (tested, rate))
                for valid in sorted({0, tested - 1, tested + 1, 3 * tested + 2} - {tested, -1}):
                    msg = binomial_problem(tested, valid, rate)
                    self.assertIsNotNone(msg, (tested, valid, rate))
                    for needle in ("no filter", "exact", f"{valid:,} valid", f"{tested:,} tested"):
                        self.assertIn(needle, msg, (tested, valid))
                    self.assertNotIn("sigma", msg)

    def test_the_filtered_rates_give_exactly_what_they_gave_before(self):
        from gpu_setperm import BINOM_MIN_TESTED, binomial_problem
        n = 0
        for rate in (1 / 16, 1 / 32, 1 / 64, 1 / 128, 1 / 256, 0.5, 0.999):
            for tested in (0, 1, BINOM_MIN_TESTED - 1, BINOM_MIN_TESTED, 12_345, 10 ** 6, 4 * 10 ** 9):
                mean, sd = tested * rate, math.sqrt(tested * rate * (1 - rate))
                for valid in {0, tested, round(mean), int(mean + 7.97 * sd), int(mean - 7.97 * sd),
                              int(mean + 8.03 * sd) + 1, max(0, int(mean - 8.03 * sd) - 1)}:
                    self.assertEqual(binomial_problem(tested, valid, rate), _binomial_problem_before(tested, valid, rate),
                                     (tested, valid, rate))
                    n += 1
        self.assertGreater(n, 200)


# ---------------------------------------------------------------- K1 launch sizing without a filter

def _engine_without_opencl(space, surv_cap):
    """(engine, K1 launches) with the REAL Engine.__init__ - the launch sizers it sets up are what is tested - over
    stand-in kernel wrappers, no OpenCL. The K1 stand-in is exact and, like the real wrapper, keeps at most `cap`
    survivors but always reports the true count; every launch is recorded as (start, count, valid)."""
    import sys
    from types import SimpleNamespace
    from unittest import mock
    import gpu_setperm
    import sp_k1_enum
    launches = []

    class K1:
        def __init__(self, gpu, space, cap=1 << 22, mode=None):
            self.space, self.cap, self.surv, self.last_idx_sum = space, cap, [], None

        def run(self, start, count):
            sp = self.space
            tested = [i for i in range(start, start + count) if not sp.cfg["distinct"] or len(set(sp.decode(i))) == sp.n]
            surv = [i for i in tested if sp.candidate_ok(i)]
            launches.append((start, count, len(surv)))
            self.surv, self.last_idx_sum = surv[:self.cap], sp_k1_enum.expected_idx_sum(start, count)
            return count, len(tested), len(surv)

    class K2:
        def __init__(self, gpu, space, max_threads=1 << 20):
            self.max_threads, self.seeds, self.last_slice = max_threads, None, None

        def launch(self, surv, off, nsurv):
            self.last_slice = (int(off), int(nsurv))

    class K3:
        def __init__(self, gpu, space, targets, hit_cap=256):
            self.last_slice = None

        def run(self, seeds, surv, off, nsurv, npass):
            self.last_slice = (int(off), int(nsurv))
            return []
    fakes = {"sp_gpu": SimpleNamespace(Gpu=None), "sp_k2_pbkdf2": SimpleNamespace(Pbkdf2Kernel=K2),
             "sp_k3_derive": SimpleNamespace(DeriveKernel=K3)}
    with mock.patch.dict(sys.modules, fakes), mock.patch.object(sp_k1_enum, "EnumKernel", K1):
        eng = gpu_setperm.Engine(space, [bytes(20)], gpu=object(), log=lambda *_: None, surv_cap=surv_cap)
    return eng, launches


class RawK1Sizer(unittest.TestCase):
    """K1's sizer grows to K1_MAX_CHUNK (2**25) but the survivor buffer holds surv_cap (2**22 by default): without a
    filter every candidate survives, so every launch above the buffer would overflow and be redone (and a buffer
    under the sizer's 64-candidate floor was an EngineError). A no-filter space caps K1's launches at the buffer."""

    def test_a_no_filter_space_caps_k1_at_the_survivor_buffer_and_the_filtered_ones_keep_their_sizer(self):
        import gpu_setperm
        raw = Space(cfg_from_text(dict(small_config_texts())["O_raw_24_wordlist"]))
        for cap in (1 << 22, gpu_setperm.K1_MAX_CHUNK, 1 << 26, 5000, 1000, 64, 32, 1):
            eng, _ = _engine_without_opencl(raw, cap)
            hi = min(gpu_setperm.K1_MAX_CHUNK, cap)
            self.assertEqual(eng.s1.hi, hi, cap)
            self.assertLessEqual(eng.s1.n, hi, cap)
            self.assertLessEqual(eng.s1.lo, hi, cap)
            self.assertEqual(eng.s1.n, min(1 << 12, hi), cap)
        for name in ("A_bip39_12_r3k1", "D_electrum_12_wordlist", "F_electrum_12_freewords"):
            sp = Space(cfg_from_text(dict(small_config_texts())[name]))
            for cap in (1 << 22, 32):
                eng, _ = _engine_without_opencl(sp, cap)                     # exactly the sizer they always had
                self.assertEqual((eng.s1.n, eng.s1.lo, eng.s1.hi, eng.s1.target, eng.s1.hard),
                                 (1 << 12, 64, gpu_setperm.K1_MAX_CHUNK, 0.3, 0.5), (name, cap))

    def test_a_raw_block_larger_than_the_buffer_is_scanned_exactly_once_and_no_launch_overflows(self):
        raw = Space(cfg_from_text(dict(small_config_texts())["O_raw_24_wordlist"]))     # 4,096 candidates
        for cap in (1000, 64, 32, 1):
            eng, launches = _engine_without_opencl(raw, cap)
            res = eng.scan_block(0, raw.total)
            self.assertEqual((res.scanned, res.tested, res.valid, res.processed), (raw.total,) * 4, cap)
            self.assertEqual([v for _, _, v in launches if v > cap], [], cap)    # no overflow, so nothing was redone
            starts = [s for s, _, _ in launches]
            self.assertEqual(starts, [sum(c for _, c, _ in launches[:i]) for i in range(len(launches))], cap)
            self.assertEqual(sum(c for _, c, _ in launches), raw.total, cap)    # each candidate scanned once
        distinct = Space(cfg_from_text(dict(small_config_texts())["M_raw_12_nochecksum_distinct"]))
        eng, launches = _engine_without_opencl(distinct, 32)
        res = eng.scan_block(0, distinct.total)
        self.assertEqual((res.scanned, res.valid), (distinct.total, sp_ref.ref_survivors(distinct.cfg)[1]))
        self.assertEqual([v for _, _, v in launches if v > 32], [])


# ---------------------------------------------------------------- the real kernels

def _vector_config(vid):
    """(config text, passphrases as typed, paths) whose single candidate is the vector's text; V3 is written as
    SCHEME bip39 + NOCHECKSUM, V5's passphrase in NFC."""
    rows = [(key, path) for (v, key, path) in KAV if v == vid]
    paths = list(dict.fromkeys(path for _, path in rows))
    passes = [V5_PASS] if vid == "V5" else list(dict.fromkeys(k for k, _ in rows if k))
    words = TEXT[vid].split()
    text = make_text("bip39raw", len(words), [(i + 1, w) for i, w in enumerate(words)], [], [], passes=tuple(passes),
                     paths=tuple(paths))
    if vid == "V3":
        text = text.replace("SCHEME bip39raw\n", "SCHEME bip39\nNOCHECKSUM\n")
    return text


@require_gpu
class RawKernels(unittest.TestCase):
    def test_the_vector_file_through_k1_k2_and_k3(self):
        """K1 in mode 0 keeps each vector's text (13 words, upper case, a non-BIP39 token, a bad checksum), K2's seeds
        are setperm_solve.seed_of's, and K3's hash160s are the file's addresses in both key forms."""
        import numpy as np
        from sp_gpu import Gpu
        from sp_k1_enum import EnumKernel
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        from sp_k3_derive import DeriveKernel
        gpu, checked = Gpu.get(), 0
        for vid in ("V0", "V1", "V2", "V3", "V5"):
            sp = Space(cfg_from_text(_vector_config(vid)))
            self.assertEqual(sp.mode, sp_space.MODE_NONE, vid)
            k1 = EnumKernel(gpu, sp)
            self.assertEqual(k1.run(0, 1), (1, 1, 1), vid)
            self.assertEqual([int(x) for x in k1.survivors(1)], [0], vid)
            m = " ".join(sp.decode(0))
            self.assertEqual(m, TEXT[vid], vid)
            seeds = Pbkdf2Kernel(gpu, sp).run_np(np.array([0], dtype=np.uint64))
            for pi, pp in enumerate(sp.passes):
                self.assertEqual(bytes(seeds[pi]), sps.seed_of(m, pp, "bip39raw"), (vid, pi))
            words = np.frombuffer(seeds.tobytes(), dtype=">u8").astype(np.uint64).reshape(len(sp.passes), 8)
            dump = DeriveKernel(gpu, sp, [bytes(20)]).dump(words)
            for (v, key, path), pair in KAV.items():
                if v != vid or key == "NFC":
                    continue
                pi = sp.passes.index(unicodedata.normalize("NFKD", V5_PASS) if key == "NFKD" else key)
                for form, want in enumerate(pair):
                    if want:
                        got = address_of_hash160(bytes(dump[pi, sp.paths_txt.index(path), form]))
                        self.assertEqual(got, want, (vid, key, path, form))
                        checked += 1
        self.assertEqual(checked, 19)                                # V0 2, V1 12, V2 2, V3 2, V5 1

    def test_k2_derives_the_joined_nfkd_text_of_long_non_ascii_tokens(self):
        """PBKDF2's key is the joined text: tokens of 11-16 UTF-8 bytes (Cyrillic, accented Latin that NFKD
        decomposes, 4-byte emoji), case kept, in a FIX alternative, REQUIRE and POOL, two passphrases; mnemonics on
        both sides of 111/112 (one or two SHA-512 blocks) and of 128/129 (above it the HMAC key is pre-hashed)."""
        import numpy as np
        from sp_gpu import Gpu
        from sp_k2_pbkdf2 import Pbkdf2Kernel
        toks = ["\u0447\u0451\u0440\u043d\u044b\u0439",                       # 'чёрный' (NFKD: 16 bytes)
                "\u0414\u0415\u041d\u042c\u0434\u0435\u043d\u044c",           # 'ДЕНЬдень' (16 bytes, case kept)
                "\u00c9\u00c9\u00c9\u00c9\u00c9",                             # 'ÉÉÉÉÉ' (NFKD: 15 bytes)
                "\U0001f600" * 4,                                             # four 4-byte emoji (16 bytes)
                "na\u00efvet\u00e9",                                          # 'naïveté' (NFKD: 11 bytes)
                "\u0401\u0416\u0418\u041a\u0451\u0436"]                       # 'ЁЖИКёж' (NFKD: 16 bytes)
        lens = set()
        for n_fix in (3, 6, 10):
            n = n_fix + 2
            fix = [(1, toks[0] + "|" + toks[1])] + [(s, toks[s % len(toks)]) for s in range(2, n_fix + 1)]
            text = make_text("bip39raw", n, fix, [toks[3]], [" ".join(toks[4:] + ["MOON", "moon"])],
                             passes=("\u0401\u0416", "MOON"))
            sp = Space(cfg_from_text(text))
            self.assertEqual(max(len(sp.tok_bytes[sp.tok_id[unicodedata.normalize("NFKD", t)]]) for t in toks), 16)
            idxs = list(range(sp.total))
            got = Pbkdf2Kernel(Gpu.get(), sp).run_np(np.array(idxs, dtype=np.uint64))
            npass = len(sp.passes)
            self.assertEqual(got.shape, (len(idxs) * npass, 64))
            for si, idx in enumerate(idxs):
                m = " ".join(sp.decode(idx))
                lens.add(len(m.encode("utf-8")))
                for pi, pp in enumerate(sp.passes):
                    self.assertEqual(bytes(got[si * npass + pi]), sps.seed_of(m, pp, "bip39raw"), (n, idx, pi))
        self.assertTrue(min(lens) < 112 and max(lens) > 128, sorted(lens))
        self.assertTrue(any(112 <= x <= 128 for x in lens), sorted(lens))


class CpuSelftest(unittest.TestCase):
    def test_the_selftests_raw_known_answers_are_the_vector_files_compressed_addresses(self):
        want = {k: c for k, (c, _) in KAV.items() if k[0] in ("V0", "V1", "V2", "V3") or k[1] == "NFKD"}
        got = {}
        for vid, text, pp, path, addr in sps.RAW_KNOWN_ANSWERS:
            self.assertEqual(text, TEXT[vid], vid)
            if vid == "V5":
                self.assertEqual(pp, V5_PASS)                                 # typed in NFC: the selftest normalises it
            got[(vid, "NFKD" if vid == "V5" else pp, path)] = addr
        self.assertEqual(got, want)

    def test_setperm_selftest_checks_the_raw_known_answers_and_a_raw_find_and_still_passes(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = sps.selftest(min(4, os.cpu_count() or 1))
        text = out.getvalue()
        self.assertTrue(ok, text)
        lines = text.splitlines()
        self.assertEqual(lines[-1], "SELFTEST PASS")
        for needle in ("bip39raw known answers", "find bip39raw 13w"):
            line = [x for x in lines if needle in x]
            self.assertEqual(len(line), 1, (needle, text))
            self.assertTrue(line[0].endswith(" OK"), line[0])


class HashOnATokenLine(unittest.TestCase):
    """A '#' starts a comment. On a FIX / REQUIRE / POOL / PASS / PASSFILE line it used to cut the rest of the line off
    silently, so a token, a passphrase or a file name holding '#' made a run search a smaller space than its config
    states and still end NO MATCH (independent review of the bip39raw change, 2026-10-01, finding F1: nothing a run
    checks can see it, every control works on the parsed space). Such a line is refused now, in every scheme."""

    RAW3 = "SCHEME bip39raw\nWORDS 3\nFIX 1 MOON\n"
    AB11 = "".join(f"FIX {i} abandon\n" for i in range(1, 12))

    def test_a_hash_on_a_token_or_passphrase_line_is_refused_not_cut_off(self):
        cases = {
            "POOL token": self.RAW3 + "REQUIRE breathe\nPOOL zoo C#\n",                    # was: pool ['zoo', 'C']
            "REQUIRE token": self.RAW3 + "POOL zoo wrong\nREQUIRE #1 breathe\n",           # was: REQUIRE []
            "FIX alternative": "SCHEME bip39raw\nWORDS 3\nFIX 1 MOON|M#N|moon\nREQUIRE breathe\nPOOL zoo\n",   # was: MOON|M
            "PASS, leading": self.RAW3 + "REQUIRE breathe\nPOOL zoo\nPASS #moon\n",       # was: no passphrase at all
            "PASS, inner": self.RAW3 + "REQUIRE breathe\nPOOL zoo\nPASS moon #1\n",       # was: 'moon'
            "lower-case key": self.RAW3 + "REQUIRE breathe\npool zoo c#\n",
            "bip39 POOL": "SCHEME bip39\nWORDS 12\n" + self.AB11 + "POOL @WORDLIST # the list\n",
            "electrum PASS": "SCHEME electrum\nWORDS 12\n" + self.AB11 + "POOL @WORDLIST\nPASS #BLM\n",
            "freewords FIX": "SCHEME electrum\nFREEWORDS\nWORDS 2\nFIX 1 c#\nPOOL zoo\n",
        }
        for name, text in cases.items():
            with self.assertRaises(SystemExit, msg=name) as cm:
                cfg_from_text(text)
            self.assertIn("'#'", str(cm.exception.code), name)

    def test_a_hash_in_a_passfile_name_is_refused_and_never_opens_the_file_named_before_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for name, body in (("list", "wrong-file\n"), ("list#1.txt", "right-file\n")):
                with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                    f.write(body)
            conf = os.path.join(d, "c.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(self.RAW3 + "REQUIRE breathe\nPOOL zoo\nPASSFILE list#1.txt\n")
            with self.assertRaises(SystemExit) as cm:                          # was: read 'list' and searched 'wrong-file'
                sps.load_config(conf)
            self.assertIn("'#'", str(cm.exception.code))

    def test_comment_lines_and_comments_after_the_other_directives_still_parse(self):
        text = ("# a header\n   # an indented comment line\nSCHEME bip39raw   # the scheme\nWORDS 3 # three\n"
                "FIX 1 MOON\nREQUIRE breathe\nPOOL zoo wrong\nPATH m/0/0  # one path\nDISTINCT # no repeats\n\n# end\n")
        cfg = cfg_from_text(text)
        self.assertEqual((cfg["scheme"], cfg["words"], cfg["fix"], cfg["require"], cfg["pool"], cfg["pass"], cfg["paths"],
                          cfg["distinct"]),
                         ("bip39raw", 3, {1: "MOON"}, ["breathe"], ["zoo", "wrong"], [""], ["m/0/0"], True))
        cfg = cfg_from_text("NOCHECKSUM  # the alias\nWORDS 3\nFIX 1 MOON\nREQUIRE breathe\nPOOL zoo\n")
        self.assertEqual(cfg["scheme"], "bip39raw")

    def test_a_passphrase_holding_a_hash_goes_in_a_passfile(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "p.txt"), "w", encoding="utf-8") as f:
                f.write("#BLM\nmoon #1\n")
            conf = os.path.join(d, "c.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(self.RAW3 + "REQUIRE breathe\nPOOL zoo\nPASSFILE p.txt\n")
            self.assertEqual(sps.load_config(conf)["pass"], ["", "#BLM", "moon #1"])

    def test_every_config_of_the_two_new_leads_folders_still_loads(self):
        import glob
        n = 0
        for d in ("newleads-2026-10-01", "newleads-raw-2026-10-01"):
            for conf in sorted(glob.glob(os.path.join(HERE, "templates", d, "*.conf"))):
                Space.load(conf)                                                 # a SystemExit here fails the test
                n += 1
        self.assertGreater(n, 400)


class RawDocs(unittest.TestCase):
    def test_the_readme_and_the_docstrings_document_bip39raw_nochecksum_passfile_and_the_exact_check(self):
        import gpu_setperm
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as f:
            readme = f.read().lower()
        for needle in ("scheme bip39raw", "nochecksum", "passfile", "electrum 3.3.8", "keystore.bip39_to_seed",
                       "nfkd", "case kept", "exact survivor-count check", "survivors == tested", "verbatim",
                       "known_answer_vectors.txt", "kav_independent.py", "min(k1_max_chunk, surv_cap)"):
            self.assertTrue(needle in readme, needle)                          # (not assertIn: it would print the file)
        for needle in ("bip39raw", "nochecksum", "passfile", "keystore.bip39_to_seed", "verbatim"):
            self.assertIn(needle, sps.__doc__.lower(), needle)
        self.assertIn("bip39raw", gpu_setperm.__doc__.lower())


class RawCpuSolver(unittest.TestCase):
    def test_finds_a_13_word_phrase_with_an_upper_case_and_a_non_bip39_token_and_misses_when_the_case_differs(self):
        words = ["zoo", "MOON", "wrong", "breathe", "zoo", "abandon", "Tower", "zoo", "about", "wrong", "zoo", "x", "zoo"]
        m = " ".join(words)
        tgt = address_of_hash160(sps.hash160s_scheme(m, "TREZOR", [parse_path("m/0/0")], "bip39raw")[0])
        free = [1, 3, 6, 9]                                                      # 0-based; MOON and breathe REQUIREd
        fix = "".join(f"FIX {i + 1} {w}\n" for i, w in enumerate(words) if i not in free)
        pool = ["zoo", "abandon", "tower", words[6], words[9]]
        body = (f"WORDS 13\nPASS TREZOR\nPATH {BIP44}\nPATH m/0/0\n{fix}POOL {' '.join(pool)}\n")
        procs = min(4, os.cpu_count() or 1)
        hit = sps.run(cfg_from_text("SCHEME bip39raw\n" + body + "REQUIRE MOON breathe\n"), tgt, procs, quiet=True)
        self.assertEqual(hit, {"mnemonic": m, "passphrase": "TREZOR", "path": "m/0/0", "scheme": "bip39raw"})
        miss = sps.run(cfg_from_text("SCHEME bip39\nNOCHECKSUM\n" + body + "REQUIRE Moon breathe\n"), tgt, procs,
                       quiet=True)                                               # 'Moon' is not 'MOON': another text
        self.assertIsNone(miss)


if __name__ == "__main__":
    unittest.main()
