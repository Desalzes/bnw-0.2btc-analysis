// K3: BIP32 walk over a shared-prefix path table, both public-key forms, target compare.
// Reuses the verified secp256k1/address code (hmac_sha512, hash160, ec pubkey create/serialize).
// Path table row (uint, stride SP_PSTR): [len, common-with-previous, idx0 .. idx7].
//
// NOTE on sha256(): sha2.cl reads its input as whole uint words (and pads by OR-ing 0x80 into the
// last word), so a 33- or 65-byte message must sit in a zero-initialised buffer that is rounded up
// to a multiple of 4 bytes; otherwise the bytes after the message are whatever the stack held.

// Child key derivation on raw arrays. ppub33 is the parent's compressed pubkey (unused for hardened).
static void sp_ckd(uchar *pk, uchar *pcc, uint index, uchar *ppub33, uchar *ck, uchar *ccc) {
  uchar data[37];
  uchar I[64];
  if (index & 0x80000000u) {
    data[0] = 0;
    for (int i = 0; i < 32; i++) data[1 + i] = pk[i];
  } else {
    for (int i = 0; i < 33; i++) data[i] = ppub33[i];
  }
  data[33] = (uchar)(index >> 24); data[34] = (uchar)(index >> 16);
  data[35] = (uchar)(index >> 8);  data[36] = (uchar)index;
  hmac_sha512(pcc, 32, data, 37, I);
  for (int i = 0; i < 32; i++) ck[i] = I[i];
  secp256k1_ec_seckey_tweak_add(ck, pk);            // ck = I_L + parent (mod n)
  for (int i = 0; i < 32; i++) ccc[i] = I[32 + i];
}

static void sp_pub33(uchar *key, uchar *out33) {
  secp256k1_pubkey pk;
  secp256k1_ec_pubkey_create(&pk, key);
  secp256k1_ec_pubkey_serialize(out33, 33, &pk, SECP256K1_EC_COMPRESSED);
}

// out: npaths x 2 x 20 bytes (form 0 = compressed hash160, form 1 = uncompressed).
static void sp_walk(uchar *seed64, __global const uint *paths, uint npaths, uchar *out) {
  uchar key[SP_MAXD + 1][32], cc[SP_MAXD + 1][32], pub[SP_MAXD + 1][33];
  uchar I[64];
  uchar bs[12] = { 0x42, 0x69, 0x74, 0x63, 0x6f, 0x69, 0x6e, 0x20, 0x73, 0x65, 0x65, 0x64 };
  hmac_sha512(bs, 12, seed64, 64, I);
  for (int i = 0; i < 32; i++) { key[0][i] = I[i]; cc[0][i] = I[32 + i]; }
  uint have_pub = 0;                                // bit d: pub[d] is valid for key[d]
  for (uint p = 0; p < npaths; p++) {
    uint len = paths[p * SP_PSTR], common = paths[p * SP_PSTR + 1];
    have_pub &= (1u << (common + 1)) - 1u;          // depths above the shared prefix are recomputed
    for (uint d = common + 1; d <= len; d++) {
      uint index = paths[p * SP_PSTR + 2 + (d - 1)];
      uchar *ppub = pub[0];
      if (!(index & 0x80000000u)) {
        if (!((have_pub >> (d - 1)) & 1u)) { sp_pub33(key[d - 1], pub[d - 1]); have_pub |= 1u << (d - 1); }
        ppub = pub[d - 1];
      }
      sp_ckd(key[d - 1], cc[d - 1], index, ppub, key[d], cc[d]);
    }
    secp256k1_pubkey pk;
    secp256k1_ec_pubkey_create(&pk, key[len]);
    uchar c33[36] __attribute__((aligned(4))) = { 0 };
    uchar u65[68] __attribute__((aligned(4))) = { 0 };
    secp256k1_ec_pubkey_serialize(c33, 33, &pk, SECP256K1_EC_COMPRESSED);
    secp256k1_ec_pubkey_serialize(u65, 65, &pk, SECP256K1_EC_UNCOMPRESSED);
    uchar h1[20], h2[20];
    hash160(c33, 33, h1);
    hash160(u65, 65, h2);
    for (int i = 0; i < 20; i++) { out[(p * 2) * 20 + i] = h1[i]; out[(p * 2 + 1) * 20 + i] = h2[i]; }
  }
}

static void sp_unpack_seed(__global const ulong *seeds, uint gid, uchar *seed64) {
  for (int i = 0; i < 8; i++) {
    ulong v = seeds[(ulong)gid * 8 + i];
    for (int j = 0; j < 8; j++) seed64[i * 8 + j] = (uchar)(v >> (56 - 8 * j));
  }
}

__kernel void sp_derive_dump(__global const ulong *seeds, uint nthreads, __global const uint *paths,
                             uint npaths, __global uchar *out) {
  uint gid = get_global_id(0);
  if (gid >= nthreads) return;
  uchar seed[64];
  uchar h[SP_MAXP * 2 * 20];
  sp_unpack_seed(seeds, gid, seed);
  sp_walk(seed, paths, npaths, h);
  for (uint i = 0; i < npaths * 40; i++) out[(ulong)gid * npaths * 40 + i] = h[i];
}

// hits: uint[1 + hit_cap*8]; hits[0] is the running count (may exceed hit_cap).
// seeds is the slice for this launch: local thread gid reads seeds[gid]; surv is the full
// survivor array, indexed at surv_off + gid / n_pass.
__kernel void sp_derive(__global const ulong *seeds, __global const ulong *surv, uint surv_off,
                        uint n_pass, uint nthreads, __global const uint *paths, uint npaths,
                        __global const uchar *targets, uint ntgt, __global uint *hits, uint hit_cap) {
  uint gid = get_global_id(0);
  if (gid >= nthreads) return;
  uint si = gid / n_pass, pi = gid - si * n_pass;
  uchar seed[64];
  uchar h[SP_MAXP * 2 * 20];
  sp_unpack_seed(seeds, gid, seed);
  sp_walk(seed, paths, npaths, h);
  for (uint j = 0; j < npaths * 2; j++) {
    for (uint t = 0; t < ntgt; t++) {
      bool eq = true;
      for (int i = 0; i < 20; i++) if (h[j * 20 + i] != targets[t * 20 + i]) eq = false;
      if (eq) {
        uint slot = atomic_inc(&hits[0]);
        if (slot < hit_cap) {
          ulong idx = surv[surv_off + si];
          __global uint *rec = hits + 1 + slot * 8;
          rec[0] = (uint)idx; rec[1] = (uint)(idx >> 32); rec[2] = pi; rec[3] = j >> 1;
          rec[4] = j & 1; rec[5] = t; rec[6] = 0; rec[7] = 0;
        }
      }
    }
  }
}
