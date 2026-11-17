// Pure config logic for the public 0.2 BTC CPU config generator: validation, space pricing, .conf I/O, and STORE-only ZIP export.
(function (root) {
  "use strict";

  const SCHEMES = ["bip39", "electrum", "bip39raw"];
  const BIP39_LENGTHS = [12, 15, 18, 21, 24];
  const DEFAULT_PATHS = ["m/44'/0'/0'/0/0", "m/44'/0'/0'/0/1", "m/44'/0'/0'/0/10", "m/0'/0/0", "m/0/0"];
  const ELECTRUM_PATHS = ["m/0/0", "m/0/1", "m/0/2", "m/1/0"];
  const PATH_RE = /^m(\/\d{1,9}'?){1,8}$/;
  const TEXT_KEYS = new Set(["FIX", "REQUIRE", "POOL", "PASS", "PASSFILE"]);
  const BROWSER_MAX_RAW = 50000;
  const BROWSER_MAX_DERIVATIONS = 2000;

  const CPU_PRESETS = [
    { id: "laptop4", rate: 2500, label: "Laptop CPU (4-6 cores, ~2,500/s)" },
    { id: "desktop8", rate: 5000, label: "Desktop CPU (6-8 cores, ~5,000/s)" },
    { id: "apple_m", rate: 7000, label: "Apple Silicon M-series (8-12 cores, ~7,000/s)" },
    { id: "desktop16", rate: 10000, label: "Desktop CPU (12-16 cores, ~10,000/s)" },
    { id: "workstation", rate: 18000, label: "Workstation CPU (24+ cores, ~18,000/s)" },
    { id: "custom", rate: 5000, label: "Custom CPU speed" },
  ];

  function sanitizeName(name) {
    const clean = String(name || "").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "");
    return clean || "my_search";
  }

  function unique(arr) {
    return Array.from(new Set(arr));
  }

  function permBig(n, k) {
    if (k < 0 || k > n) return 0n;
    let out = 1n;
    for (let i = 0; i < k; i++) out *= BigInt(n - i);
    return out;
  }

  function powBig(base, exp) {
    if (exp === 0) return 1n;
    if (base === 0) return 0n;
    return BigInt(base) ** BigInt(exp);
  }

  function normalizeState(raw) {
    const scheme = SCHEMES.includes(raw && raw.scheme) ? raw.scheme : "bip39";
    let words = Number.isInteger(raw && raw.words) ? raw.words : 12;
    if (words < 1) words = 1;
    if (words > 24) words = 24;
    const fix = {};
    if (raw && raw.fix && typeof raw.fix === "object") {
      for (const [k, v] of Object.entries(raw.fix)) {
        const slot = Number(k);
        if (!Number.isInteger(slot) || slot < 1 || slot > words) continue;
        const list = Array.isArray(v) ? v.map((w) => String(w).trim()).filter(Boolean) : [];
        if (list.length) fix[slot] = unique(list);
      }
    }
    const require = Array.isArray(raw && raw.require) ? raw.require.map((w) => String(w).trim()).filter(Boolean) : [];
    const poolAll = Boolean(raw && raw.poolAll);
    const pool = unique(Array.isArray(raw && raw.pool) ? raw.pool.map((w) => String(w).trim()).filter(Boolean) : []);
    const passes = Array.isArray(raw && raw.passes) ? raw.passes.map((p) => String(p)) : [];
    const paths = unique(Array.isArray(raw && raw.paths) ? raw.paths.map((p) => String(p).trim()).filter(Boolean) : []);
    return {
      name: sanitizeName(raw && raw.name),
      note: String((raw && raw.note) || "").replace(/[\r\n]+/g, " ").trim(),
      scheme,
      words,
      fix,
      require,
      poolAll,
      pool,
      passes,
      paths,
      distinct: Boolean(raw && raw.distinct),
      freewords: Boolean(raw && raw.freewords),
    };
  }

  function validate(cfg, knownSet) {
    const problems = [];
    const warnings = [];
    const s = normalizeState(cfg);
    if (!SCHEMES.includes(s.scheme)) problems.push({ key: "Unknown scheme {scheme}.", vars: { scheme: s.scheme } });
    if (s.scheme === "bip39" && !BIP39_LENGTHS.includes(s.words)) {
      problems.push({ key: "BIP39 requires 12, 15, 18, 21 or 24 words (got {n}).", vars: { n: s.words } });
    }
    const fixedSlots = Object.keys(s.fix).map(Number);
    const free = s.words - fixedSlots.length;
    if (s.require.length > free) {
      problems.push({ key: "{r} required words do not fit in {f} open slots.", vars: { r: s.require.length, f: free } });
    }
    if (free > s.require.length && !s.poolAll && s.pool.length === 0) {
      problems.push({ key: "Open slots are left after required words, so the filler pool cannot be empty." });
    }
    if (s.paths.length === 0) {
      problems.push({ key: "Select or enter at least one derivation path." });
    } else {
      for (const path of s.paths) {
        if (!PATH_RE.test(path)) {
          problems.push({ key: "Invalid derivation path: {path}.", vars: { path } });
        }
      }
    }
    if (s.freewords && s.scheme !== "electrum") {
      problems.push({ key: "FREEWORDS only applies to Electrum seeds." });
    }
    const allTokens = [];
    for (const slot of fixedSlots) allTokens.push(...s.fix[slot]);
    allTokens.push(...s.require, ...s.pool);
    const hashToken = allTokens.find((t) => t.includes("#"));
    if (hashToken) {
      problems.push({ key: "Words cannot contain '#': {token}.", vars: { token: hashToken } });
    }
    const hashPass = s.passes.find((p) => p.includes("#"));
    if (hashPass !== undefined) {
      problems.push({ key: "Passphrases cannot contain '#' on a PASS line." });
    }
    if (knownSet && (s.scheme === "bip39" || (s.scheme === "electrum" && !s.freewords))) {
      const missing = unique(allTokens.filter((w) => !knownSet.has(w)));
      if (missing.length) {
        const preview = missing.slice(0, 8).join(", ") + (missing.length > 8 ? " ..." : "");
        if (s.scheme === "bip39") {
          problems.push({ key: "Not on the BIP39 list: {words}. Remove them or switch to Electrum (free words) or BIP39 raw.", vars: { words: preview } });
        } else {
          problems.push({ key: "Not on the BIP39 list: {words}. Enable Free words for Electrum.", vars: { words: preview } });
        }
      }
    }
    if (!s.distinct && free > s.require.length && (s.poolAll || s.pool.length > 0)) {
      warnings.push({ key: "Without DISTINCT, a pool slot may repeat a word already used elsewhere in the phrase." });
    }
    if (s.scheme === "electrum" && s.paths.some((p) => p.includes("'"))) {
      warnings.push({ key: "Electrum seeds usually use m/0/0, m/0/1, m/0/2, m/1/0." });
    }
    return { problems, warnings };
  }

  function price(cfg, knownCount, cpuRate) {
    const s = normalizeState(cfg);
    const rate = cpuRate > 0 ? cpuRate : 5000;
    const fixedSlots = Object.keys(s.fix).map(Number);
    const free = Math.max(0, s.words - fixedSlots.length);
    const r = s.require.length;
    const poolCount = s.poolAll ? knownCount : s.pool.length;
    const placements = permBig(free, r);
    let alternatives = 1n;
    for (const slot of fixedSlots) alternatives *= BigInt(s.fix[slot].length);
    const poolExp = Math.max(0, free - r);
    const poolCombos = powBig(poolCount, poolExp);
    const raw = placements * poolCombos * alternatives;
    const divisor = s.scheme === "bip39" ? (1 << Math.floor(s.words / 3)) : s.scheme === "electrum" ? 256 : 1;
    const passCount = unique(["", ...s.passes]).length;
    const valid = Number(raw) / divisor;
    const derivations = valid * passCount;
    const hours = derivations / rate / 3600;
    return {
      free,
      fixed: fixedSlots.length,
      required: r,
      poolSlots: poolExp,
      poolCount,
      placements,
      alternatives,
      raw,
      divisor,
      valid,
      passCount,
      paths: s.paths.length,
      derivations,
      hours,
      rate,
    };
  }

  function formatCount(n) {
    if (typeof n === "bigint") {
      if (n <= 999999999999n) return Number(n).toLocaleString("en-US");
      return Number(n).toExponential(2);
    }
    if (!Number.isFinite(n)) return "> 1e300";
    if (n < 1e12) return Math.round(n).toLocaleString("en-US");
    return n.toExponential(2);
  }

  function formatHours(hours) {
    if (!Number.isFinite(hours) || hours > 365 * 24 * 10000) return { key: "> 10,000 years", vars: {} };
    const seconds = hours * 3600;
    if (seconds < 1) return { key: "under a second", vars: {} };
    if (seconds < 90) return { key: "{n} s", vars: { n: Math.round(seconds) } };
    if (hours < 1.5) return { key: "{n} min", vars: { n: Math.round(seconds / 60) } };
    if (hours < 72) return { key: "{n} h", vars: { n: hours.toFixed(1) } };
    const days = hours / 24;
    if (days < 730) return { key: "{n} days", vars: { n: Math.round(days).toLocaleString("en-US") } };
    return { key: "{n} years", vars: { n: Math.round(days / 365).toLocaleString("en-US") } };
  }

  function renderConf(cfg, stats) {
    const s = normalizeState(cfg);
    const lines = [];
    if (s.note) lines.push("# " + s.note);
    else lines.push("# " + s.name + " (built with 0.2 BTC Config Builder)");
    if (stats) {
      lines.push("# Estimated derivations: " + formatCount(stats.derivations) + " (" + s.scheme + ", " + s.words + " words)");
    }
    lines.push("SCHEME " + s.scheme);
    lines.push("WORDS " + s.words);
    if (s.scheme === "electrum" && s.freewords) lines.push("FREEWORDS");
    if (s.distinct) lines.push("DISTINCT");
    const slots = Object.keys(s.fix).map(Number).sort((a, b) => a - b);
    for (const slot of slots) lines.push("FIX " + slot + " " + s.fix[slot].join("|"));
    if (s.require.length) lines.push("REQUIRE " + s.require.join(" "));
    if (s.poolAll) {
      lines.push("POOL @WORDLIST");
    } else if (s.pool.length) {
      lines.push("POOL " + s.pool.join(" "));
    }
    const extraPasses = unique(s.passes.filter((p) => p !== ""));
    for (const p of extraPasses) lines.push("PASS " + p);
    for (const path of s.paths) lines.push("PATH " + path);
    return lines.join("\n") + "\n";
  }

  function parseConf(text) {
    const out = {
      name: "imported_search",
      note: "",
      scheme: "bip39",
      words: 12,
      fix: {},
      require: [],
      poolAll: false,
      pool: [],
      passes: [],
      paths: [],
      distinct: false,
      freewords: false,
      errors: [],
    };
    let nochecksum = false;
    const seenPass = new Set([""]);
    const lines = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    for (const raw of lines) {
      const trimmed = raw.trim();
      if (!trimmed) continue;
      if (trimmed.startsWith("#")) {
        if (!out.note) out.note = trimmed.replace(/^#+/, "").trim();
        continue;
      }
      const hashIdx = raw.indexOf("#");
      const line = (hashIdx >= 0 ? raw.slice(0, hashIdx) : raw).trim();
      if (!line) continue;
      const sp = line.search(/\s/);
      const key = (sp < 0 ? line : line.slice(0, sp)).toUpperCase();
      const rest = (sp < 0 ? "" : line.slice(sp + 1)).trim();
      if (hashIdx >= 0 && TEXT_KEYS.has(key)) {
        out.errors.push("A '#' on a " + key + " line is refused by the solver.");
        continue;
      }
      if (key === "SCHEME") out.scheme = rest.toLowerCase();
      else if (key === "WORDS") {
        const n = Number(rest);
        if (Number.isInteger(n)) out.words = n;
        else out.errors.push("WORDS must be an integer.");
      } else if (key === "FIX") {
        const parts = rest.split(/\s+/).filter(Boolean);
        const slot = Number(parts[0]);
        if (parts.length === 2 && Number.isInteger(slot) && slot >= 1 && slot <= 24) {
          out.fix[slot] = parts[1].split("|").filter(Boolean);
        } else {
          out.errors.push("Invalid FIX line: " + line);
        }
      } else if (key === "REQUIRE") {
        out.require.push(...rest.split(/\s+/).filter(Boolean));
      } else if (key === "POOL") {
        for (const w of rest.split(/\s+/).filter(Boolean)) {
          if (w === "@WORDLIST") out.poolAll = true;
          else out.pool.push(w);
        }
      } else if (key === "PASS") {
        if (!seenPass.has(rest)) {
          seenPass.add(rest);
          out.passes.push(rest);
        }
      } else if (key === "PASSFILE") {
        out.errors.push("PASSFILE (" + rest + ") is external and cannot be loaded automatically.");
      } else if (key === "PATH") {
        if (rest) out.paths.push(rest);
      } else if (key === "DISTINCT") {
        out.distinct = true;
      } else if (key === "FREEWORDS") {
        out.freewords = true;
      } else if (key === "NOCHECKSUM") {
        nochecksum = true;
      } else {
        out.errors.push("Unsupported keyword: " + key);
      }
    }
    if (nochecksum && out.scheme === "bip39") out.scheme = "bip39raw";
    out.pool = unique(out.pool);
    out.paths = unique(out.paths);
    return out;
  }

  // Uncompressed (STORE) ZIP builder so bundled queues need no external library.
  const CRC_TABLE = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      t[n] = c >>> 0;
    }
    return t;
  })();

  function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  function buildZip(entries) {
    const enc = new TextEncoder();
    const localParts = [];
    const centralParts = [];
    let offset = 0;
    for (const item of entries) {
      const nameBytes = enc.encode(item.name);
      const dataBytes = item.data instanceof Uint8Array ? item.data : enc.encode(String(item.data));
      const crc = crc32(dataBytes);
      const lh = new Uint8Array(30 + nameBytes.length);
      const lv = new DataView(lh.buffer);
      lv.setUint32(0, 0x04034b50, true);
      lv.setUint16(4, 20, true);
      lv.setUint16(6, 0x0800, true);
      lv.setUint16(8, 0, true);
      lv.setUint16(10, 0, true);
      lv.setUint16(12, 0, true);
      lv.setUint32(14, crc, true);
      lv.setUint32(18, dataBytes.length, true);
      lv.setUint32(22, dataBytes.length, true);
      lv.setUint16(26, nameBytes.length, true);
      lv.setUint16(28, 0, true);
      lh.set(nameBytes, 30);
      localParts.push(lh, dataBytes);

      const ch = new Uint8Array(46 + nameBytes.length);
      const cv = new DataView(ch.buffer);
      cv.setUint32(0, 0x02014b50, true);
      cv.setUint16(4, 20, true);
      cv.setUint16(6, 20, true);
      cv.setUint16(8, 0x0800, true);
      cv.setUint16(10, 0, true);
      cv.setUint16(12, 0, true);
      cv.setUint16(14, 0, true);
      cv.setUint32(16, crc, true);
      cv.setUint32(20, dataBytes.length, true);
      cv.setUint32(24, dataBytes.length, true);
      cv.setUint16(28, nameBytes.length, true);
      cv.setUint16(30, 0, true);
      cv.setUint16(32, 0, true);
      cv.setUint16(34, 0, true);
      cv.setUint16(36, 0, true);
      cv.setUint32(38, 0, true);
      cv.setUint32(42, offset, true);
      ch.set(nameBytes, 46);
      centralParts.push(ch);
      offset += lh.length + dataBytes.length;
    }
    const centralSize = centralParts.reduce((s, p) => s + p.length, 0);
    const eocd = new Uint8Array(22);
    const ev = new DataView(eocd.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(4, 0, true);
    ev.setUint16(6, 0, true);
    ev.setUint16(8, entries.length, true);
    ev.setUint16(10, entries.length, true);
    ev.setUint32(12, centralSize, true);
    ev.setUint32(16, offset, true);
    ev.setUint16(20, 0, true);
    const total = offset + centralSize + eocd.length;
    const out = new Uint8Array(total);
    let pos = 0;
    for (const part of localParts) { out.set(part, pos); pos += part.length; }
    for (const part of centralParts) { out.set(part, pos); pos += part.length; }
    out.set(eocd, pos);
    return out;
  }

  function runQueuePy(confFilenames) {
    const listLiteral = JSON.stringify(confFilenames, null, 2);
    return [
      "#!/usr/bin/env python3",
      '"""Run queued CPU configs one after another using setperm_solve.py from bnw-0.2btc-analysis.',
      "",
      "Place this folder inside or beside puzzles/btc-02-picture/harness/ and run:",
      "    python3 run_queue.py --solver /path/to/setperm_solve.py",
      '"""',
      "import argparse",
      "import pathlib",
      "import subprocess",
      "import sys",
      "",
      "QUEUE = " + listLiteral,
      "",
      "def find_solver(given):",
      "    if given:",
      "        return pathlib.Path(given)",
      "    here = pathlib.Path(__file__).resolve().parent",
      "    for candidate in (",
      "        here / 'setperm_solve.py',",
      "        here.parent / 'setperm_solve.py',",
      "        here / 'puzzles' / 'btc-02-picture' / 'harness' / 'setperm_solve.py',",
      "    ):",
      "        if candidate.is_file():",
      "            return candidate",
      "    return None",
      "",
      "def main():",
      "    ap = argparse.ArgumentParser()",
      "    ap.add_argument('--solver', help='Path to setperm_solve.py')",
      "    ap.add_argument('--procs', type=int, help='CPU worker processes')",
      "    args = ap.parse_args()",
      "    solver = find_solver(args.solver)",
      "    if not solver or not solver.is_file():",
      "        print('Could not find setperm_solve.py. Pass --solver /path/to/setperm_solve.py', file=sys.stderr)",
      "        return 1",
      "    here = pathlib.Path(__file__).resolve().parent",
      "    ck_dir = here / 'checkpoints'",
      "    ck_dir.mkdir(exist_ok=True)",
      "    done_file = here / 'completed.txt'",
      "    done = set(done_file.read_text(encoding='utf-8').splitlines()) if done_file.exists() else set()",
      "    for name in QUEUE:",
      "        if name in done:",
      "            print(f'Skipping completed: {name}')",
      "            continue",
      "        conf = here / name",
      "        ck = ck_dir / (name + '.ck')",
      "        cmd = [sys.executable, str(solver), str(conf), '--checkpoint', str(ck)]",
      "        if args.procs:",
      "            cmd += ['--procs', str(args.procs)]",
      "        print('Running:', ' '.join(cmd))",
      "        rc = subprocess.call(cmd)",
      "        if rc == 2:",
      "            print('MATCH FOUND! Check FOUND_setperm.txt and stop.')",
      "            return 2",
      "        if rc != 0:",
      "            print(f'Stopped with exit code {rc} on {name}', file=sys.stderr)",
      "            return rc",
      "        with done_file.open('a', encoding='utf-8') as f:",
      "            f.write(name + '\\n')",
      "    print('All queued configs finished with no match.')",
      "    return 0",
      "",
      "if __name__ == '__main__':",
      "    sys.exit(main())",
      "",
    ].join("\n");
  }

  function canRunInBrowser(stats) {
    if (!stats) return { ok: false, key: "Save as config, too much compute for the browser." };
    if (stats.raw > BigInt(BROWSER_MAX_RAW) || stats.derivations > BROWSER_MAX_DERIVATIONS) {
      return {
        ok: false,
        key: "Save as config, too much compute for the browser (limit: {maxDeriv} derivations or {maxRaw} raw phrases).",
        vars: { maxDeriv: BROWSER_MAX_DERIVATIONS.toLocaleString("en-US"), maxRaw: BROWSER_MAX_RAW.toLocaleString("en-US") },
      };
    }
    return { ok: true };
  }

  function* enumerateRawPhrases(cfg, wordlist, limit) {
    const s = normalizeState(cfg);
    const n = s.words;
    const pool = s.poolAll ? wordlist : s.pool;
    const openSlots = [];
    for (let i = 1; i <= n; i++) {
      if (!s.fix[i] || !s.fix[i].length) openSlots.push(i);
    }
    const fixedSlots = [];
    for (let i = 1; i <= n; i++) {
      if (s.fix[i] && s.fix[i].length) fixedSlots.push(i);
    }
    const req = s.require.slice();
    const arr = new Array(n);
    let yielded = 0;

    function* fillPool(openIdx, usedSet) {
      if (openIdx === openSlots.length) {
        if (s.distinct) {
          const checkSet = new Set(arr);
          if (checkSet.size !== n) return;
        }
        yielded++;
        if (limit && yielded > limit) throw new Error("BROWSER_LIMIT");
        yield arr.join(" ");
        return;
      }
      const slotPos = openSlots[openIdx] - 1;
      if (arr[slotPos] !== undefined) {
        yield* fillPool(openIdx + 1, usedSet);
        return;
      }
      for (const w of pool) {
        if (s.distinct && usedSet.has(w)) continue;
        arr[slotPos] = w;
        if (s.distinct) usedSet.add(w);
        yield* fillPool(openIdx + 1, usedSet);
        if (s.distinct) usedSet.delete(w);
        arr[slotPos] = undefined;
      }
    }

    function* placeReq(reqIdx, usedSlotMask, usedSet) {
      if (reqIdx === req.length) {
        yield* fillPool(0, usedSet);
        return;
      }
      const w = req[reqIdx];
      if (s.distinct && usedSet.has(w)) return;
      for (let i = 0; i < openSlots.length; i++) {
        if (usedSlotMask & (1 << i)) continue;
        const slotPos = openSlots[i] - 1;
        arr[slotPos] = w;
        if (s.distinct) usedSet.add(w);
        yield* placeReq(reqIdx + 1, usedSlotMask | (1 << i), usedSet);
        if (s.distinct) usedSet.delete(w);
        arr[slotPos] = undefined;
      }
    }

    function* placeFixed(fIdx, usedSet) {
      if (fIdx === fixedSlots.length) {
        yield* placeReq(0, 0, usedSet);
        return;
      }
      const slot = fixedSlots[fIdx];
      const slotPos = slot - 1;
      for (const w of s.fix[slot]) {
        if (s.distinct && usedSet.has(w)) continue;
        arr[slotPos] = w;
        if (s.distinct) usedSet.add(w);
        yield* placeFixed(fIdx + 1, usedSet);
        if (s.distinct) usedSet.delete(w);
        arr[slotPos] = undefined;
      }
    }

    yield* placeFixed(0, new Set());
  }

  const api = {
    SCHEMES,
    BIP39_LENGTHS,
    DEFAULT_PATHS,
    ELECTRUM_PATHS,
    PATH_RE,
    CPU_PRESETS,
    BROWSER_MAX_RAW,
    BROWSER_MAX_DERIVATIONS,
    sanitizeName,
    normalizeState,
    validate,
    price,
    canRunInBrowser,
    enumerateRawPhrases,
    formatCount,
    formatHours,
    renderConf,
    parseConf,
    buildZip,
    runQueuePy,
  };
  root.ConfigCore = api;
  if (typeof module !== "undefined") module.exports = api;
})(globalThis);
