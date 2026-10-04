"""GPU tests for the shared kernel library (kernels/sp_decode.cl, kernels/sp_sha512.cl).

The kernels K1/K2 build on these functions. Testing them through tiny probe kernels
gives early, precise failures: every check compares the GPU against sp_space / hashlib.
Promoted from an independent verifier's probe that caught nine mutations the
compile-only Task 2 tests missed."""
import hashlib
import hmac
import random
import struct
import unittest

import numpy as np

import sp_ref
import sp_space
from sp_testutil import cfg_from_text, make_text, require_gpu, small_config_texts
from template_solve import WORDS

PROBE = r"""
__kernel void k_decode(__global const ulong *D, __global const ulong *idxs, __global ushort *out,
                       __global uchar *okv, ulong cnt) {
  size_t g = get_global_id(0); if (g >= cnt) return;
  ushort tok[SP_NMAX]; for (int i = 0; i < SP_NMAX; i++) tok[i] = 0xFFFF;
  sp_decode(idxs[g], D, tok);
  okv[g] = sp_distinct(tok, (int)D[SP_D_N]) ? 1 : 0;
  for (int i = 0; i < SP_NMAX; i++) out[g * SP_NMAX + i] = tok[i];
}
// per candidate: plain SHA-512(mnemonic) and the Electrum HMAC("Seed version", mnemonic)
__kernel void k_mn(__global const ulong *D, __global const ulong *idxs, __global const uchar *tokb,
                   __global const uchar *tokl, __global ulong *out, ulong cnt) {
  size_t g = get_global_id(0); if (g >= cnt) return;
  ushort tok[SP_NMAX]; sp_decode(idxs[g], D, tok);
  int n = (int)D[SP_D_N];
  ulong m[32], m2[32]; for (int i = 0; i < 32; i++) { m[i] = 0; m2[i] = 0; }
  int len = sp_pack_tokens(tok, n, tokb, tokl, m);
  for (int i = 0; i < 32; i++) m2[i] = m[i];
  ulong st[8] = {SP_IV0, SP_IV1, SP_IV2, SP_IV3, SP_IV4, SP_IV5, SP_IV6, SP_IV7};
  sp_sha512_finish(st, m, len, 0);
  ulong ist[8], ost[8], in[8], o[8];
  for (int i = 0; i < 8; i++) { ist[i] = D[SP_O_MID + i]; ost[i] = D[SP_O_MID + 8 + i]; in[i] = ist[i]; }
  sp_sha512_finish(in, m2, len, 128);
  hmac_block64(ost, in, o);
  __global ulong *p = out + g * 17;
  for (int i = 0; i < 8; i++) { p[i] = st[i]; p[8 + i] = o[i]; }
  p[16] = (ulong)len;
}
__kernel void k_finish(__global const ulong *in_state, __global const ulong *msgs, __global const int *lens,
                       __global const int *prefixes, __global ulong *out, ulong cnt) {
  size_t g = get_global_id(0); if (g >= cnt) return;
  ulong st[8], m[32];
  for (int i = 0; i < 8; i++) st[i] = in_state[g * 8 + i];
  for (int i = 0; i < 32; i++) m[i] = msgs[g * 32 + i];
  sp_sha512_finish(st, m, lens[g], prefixes[g]);
  for (int i = 0; i < 8; i++) out[g * 8 + i] = st[i];
}
__kernel void k_hmac64(__global const ulong *ist_g, __global const ulong *ost_g, __global const ulong *msgs,
                       __global ulong *out, ulong cnt) {
  size_t g = get_global_id(0); if (g >= cnt) return;
  ulong ist[8], ost[8], msg[8], a[8], b[8];
  for (int i = 0; i < 8; i++) { ist[i] = ist_g[g * 8 + i]; ost[i] = ost_g[g * 8 + i]; msg[i] = msgs[g * 8 + i]; }
  hmac_block64(ist, msg, a); hmac_block64(ost, a, b);
  for (int i = 0; i < 8; i++) out[g * 8 + i] = b[i];
}
__kernel void k_iv(__global ulong *out) {
  ulong v[8] = {SP_IV0, SP_IV1, SP_IV2, SP_IV3, SP_IV4, SP_IV5, SP_IV6, SP_IV7};
  for (int i = 0; i < 8; i++) out[i] = v[i];
}
"""


def _words_be(b, nwords):
    return np.array(struct.unpack(">%dQ" % nwords, b.ljust(nwords * 8, b"\x00")), dtype=np.uint64)


def _digest(words):
    return b"".join(int(x).to_bytes(8, "big") for x in words)


@require_gpu
class SharedLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        import pyopencl as cl
        from sp_gpu import KDIR, Gpu
        cls.cl, cls.gpu = cl, Gpu.get()
        src = sp_space.cl_defines() + "\n" + "\n".join(
            open(os.path.join(KDIR, f + ".cl"), encoding="utf-8").read() for f in ("sp_sha512", "sp_decode")) + PROBE
        prog = cl.Program(cls.gpu.ctx, src).build(options="-cl-std=CL1.2")
        cls.K = {n: cl.Kernel(prog, n) for n in ("k_decode", "k_mn", "k_finish", "k_hmac64", "k_iv")}

    def _decode(self, sp, idxs):
        g, cnt = self.gpu, len(idxs)
        D, ib = g.ro(sp.descriptor()), g.ro(np.array(idxs, dtype=np.uint64))
        ob, okb = g.rw(cnt * sp_space.SP_NMAX * 2), g.rw(cnt)
        self.K["k_decode"].set_args(D, ib, ob, okb, np.uint64(cnt))
        g.launch(self.K["k_decode"], cnt)
        out = g.read(ob, np.uint16, cnt * sp_space.SP_NMAX).reshape(cnt, sp_space.SP_NMAX)
        return out, g.read(okb, np.uint8, cnt)

    def test_iv_constants(self):
        g = self.gpu
        ob = g.rw(64)
        self.K["k_iv"].set_args(ob)
        g.launch(self.K["k_iv"], 1)
        self.assertEqual([int(x) for x in g.read(ob, np.uint64, 8)], sp_ref.SHA512_IV)

    def test_decode_and_distinct_match_space_on_every_config(self):
        rng = random.Random(4242)
        for name, text in small_config_texts():
            sp = sp_space.Space(cfg_from_text(text))
            idxs = sorted({0, sp.total - 1} | {rng.randrange(sp.total) for _ in range(150)} |
                          {min(sp.total - 1, sp.per_unit * u + off)
                           for u in (0, 1, sp.units - 1) for off in (0, 1, sp.per_unit - 1)})
            out, oks = self._decode(sp, idxs)
            for j, i in enumerate(idxs):
                ids = sp.decode_ids(i)
                self.assertEqual(list(map(int, out[j][:sp.n])), ids, (name, i))
                self.assertTrue(all(int(x) == 0xFFFF for x in out[j][sp.n:]), (name, i, "wrote beyond n"))
                self.assertEqual(bool(oks[j]), len(set(ids)) == sp.n, (name, i, "distinct"))

    def test_full_permutation_space_decodes_without_a_table(self):
        sp = sp_space.Space(cfg_from_text(make_text("bip39", 12, [], WORDS[100:112], [])))
        rng = random.Random(1)
        idxs = [0, 1, sp.total - 1, sp.total - 2, 12345, 123456789, 479001599 - 7] + \
               [rng.randrange(sp.total) for _ in range(200)]
        out, oks = self._decode(sp, idxs)
        for j, i in enumerate(idxs):
            self.assertEqual(list(map(int, out[j][:12])), sp.decode_ids(i), i)
            self.assertTrue(bool(oks[j]), i)

    def test_pack_tokens_sha512_and_seed_version_hmac(self):
        rng = random.Random(7)
        g = self.gpu
        for name, text in small_config_texts():
            sp = sp_space.Space(cfg_from_text(text))
            idxs = sorted({0, sp.total - 1} | {rng.randrange(sp.total) for _ in range(100)})
            cnt = len(idxs)
            tokb, tokl = sp.token_tables()
            D, Tb, Tl = g.ro(sp.descriptor()), g.ro(tokb), g.ro(tokl)
            ib, ob = g.ro(np.array(idxs, dtype=np.uint64)), g.rw(cnt * 17 * 8)
            self.K["k_mn"].set_args(D, ib, Tb, Tl, ob, np.uint64(cnt))
            g.launch(self.K["k_mn"], cnt)
            r = g.read(ob, np.uint64, cnt * 17).reshape(cnt, 17)
            for j, i in enumerate(idxs):
                mn = " ".join(sp.decode(i)).encode()
                self.assertEqual(int(r[j][16]), len(mn), (name, i, "packed length"))
                self.assertEqual(_digest(r[j][:8]), hashlib.sha512(mn).digest(), (name, i, "sha512"))
                self.assertEqual(_digest(r[j][8:16]), hmac.new(b"Seed version", mn, hashlib.sha512).digest(),
                                 (name, i, "HMAC Seed version"))

    def test_sha512_finish_at_block_boundaries_with_and_without_a_prefix(self):
        rng = random.Random(3)
        g = self.gpu
        mids = sp_ref.hmac_midstates(b"Seed version")
        rows = []
        for L in (0, 1, 55, 56, 110, 111, 112, 113, 127, 128, 129, 200, 222, 223, 224, 238, 239):
            msg = bytes(rng.randrange(256) for _ in range(L))
            rows += [(0, msg, sp_ref.SHA512_IV), (128, msg, mids[0])]
        cnt = len(rows)
        bufs = [g.ro(np.array([r[2] for r in rows], dtype=np.uint64).reshape(-1)),
                g.ro(np.concatenate([_words_be(r[1], 32) for r in rows])),
                g.ro(np.array([len(r[1]) for r in rows], dtype=np.int32)),
                g.ro(np.array([r[0] for r in rows], dtype=np.int32))]
        ob = g.rw(cnt * 64)
        self.K["k_finish"].set_args(*bufs, ob, np.uint64(cnt))
        g.launch(self.K["k_finish"], cnt)
        o = g.read(ob, np.uint64, cnt * 8).reshape(cnt, 8)
        for j, (prefix, msg, _) in enumerate(rows):
            want = hashlib.sha512(msg).digest() if prefix == 0 else sp_ref.sha512_finish_py(mids[0], msg, 128)
            self.assertEqual(_digest(o[j]), want, (len(msg), prefix))

    def test_hmac_block64_chain_is_hmac_of_a_64_byte_message(self):
        rng = random.Random(5)
        g = self.gpu
        keys = [b"Seed version", b"k", b"K" * 128, b"L" * 200, b"mnemonic words " * 12]
        rows = [(k, bytes(rng.randrange(256) for _ in range(64))) for k in keys for _ in range(3)]
        cnt = len(rows)
        bufs = [g.ro(np.array([sp_ref.hmac_midstates(k)[0] for k, _ in rows], dtype=np.uint64).reshape(-1)),
                g.ro(np.array([sp_ref.hmac_midstates(k)[1] for k, _ in rows], dtype=np.uint64).reshape(-1)),
                g.ro(np.concatenate([_words_be(m, 8) for _, m in rows]))]
        ob = g.rw(cnt * 64)
        self.K["k_hmac64"].set_args(*bufs, ob, np.uint64(cnt))
        g.launch(self.K["k_hmac64"], cnt)
        o = g.read(ob, np.uint64, cnt * 8).reshape(cnt, 8)
        for j, (k, m) in enumerate(rows):
            self.assertEqual(_digest(o[j]), hmac.new(k, m, hashlib.sha512).digest(), len(k))


if __name__ == "__main__":
    unittest.main()
