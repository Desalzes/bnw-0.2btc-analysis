(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ClockSlices = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const RULES = [
    { id: "mid_literal", text: "Between numerals: sum of the two, 12 as 12 (13, 3, 5 ... 23), the record's rule" },
    { id: "mid_zero12", text: "Between numerals: sum of the two, 12 as 0" },
    { id: "mid_mod12", text: "Between numerals: sum on the 12-hour dial" },
    { id: "numeral_lower", text: "Between numerals: the numeral just passed" },
    { id: "numeral_upper", text: "Between numerals: the numeral coming up" },
    { id: "numeral_nearest", text: "Around each numeral: the nearest numeral" },
    { id: "mixed", text: "Half hours: a numeral gives its number, a midpoint the sum" },
    { id: "ring24", text: "Half hours: numbered 1 to 24 from 12|1" },
  ];
  const BETWEEN = new Set(["mid_literal", "mid_zero12", "mid_mod12", "numeral_lower", "numeral_upper"]);
  const HALF = new Set(["mixed", "ring24"]);

  const mod = (a, n) => ((a % n) + n) % n;

  function ang(c, p) {
    return mod((Math.atan2(p[1] - c[1], p[0] - c[0]) * 180) / Math.PI, 360);
  }

  function dial(c, p, t12, mirrored) {
    const a = ang(c, p);
    return mod(mirrored ? t12 - a : a - t12, 360) / 30;
  }

  function midSum(lo, rule) {
    lo = lo || 12;
    const hi = (lo % 12) + 1;
    if (rule === "mid_literal") return lo + hi;
    if (rule === "mid_zero12") return (lo % 12) + (hi % 12);
    return mod(lo + hi - 1, 12) + 1;
  }

  function snap(d, rule) {
    const f = d - Math.floor(d);
    if (rule === "numeral_nearest") return { slot: mod(Math.round(d), 12) || 12, margin: Math.abs(f - 0.5) * 30 };
    if (rule === "numeral_lower") return { slot: mod(Math.floor(d), 12) || 12, margin: Math.min(f, 1 - f) * 30 };
    if (rule === "numeral_upper") return { slot: mod(Math.ceil(d - 1e-12), 12) || 12, margin: Math.min(f, 1 - f) * 30 };
    if (BETWEEN.has(rule)) return { slot: midSum(Math.floor(d), rule), margin: Math.min(f, 1 - f) * 30 };
    if (rule === "mixed") {
      const h = mod(Math.round(d * 2), 24);
      const margin = (0.5 - Math.abs(d * 2 - Math.round(d * 2))) * 15;
      return { slot: h % 2 === 0 ? h / 2 || 12 : midSum(Math.floor(h / 2), "mid_literal"), margin };
    }
    if (rule === "ring24") {
      const q = mod(Math.round(2 * d - 1), 24);
      return { slot: q + 1, margin: (0.5 - Math.abs(2 * d - 1 - Math.round(2 * d - 1))) * 15 };
    }
    throw new Error("unknown rule " + rule);
  }

  function sliceCount(rule) {
    return HALF.has(rule) ? 24 : 12;
  }

  function sliceOf(d, rule) {
    if (BETWEEN.has(rule)) return Math.floor(d);
    if (HALF.has(rule)) return mod(Math.round(2 * d), 24);
    return mod(Math.round(d), 12);
  }

  function sliceStart(k, rule) {
    if (BETWEEN.has(rule)) return k;
    if (HALF.has(rule)) return mod(k / 2 - 0.25, 12);
    return mod(k - 0.5, 12);
  }

  function sliceLabel(k, rule) {
    if (rule === "numeral_lower") return k || 12;
    if (rule === "numeral_upper") return (k + 1) % 12 || 12;
    if (BETWEEN.has(rule)) return midSum(k, rule);
    if (rule === "numeral_nearest") return k || 12;
    if (rule === "ring24") return mod(k - 1, 24) + 1;
    return k % 2 === 0 ? k / 2 || 12 : midSum(Math.floor(k / 2), "mid_literal");
  }

  function sliceName(k, rule) {
    const num = (n) => mod(n, 12) || 12;
    if (BETWEEN.has(rule)) return num(k) + "|" + num(k + 1);
    if (HALF.has(rule)) return k % 2 === 0 ? String(num(k / 2)) : num((k - 1) / 2) + "|" + num((k + 1) / 2);
    return String(num(k));
  }

  function fit(things, place) {
    const slices = Array.from({ length: sliceCount(place.rule) }, () => []);
    const where = [];
    const edge = [];
    let outside = 0;
    things.forEach((p, i) => {
      if (place.insideOnly && Math.hypot(p[0] - place.cx, p[1] - place.cy) > place.r) {
        outside++;
        where.push(-1);
        return;
      }
      const d = dial([place.cx, place.cy], p, place.t12, place.mirrored);
      const k = sliceOf(d, place.rule);
      slices[k].push(i);
      where.push(k);
      if (snap(d, place.rule).margin < (place.edgeDeg || 0)) edge.push(i);
    });
    let alone = 0, crowded = 0, empty = 0;
    for (const s of slices) {
      if (s.length === 1) alone++;
      else if (s.length > 1) crowded++;
      else empty++;
    }
    return { slices, where, edge, alone, crowded, empty, outside };
  }

  function capture(things, place) {
    const { slices } = fit(things.map((t) => [t.x, t.y]), place);
    const cones = slices.map((idx, k) => ({
      slot: sliceLabel(k, place.rule),
      name: sliceName(k, place.rule),
      words: [...new Set(idx.map((i) => things[i].name.trim().toLowerCase()))],
    }));
    return { cx: place.cx, cy: place.cy, t12: place.t12, r: place.r, mirrored: !!place.mirrored, rule: place.rule,
             insideOnly: !!place.insideOnly, cones };
  }

  function sliceTable(names, res, rule) {
    return res.slices.map((idx, k) => [String(sliceLabel(k, rule)), sliceName(k, rule), idx.map((i) => names[i]).join(", ") || "none"]);
  }

  function tableText(rows) {
    return ["Slices", "Slot\tBetween\tThings"].concat(rows.map((r) => r.join("\t"))).join("\n");
  }

  function parseTable(text) {
    const rows = text.split(/\r?\n/).map((ln) => ln.trim().match(/^(\d+)\s+(\d+(?:\|\d+)?)\s+(.+)$/)).filter(Boolean);
    const rule = RULES.map((r) => r.id).find((id) => rows.length === sliceCount(id) &&
      rows.every((row, k) => row[1] === String(sliceLabel(k, id)) && row[2] === sliceName(k, id)));
    if (!rule) return null;
    const cones = rows.map((row, k) => ({
      slot: sliceLabel(k, rule),
      name: sliceName(k, rule),
      words: row[3] === "none" ? [] : [...new Set(row[3].split(",").map((w) => w.trim().toLowerCase()).filter(Boolean))],
    }));
    return { rule, cones };
  }

  const csvCell = (v) => { v = String(v); return /[",\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; };
  const round = (v, places) => Math.round(v * 10 ** places) / 10 ** places;

  function rowsCsv(rows) {
    const n = Math.max(0, ...rows.map((r) => r.cones.length));
    const sameRule = rows.every((r) => r.rule === rows[0].rule);
    const head = Array.from({ length: n }, (_, k) =>
      sameRule ? rows[0].cones[k].slot + " (" + rows[0].cones[k].name + ")" : "cone " + (k + 1));
    head.push("picture", "centre_x", "centre_y", "turn", "size", "slices", "mirrored", "only_inside_ring");
    const lines = rows.map((r) => Array.from({ length: n }, (_, k) => (r.cones[k] ? r.cones[k].words.join("|") : ""))
      .concat(r.cx == null ? [r.picture, "", "", "", "", r.rule, "", ""] :    // a row from the clipboard has no clock position
        [r.picture, round(r.cx, 1), round(r.cy, 1), round(mod(r.t12 + 90, 360), 2), round(r.r, 1), r.rule,
         r.mirrored ? "yes" : "no", r.insideOnly ? "yes" : "no"]));
    return [head].concat(lines).map((cells) => cells.map(csvCell).join(",") + "\r\n").join("");
  }

  function rng(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function chance(things, place, n, seed, size) {
    const current = fit(things, place).alone;
    const rand = rng(seed);
    let atLeast = 0;
    for (let i = 0; i < n; i++) {
      const p = Object.assign({}, place, { cx: rand() * size[0], cy: rand() * size[1], t12: rand() * 360 });
      if (fit(things, p).alone >= current) atLeast++;
    }
    return { current, n, atLeast, share: atLeast / n };
  }

  return { RULES, ang, dial, snap, sliceCount, sliceOf, sliceStart, sliceLabel, sliceName, fit, capture, sliceTable, tableText, parseTable, rowsCsv, chance };
});
