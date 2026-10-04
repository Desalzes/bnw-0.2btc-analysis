// Shared register-resident SHA-512 for K1 (Electrum filter) and K2 (PBKDF2).
// The block from `#define R64` through hmac_block64 is copied verbatim from the verified
// prototype scratch/btc02-gpu-orient/engine/bench_opt.cl (file sha256
// c9cc0ee91290f82218cf5627c20314e75eaa93726924c4900262cbd2832b5907; verified against hashlib,
// 426k seeds/s). Only renames: IV0..7 -> SP_IV0..7, K5 -> SP_K5, so this file can be
// compiled together with sha2.cl.
//   64-bit word state, 16-word rolling message schedule, fully unrolled;
//   padding words for the fixed 192-byte HMAC messages are literals.

#define R64(x,n) rotate((ulong)(x), (ulong)(64-(n)))
#define BS0(x) (R64(x,28) ^ R64(x,34) ^ R64(x,39))
#define BS1(x) (R64(x,14) ^ R64(x,18) ^ R64(x,41))
#define SS0(x) (R64(x,1)  ^ R64(x,8)  ^ ((x) >> 7))
#define SS1(x) (R64(x,19) ^ R64(x,61) ^ ((x) >> 6))
#define CHX(x,y,z) bitselect((z),(y),(x))
#define MAJX(x,y,z) bitselect((x),(y),((x)^(z)))

__constant ulong SP_K5[80] = {
 0x428a2f98d728ae22UL,0x7137449123ef65cdUL,0xb5c0fbcfec4d3b2fUL,0xe9b5dba58189dbbcUL,0x3956c25bf348b538UL,
 0x59f111f1b605d019UL,0x923f82a4af194f9bUL,0xab1c5ed5da6d8118UL,0xd807aa98a3030242UL,0x12835b0145706fbeUL,
 0x243185be4ee4b28cUL,0x550c7dc3d5ffb4e2UL,0x72be5d74f27b896fUL,0x80deb1fe3b1696b1UL,0x9bdc06a725c71235UL,
 0xc19bf174cf692694UL,0xe49b69c19ef14ad2UL,0xefbe4786384f25e3UL,0x0fc19dc68b8cd5b5UL,0x240ca1cc77ac9c65UL,
 0x2de92c6f592b0275UL,0x4a7484aa6ea6e483UL,0x5cb0a9dcbd41fbd4UL,0x76f988da831153b5UL,0x983e5152ee66dfabUL,
 0xa831c66d2db43210UL,0xb00327c898fb213fUL,0xbf597fc7beef0ee4UL,0xc6e00bf33da88fc2UL,0xd5a79147930aa725UL,
 0x06ca6351e003826fUL,0x142929670a0e6e70UL,0x27b70a8546d22ffcUL,0x2e1b21385c26c926UL,0x4d2c6dfc5ac42aedUL,
 0x53380d139d95b3dfUL,0x650a73548baf63deUL,0x766a0abb3c77b2a8UL,0x81c2c92e47edaee6UL,0x92722c851482353bUL,
 0xa2bfe8a14cf10364UL,0xa81a664bbc423001UL,0xc24b8b70d0f89791UL,0xc76c51a30654be30UL,0xd192e819d6ef5218UL,
 0xd69906245565a910UL,0xf40e35855771202aUL,0x106aa07032bbd1b8UL,0x19a4c116b8d2d0c8UL,0x1e376c085141ab53UL,
 0x2748774cdf8eeb99UL,0x34b0bcb5e19b48a8UL,0x391c0cb3c5c95a63UL,0x4ed8aa4ae3418acbUL,0x5b9cca4f7763e373UL,
 0x682e6ff3d6b2b8a3UL,0x748f82ee5defb2fcUL,0x78a5636f43172f60UL,0x84c87814a1f0ab72UL,0x8cc702081a6439ecUL,
 0x90befffa23631e28UL,0xa4506cebde82bde9UL,0xbef9a3f7b2c67915UL,0xc67178f2e372532bUL,0xca273eceea26619cUL,
 0xd186b8c721c0c207UL,0xeada7dd6cde0eb1eUL,0xf57d4f7fee6ed178UL,0x06f067aa72176fbaUL,0x0a637dc5a2c898a6UL,
 0x113f9804bef90daeUL,0x1b710b35131c471bUL,0x28db77f523047d84UL,0x32caab7b40c72493UL,0x3c9ebe0a15c9bebcUL,
 0x431d67c49c100d4cUL,0x4cc5d4becb3e42b6UL,0x597f299cfc657e2aUL,0x5fcb6fab3ad6faecUL,0x6c44198c4a475817UL };

#define SP_IV0 0x6a09e667f3bcc908UL
#define SP_IV1 0xbb67ae8584caa73bUL
#define SP_IV2 0x3c6ef372fe94f82bUL
#define SP_IV3 0xa54ff53a5f1d36f1UL
#define SP_IV4 0x510e527fade682d1UL
#define SP_IV5 0x9b05688c2b3e6c1fUL
#define SP_IV6 0x1f83d9abfb41bd6bUL
#define SP_IV7 0x5be0cd19137e2179UL

// st[8] in/out, w[16] clobbered. Everything fully unrolled -> registers.
static inline void sha512_comp(ulong *st, ulong *w) {
  ulong a = st[0], b = st[1], c = st[2], d = st[3], e = st[4], f = st[5], g = st[6], h = st[7];
  #pragma unroll
  for (int i = 0; i < 80; i++) {
    ulong wi;
    if (i < 16) wi = w[i];
    else { wi = w[i & 15] + SS0(w[(i + 1) & 15]) + w[(i + 9) & 15] + SS1(w[(i + 14) & 15]); w[i & 15] = wi; }
    ulong t1 = h + BS1(e) + CHX(e, f, g) + SP_K5[i] + wi;
    ulong t2 = BS0(a) + MAJX(a, b, c);
    h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
  }
  st[0] += a; st[1] += b; st[2] += c; st[3] += d; st[4] += e; st[5] += f; st[6] += g; st[7] += h;
}

// one HMAC block over a 64-byte message (U or inner hash) with the 192-byte-total padding
static inline void hmac_block64(const ulong *pre, const ulong *msg, ulong *out) {
  ulong w[16];
  #pragma unroll
  for (int i = 0; i < 8; i++) w[i] = msg[i];
  w[8] = 0x8000000000000000UL;
  w[9] = 0; w[10] = 0; w[11] = 0; w[12] = 0; w[13] = 0; w[14] = 0;
  w[15] = (128 + 64) * 8;
  #pragma unroll
  for (int i = 0; i < 8; i++) out[i] = pre[i];
  sha512_comp(out, w);
}

// ---- token packing and 1-2 block finish (shared by K1 Electrum filter and K2) ----

// OR one byte into a big-endian packed word array (caller zeroed it).
static inline void sp_put(ulong *m, int pos, uchar b) {
  m[pos >> 3] |= ((ulong)b) << (56 - 8 * (pos & 7));
}

// "t0 t1 ... t(n-1)" packed big-endian into m[0..29] (caller zeroes m[32]). Returns the byte length.
static int sp_pack_tokens(const ushort *tok, int n, __global const uchar *tokb,
                          __global const uchar *tokl, ulong *m) {
  int pos = 0;
  for (int i = 0; i < n; i++) {
    int t = tok[i];
    int l = tokl[t];
    for (int j = 0; j < l; j++) { sp_put(m, pos, tokb[t * SP_TOKW + j]); pos++; }
    if (i < n - 1) { sp_put(m, pos, 32); pos++; }
  }
  return pos;
}

// Finish a SHA-512 whose first `prefix` bytes (0 or 128) are already absorbed into st.
// m[0..31] holds the `len` (<= 239) tail bytes, rest zero. st becomes the digest state.
static void sp_sha512_finish(ulong *st, ulong *m, int len, int prefix) {
  int nb = (len + 17 + 127) / 128;             // 1 block if len <= 111, else 2
  sp_put(m, len, 0x80);
  ulong w[16];
  for (int b = 0; b < nb; b++) {
    for (int i = 0; i < 16; i++) w[i] = m[b * 16 + i];
    if (b == nb - 1) { w[14] = 0; w[15] = (ulong)(prefix + len) * 8; }
    sha512_comp(st, w);
  }
}
