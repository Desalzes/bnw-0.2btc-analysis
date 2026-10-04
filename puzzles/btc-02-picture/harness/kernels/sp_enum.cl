// K1: enumerate + filter + compact.
//   idx = start + gid -> sp_decode -> DISTINCT -> (BIP39 checksum | Electrum "Seed version") -> survivor.
// Counters (ctr uint[4] = scanned, tested, valid, slot) are accumulated in __local per work-group and added once to
// global (slot is the survivor-store cursor). Survivors are stored only while slot < surv_cap, but `valid` is always
// exact, so the host can detect overflow and redo a smaller chunk without losing anything.
// isum (a separate ulong) is the EXACT 64-bit sum of idx over the threads that ran (per work-group in __local ulong,
// then one 64-bit atomic add to global): `scanned` only counts threads, so a range shifted by one would still report
// scanned == count, and a 32-bit sum is blind to a shift k with count*k = 0 mod 2**32 and to a start truncated to
// 32 bits. The host compares isum with the sum of [start, start+count) mod 2**64.
// Requires (concatenated before this file): common.cl, sha2.cl, sp_sha512.cl, sp_decode.cl.

#pragma OPENCL EXTENSION cl_khr_int64_base_atomics : enable

static bool sp_bip39_ok(const ushort *tok, int n) {
  int cs = n / 3;                       // 4,5,6,7,8 checksum bits
  int ent_bits = n * 11 - cs;           // 128,160,192,224,256
  uint entw[8];                         // uint storage so sha256()'s uint* view is aligned
  uchar *ent = (uchar *)entw;
  for (int i = 0; i < 8; i++) entw[i] = 0;
  for (int i = 0; i < n; i++)
    for (int b = 0; b < 11; b++) {
      int bitpos = i * 11 + b;          // MSB first
      if (bitpos < ent_bits && ((tok[i] >> (10 - b)) & 1)) ent[bitpos >> 3] |= (uchar)(0x80 >> (bitpos & 7));
    }
  uint hw[8];
  sha256(entw, ent_bits / 8, hw);
  uchar *h = (uchar *)hw;               // sha256() leaves the digest in byte order
  return (h[0] >> (8 - cs)) == (tok[n - 1] & ((1 << cs) - 1));
}

static bool sp_electrum_ok(const ushort *tok, int n, __global const ulong *D,
                           __global const uchar *tokb, __global const uchar *tokl) {
  ulong m[32];
  for (int i = 0; i < 32; i++) m[i] = 0;
  int len = sp_pack_tokens(tok, n, tokb, tokl, m);
  ulong st[8], ost[8], out[8];
  for (int i = 0; i < 8; i++) { st[i] = D[SP_O_MID + i]; ost[i] = D[SP_O_MID + 8 + i]; }
  sp_sha512_finish(st, m, len, 128);          // inner hash of HMAC(key="Seed version", mnemonic)
  hmac_block64(ost, st, out);                 // outer hash
  return (uchar)(out[0] >> 56) == 0x01;
}

__kernel void sp_enum(ulong start, ulong count, __global const ulong *D, __global const uchar *tokb,
                      __global const uchar *tokl, __global ulong *surv, uint surv_cap, __global uint *ctr,
                      __global ulong *isum) {
  __local uint lc[3];
  __local ulong lsum;
  int lid = get_local_id(0);
  if (lid == 0) { lc[0] = 0; lc[1] = 0; lc[2] = 0; lsum = 0; }
  barrier(CLK_LOCAL_MEM_FENCE);
  ulong gid = get_global_id(0);
  if (gid < count) {                          // no early return: every thread must reach the barrier
    ulong idx = start + gid;
    int n = (int)D[SP_D_N];
    ushort tok[SP_NMAX];
    atomic_inc(&lc[0]);
    atom_add(&lsum, idx);
    sp_decode(idx, D, tok);
    bool ok = true;
    if (D[SP_D_DISTINCT] != 0 && !sp_distinct(tok, n)) ok = false;
    if (ok) {
      atomic_inc(&lc[1]);
      int mode = (int)D[SP_D_MODE];
      if (mode == 1) ok = sp_bip39_ok(tok, n);
      else if (mode == 2) ok = sp_electrum_ok(tok, n, D, tokb, tokl);
      if (ok) {
        atomic_inc(&lc[2]);
        uint slot = atomic_inc(&ctr[3]);
        if (slot < surv_cap) surv[slot] = idx;
      }
    }
  }
  barrier(CLK_LOCAL_MEM_FENCE);
  if (lid == 0) {
    atomic_add(&ctr[0], lc[0]);
    atomic_add(&ctr[1], lc[1]);
    atomic_add(&ctr[2], lc[2]);
    atom_add(isum, lsum);
  }
}
