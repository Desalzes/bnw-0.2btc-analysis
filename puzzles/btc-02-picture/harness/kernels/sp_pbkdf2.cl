// K2: PBKDF2-HMAC-SHA512(mnemonic, salt-block, 2048 iterations) per (survivor x passphrase).
// Register-resident (the verified bench_opt.cl prototype, 426k seeds/s): ipad/opad states are
// computed once per candidate, then exactly 2 compressions per iteration.
// Thread t: survivor surv[surv_off + t / n_pass], passphrase t % n_pass.
// A mnemonic longer than 128 bytes is HMAC-keyed by its SHA-512 digest (RFC 2104).

__kernel void sp_pbkdf2(__global const ulong *surv, uint surv_off, uint n_pass, uint nthreads,
                        __global const ulong *D, __global const uchar *tokb, __global const uchar *tokl,
                        __global const ulong *salts, __global ulong *seeds) {
  uint gid = get_global_id(0);
  if (gid >= nthreads) return;
  uint si = gid / n_pass, pi = gid - si * n_pass;
  ushort tok[SP_NMAX];
  sp_decode(surv[surv_off + si], D, tok);
  int n = (int)D[SP_D_N];
  ulong m[32];
  for (int i = 0; i < 32; i++) m[i] = 0;
  int len = sp_pack_tokens(tok, n, tokb, tokl, m);
  ulong kw[16];
  if (len > 128) {
    ulong h[8] = {SP_IV0, SP_IV1, SP_IV2, SP_IV3, SP_IV4, SP_IV5, SP_IV6, SP_IV7};
    sp_sha512_finish(h, m, len, 0);
    for (int i = 0; i < 8; i++) { kw[i] = h[i]; kw[8 + i] = 0; }
  } else {
    for (int i = 0; i < 16; i++) kw[i] = m[i];
  }
  ulong ist[8] = {SP_IV0, SP_IV1, SP_IV2, SP_IV3, SP_IV4, SP_IV5, SP_IV6, SP_IV7};
  ulong ost[8] = {SP_IV0, SP_IV1, SP_IV2, SP_IV3, SP_IV4, SP_IV5, SP_IV6, SP_IV7};
  ulong w[16];
  #pragma unroll
  for (int i = 0; i < 16; i++) w[i] = kw[i] ^ 0x3636363636363636UL;
  sha512_comp(ist, w);
  #pragma unroll
  for (int i = 0; i < 16; i++) w[i] = kw[i] ^ 0x5c5c5c5c5c5c5c5cUL;
  sha512_comp(ost, w);
  // U1 = HMAC(mnemonic, salt || INT(1)); the padded block was built on the host
  #pragma unroll
  for (int i = 0; i < 16; i++) w[i] = salts[pi * 16 + i];
  ulong u[8], t[8], in[8];
  #pragma unroll
  for (int i = 0; i < 8; i++) in[i] = ist[i];
  sha512_comp(in, w);
  hmac_block64(ost, in, u);
  #pragma unroll
  for (int i = 0; i < 8; i++) t[i] = u[i];
  for (int it = 1; it < 2048; it++) {
    hmac_block64(ist, u, in);
    hmac_block64(ost, in, u);
    #pragma unroll
    for (int i = 0; i < 8; i++) t[i] ^= u[i];
  }
  #pragma unroll
  for (int i = 0; i < 8; i++) seeds[(ulong)gid * 8 + i] = t[i];
}
