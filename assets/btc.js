// The try-a-phrase page's derivation: BIP39 and Electrum seeds, BIP32 paths, P2PKH addresses, compared with the prize address.
// It follows harness/setperm_solve.py and sp_ref.py. It holds no signing code and no network code, and it never returns a key.
(function (root) {
  "use strict";

  const TARGET = "1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ";
  const DEFAULT_PATHS = ["m/44'/0'/0'/0/0", "m/44'/0'/0'/0/1", "m/44'/0'/0'/0/10", "m/0'/0/0", "m/0/0", "m/0/1", "m/0/2", "m/1/0"];
  const PATH_RE = /^m(\/\d{1,9}'?){1,8}$/;
  const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  const HARDENED = 0x80000000;

  const P = (1n << 256n) - (1n << 32n) - 977n;
  const N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141n;
  const G = [0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798n,
             0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8n];

  const subtle = root.crypto.subtle;
  const utf8 = (s) => new TextEncoder().encode(s);

  function concat(...parts) {
    const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
    let at = 0;
    for (const p of parts) { out.set(p, at); at += p.length; }
    return out;
  }

  function toBig(bytes) {
    let n = 0n;
    for (const b of bytes) n = (n << 8n) | BigInt(b);
    return n;
  }

  function fromBig(n, len) {
    const out = new Uint8Array(len);
    for (let i = len - 1; i >= 0; i--) { out[i] = Number(n & 255n); n >>= 8n; }
    return out;
  }

  // ------------------------------------------------------------ hashes

  async function sha256(bytes) { return new Uint8Array(await subtle.digest("SHA-256", bytes)); }

  async function hmac512(key, msg) {
    const k = await subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-512" }, false, ["sign"]);
    return new Uint8Array(await subtle.sign("HMAC", k, msg));
  }

  async function pbkdf2(password, salt) {
    const k = await subtle.importKey("raw", password, "PBKDF2", false, ["deriveBits"]);
    return new Uint8Array(await subtle.deriveBits({ name: "PBKDF2", hash: "SHA-512", salt, iterations: 2048 }, k, 512));
  }

  // WebCrypto has no RIPEMD-160, so it is written out here (Dobbertin, Bosselaers, Preneel, 1996).
  const RL = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8,
              3, 10, 14, 4, 9, 15, 8, 1, 2, 7, 0, 6, 13, 11, 5, 12, 1, 9, 11, 10, 0, 8, 12, 4, 13, 3, 7, 15, 14, 5, 6, 2,
              4, 0, 5, 9, 7, 12, 2, 10, 14, 1, 3, 8, 11, 6, 15, 13];
  const RR = [5, 14, 7, 0, 9, 2, 11, 4, 13, 6, 15, 8, 1, 10, 3, 12, 6, 11, 3, 7, 0, 13, 5, 10, 14, 15, 8, 12, 4, 9, 1, 2,
              15, 5, 1, 3, 7, 14, 6, 9, 11, 8, 12, 2, 10, 0, 4, 13, 8, 6, 4, 1, 3, 11, 15, 0, 5, 12, 2, 13, 9, 7, 10, 14,
              12, 15, 10, 4, 1, 5, 8, 7, 6, 2, 13, 14, 0, 3, 9, 11];
  const SL = [11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8, 7, 6, 8, 13, 11, 9, 7, 15, 7, 12, 15, 9, 11, 7, 13, 12,
              11, 13, 6, 7, 14, 9, 13, 15, 14, 8, 13, 6, 5, 12, 7, 5, 11, 12, 14, 15, 14, 15, 9, 8, 9, 14, 5, 6, 8, 6, 5, 12,
              9, 15, 5, 11, 6, 8, 13, 12, 5, 12, 13, 14, 11, 8, 5, 6];
  const SR = [8, 9, 9, 11, 13, 15, 15, 5, 7, 7, 8, 11, 14, 14, 12, 6, 9, 13, 15, 7, 12, 8, 9, 11, 7, 7, 12, 7, 6, 15, 13, 11,
              9, 7, 15, 11, 8, 6, 6, 14, 12, 13, 5, 14, 13, 13, 7, 5, 15, 5, 8, 11, 14, 14, 6, 14, 6, 9, 12, 9, 12, 5, 15, 8,
              8, 5, 12, 9, 12, 5, 14, 6, 8, 13, 6, 5, 15, 13, 11, 11];
  const KL = [0x00000000, 0x5A827999, 0x6ED9EBA1, 0x8F1BBCDC, 0xA953FD4E];
  const KR = [0x50A28BE6, 0x5C4DD124, 0x6D703EF3, 0x7A6D76E9, 0x00000000];

  function rmdF(round, x, y, z) {
    switch (round) {
      case 0: return x ^ y ^ z;
      case 1: return (x & y) | (~x & z);
      case 2: return (x | ~y) ^ z;
      case 3: return (x & z) | (y & ~z);
      default: return x ^ (y | ~z);
    }
  }

  const rol = (x, n) => (x << n) | (x >>> (32 - n));

  function ripemd160(msg) {
    const bitLen = msg.length * 8;
    const padded = new Uint8Array(((msg.length + 8) >> 6) * 64 + 64);
    padded.set(msg);
    padded[msg.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, bitLen >>> 0, true);
    view.setUint32(padded.length - 4, Math.floor(bitLen / 2 ** 32), true);
    let h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476, 0xC3D2E1F0];
    for (let off = 0; off < padded.length; off += 64) {
      const w = [];
      for (let i = 0; i < 16; i++) w.push(view.getUint32(off + i * 4, true));
      let [a, b, c, d, e] = h;
      let [a2, b2, c2, d2, e2] = h;
      for (let j = 0; j < 80; j++) {
        const r = Math.floor(j / 16);
        let t = (a + rmdF(r, b, c, d) + w[RL[j]] + KL[r]) | 0;
        t = (rol(t, SL[j]) + e) | 0;
        a = e; e = d; d = rol(c, 10); c = b; b = t;
        t = (a2 + rmdF(4 - r, b2, c2, d2) + w[RR[j]] + KR[r]) | 0;
        t = (rol(t, SR[j]) + e2) | 0;
        a2 = e2; e2 = d2; d2 = rol(c2, 10); c2 = b2; b2 = t;
      }
      const t = (h[1] + c + d2) | 0;
      h = [t, (h[2] + d + e2) | 0, (h[3] + e + a2) | 0, (h[4] + a + b2) | 0, (h[0] + b + c2) | 0];
    }
    const out = new Uint8Array(20);
    const ov = new DataView(out.buffer);
    h.forEach((v, i) => ov.setUint32(i * 4, v >>> 0, true));
    return out;
  }

  // ------------------------------------------------------------ secp256k1, public keys only

  const mod = (a, m = P) => { const r = a % m; return r < 0n ? r + m : r; };

  function inverse(a, m = P) {
    let [r0, r1, s0, s1] = [mod(a, m), m, 1n, 0n];
    while (r1 !== 0n) {
      const q = r0 / r1;
      [r0, r1] = [r1, r0 - q * r1];
      [s0, s1] = [s1, s0 - q * s1];
    }
    return mod(s0, m);
  }

  // Jacobian points [X, Y, Z]; Z = 0 is the point at infinity.
  function double([x, y, z]) {
    if (y === 0n || z === 0n) return [0n, 1n, 0n];
    const yy = mod(y * y), s = mod(4n * x * yy), m = mod(3n * x * x);
    const nx = mod(m * m - 2n * s);
    return [nx, mod(m * (s - nx) - 8n * yy * yy), mod(2n * y * z)];
  }

  function add(p, q) {
    if (p[2] === 0n) return q;
    if (q[2] === 0n) return p;
    const zz1 = mod(p[2] * p[2]), zz2 = mod(q[2] * q[2]);
    const u1 = mod(p[0] * zz2), u2 = mod(q[0] * zz1);
    const s1 = mod(p[1] * zz2 * q[2]), s2 = mod(q[1] * zz1 * p[2]);
    if (u1 === u2) return s1 === s2 ? double(p) : [0n, 1n, 0n];
    const h = mod(u2 - u1), r = mod(s2 - s1), hh = mod(h * h), hhh = mod(h * hh), v = mod(u1 * hh);
    const nx = mod(r * r - hhh - 2n * v);
    return [nx, mod(r * (v - nx) - s1 * hhh), mod(h * p[2] * q[2])];
  }

  function publicPoint(k) {
    let result = [0n, 1n, 0n], base = [G[0], G[1], 1n];
    for (let n = k; n > 0n; n >>= 1n) {
      if (n & 1n) result = add(result, base);
      base = double(base);
    }
    const zi = inverse(result[2]), zi2 = mod(zi * zi);
    return [mod(result[0] * zi2), mod(result[1] * zi2 * zi)];
  }

  function serialize([x, y], compressed) {
    if (compressed) return concat(Uint8Array.of(y & 1n ? 3 : 2), fromBig(x, 32));
    return concat(Uint8Array.of(4), fromBig(x, 32), fromBig(y, 32));
  }

  // ------------------------------------------------------------ text, as the engine and the wallets read it

  // Python's str.split() whitespace, and unicodedata.combining() from data/combining.js where a page has loaded it.
  const SPACE = /[\t-\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/;
  const MARKS = root.COMBINING ? new RegExp("[" + root.COMBINING + "]", "gu") : /\p{Mn}/gu;
  const words = (s) => s.split(SPACE).filter(Boolean);
  const collapse = (s) => words(s).join(" ");

  // BIP39 and bip39raw: NFKD of the words joined by single spaces, case kept (setperm_solve.raw_token_problem).
  const bip39Text = (s) => collapse(s).normalize("NFKD");

  // Electrum's normalize_text: NFKD, lower case, accents dropped, spaces collapsed (setperm_solve.normalize_pass).
  const electrumText = (s) => collapse(s.normalize("NFKD").toLowerCase().replace(MARKS, ""));

  function normalizePass(pp, scheme) {
    return scheme === "electrum" ? electrumText(pp) : pp.normalize("NFKD");
  }

  async function seedOf(text, pass, scheme) {
    const prefix = scheme === "electrum" ? "electrum" : "mnemonic";
    return pbkdf2(utf8(text), utf8(prefix + normalizePass(pass, scheme)));
  }

  function normalizedPhrase(text, scheme) {
    return scheme === "electrum" ? electrumText(text) : bip39Text(text);
  }

  async function electrumStandard(text) {
    return (await hmac512(utf8("Seed version"), utf8(electrumText(text))))[0] === 0x01;
  }

  // null when the phrase cannot carry a BIP39 checksum (wrong length or a word outside the list), else true or false.
  async function bip39Checksum(text, wordlist) {
    const list = words(bip39Text(text));
    const count = list.length;
    const index = new Map(wordlist.map((w, i) => [w, i]));
    if (![12, 15, 18, 21, 24].includes(count) || !list.every((w) => index.has(w))) return null;
    let v = 0n;
    for (const w of list) v = (v << 11n) | BigInt(index.get(w));
    const cs = BigInt(count / 3);
    const entBytes = (count * 11 - count / 3) / 8;
    const digest = await sha256(fromBig(v >> cs, entBytes));
    return (v & ((1n << cs) - 1n)) === BigInt(digest[0]) >> (8n - cs);
  }

  // ------------------------------------------------------------ BIP32 and addresses

  function parsePath(text) {
    if (!PATH_RE.test(text)) throw new Error("not a path: " + text);
    return text.split("/").slice(1).map((p) => (p.endsWith("'") ? Number(p.slice(0, -1)) + HARDENED : Number(p)));
  }

  async function childKey(node, i) {
    const index = fromBig(BigInt(i), 4);
    const data = i >= HARDENED ? concat(Uint8Array.of(0), fromBig(node.k, 32), index)
                               : concat(serialize(publicPoint(node.k), true), index);
    const I = await hmac512(node.c, data), left = toBig(I.slice(0, 32)), k = mod(left + node.k, N);
    if (left >= N || k === 0n) throw new Error("this path has no valid key");
    return { k, c: I.slice(32) };
  }

  function base58(bytes) {
    let n = toBig(bytes), s = "";
    while (n > 0n) { s = B58[Number(n % 58n)] + s; n /= 58n; }
    for (const b of bytes) { if (b !== 0) break; s = "1" + s; }
    return s;
  }

  async function addressOf(pub) {
    const body = concat(Uint8Array.of(0), ripemd160(await sha256(pub)));
    return base58(concat(body, (await sha256(await sha256(body))).slice(0, 4)));
  }

  // One row per (path, key form): { path, compressed, address, match }. Parents are shared, as in the engine's cache.
  async function addresses(seed, paths, target) {
    const I = await hmac512(utf8("Bitcoin seed"), seed);
    const cache = new Map([["", { k: toBig(I.slice(0, 32)), c: I.slice(32) }]]);
    const rows = [];
    for (const path of paths) {
      const steps = parsePath(path);
      let key = "";
      for (const i of steps) {
        const next = key + "/" + i;
        if (!cache.has(next)) cache.set(next, await childKey(cache.get(key), i));
        key = next;
      }
      const point = publicPoint(cache.get(key).k);
      for (const compressed of [true, false]) {
        const address = await addressOf(serialize(point, compressed));
        rows.push({ path, compressed, address, match: address === target });
      }
    }
    return rows;
  }

  // The page's one call. `wordlist` is the BIP39 English list; `target` is replaced only by the tests' planted answer.
  async function check({ text, pass = "", scheme = "bip39", paths = DEFAULT_PATHS, wordlist = [], target = TARGET }) {
    const phrase = normalizedPhrase(text, scheme);
    if (!phrase) throw new Error("no words");
    const seed = await seedOf(phrase, pass, scheme);
    const rows = await addresses(seed, paths, target);
    const known = new Set(wordlist);
    return {
      scheme, rows, match: rows.some((r) => r.match), count: words(phrase).length,
      unknown: wordlist.length ? words(phrase).filter((w) => !known.has(w)) : [],
      checksum: scheme === "electrum" ? null : await bip39Checksum(text, wordlist),
      electrumStandard: scheme === "electrum" ? await electrumStandard(text) : null,
    };
  }

  const api = { TARGET, DEFAULT_PATHS, PATH_RE, check, normalizePass, electrumText, bip39Text, ripemd160, addressOf };
  root.BTC = api;
  if (typeof module !== "undefined") module.exports = api;
})(globalThis);
