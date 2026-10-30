// The picture lab: the views of lab-ops.js on a pan-and-zoom canvas. The original pixels are never changed.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const L = window.LabOps;
  const W = 1600, H = 1200;
  const CIRCLES = [
    { name: "The clock (hub 472.0, 940.2)", cx: 472.0, cy: 940.2, r0: 40, r1: 200 },
    { name: "The Great Seal ring (centre 646.2, 946.9)", cx: 646.2, cy: 946.9, r0: 150, r1: 205 },
    { name: "The whole Great Seal", cx: 646.2, cy: 946.9, r0: 0, r1: 205 },
  ];
  const SLIDERS = {
    radius: { name: "Blur radius", min: 1, max: 40, step: 1, unit: "px" },
    low: { name: "Black point", min: 0, max: 254, step: 1, unit: "" },
    high: { name: "White point", min: 1, max: 255, step: 1, unit: "" },
    dark: { name: "Treat as art below", min: 100, max: 240, step: 1, unit: "" },
    paperSigma: { name: "Blur radius", min: 5, max: 60, step: 1, unit: "px" },
    gain: { name: "Gain", min: 2, max: 30, step: 1, unit: "x" },
    bit: { name: "Bit", min: 0, max: 7, step: 1, unit: "" },
    faintLo: { name: "Black at", min: -40, max: 0, step: 1, unit: "" },
    faintHi: { name: "White at", min: -4, max: 10, step: 0.5, unit: "" },
  };
  const VIEWS = [
    { id: "original", name: "Original", note: "The file as published, 1600 by 1200." },
    { id: "negative", name: "Negative", note: "Every colour inverted." },
    { id: "grey", name: "Greyscale", scalar: 1, note: "Brightness only, with the colour taken out." },
    { id: "red", name: "Red channel", scalar: 1, note: "The red values alone." },
    { id: "green", name: "Green channel", scalar: 1, note: "The green values alone." },
    { id: "blue", name: "Blue channel", scalar: 1, note: "The blue values alone." },
    { id: "saturation", name: "Saturation", scalar: 1, note: "How far each pixel is from grey. Coloured marks stand out from black ink and paper." },
    { id: "redblue", name: "Red minus blue", scalar: 1, note: "Separates warm paint from cool paint." },
    { id: "greenopp", name: "Green against red and blue", scalar: 1, note: "Green less the mean of red and blue. Shows green tints the eye skips." },
    { id: "highpass", name: "High-pass", scalar: 1, controls: ["radius"], note: "Brightness less a blurred copy. Keeps fine detail and drops large shapes." },
    { id: "levels", name: "Levels", controls: ["low", "high"], note: "Maps one brightness range to full black and white. The default is the pale range, 205 to 248." },
    { id: "paper", name: "Paper texture", controls: ["dark", "paperSigma", "gain"], note: "High-pass of the paper alone: dark art is filled in first, so faint shapes between the lines show." },
    { id: "faint", name: "Faint wash recovery", controls: ["faintLo", "faintHi"], note: "Estimates the paper level from the pixels between the ink, then removes the smooth background. Marks a few grey levels below the wash go dark. Slate means no paper was visible there. Takes a few seconds." },
    { id: "bit", name: "Bit plane", controls: ["channel", "bit"], note: "One bit of one channel. Bit 0 is the lowest." },
  ];

  const canvas = $("view"), ctx = canvas.getContext("2d");
  const output = document.createElement("canvas"), outCtx = output.getContext("2d", { willReadFrequently: true });
  const base = document.createElement("canvas");
  base.width = W; base.height = H;
  const baseCtx = base.getContext("2d", { willReadFrequently: true });

  const S = {
    view: "original", stretch: "whole", radius: 8, low: 205, high: 248, dark: 190, paperSigma: 25, gain: 12,
    channel: 0, bit: 0, faintLo: -16, faintHi: 2, flipH: false, flipV: false, rotate: 0, grid: 0, polar: false,
    pcx: CIRCLES[0].cx, pcy: CIRCLES[0].cy, pr0: CIRCLES[0].r0, pr1: CIRCLES[0].r1, x: W / 2, y: H / 2, zoom: 0,
  };
  let rgba = null, field = null, fieldKey = "", invalid = null, lastRgba = null, peeking = false, drawn = 0, busy = false;
  let polarInfo = null, stretchTimer = 0;

  const current = () => VIEWS.find((v) => v.id === S.view);
  const size = () => (S.polar && polarInfo ? [polarInfo.width, polarInfo.height] : [W, H]);
  const dpr = () => window.devicePixelRatio || 1;

  function setStatus(text) { $("status").textContent = text; }

  // ---- the view, worked out from the original pixels

  const nextFrame = () => new Promise((done) => requestAnimationFrame(() => setTimeout(done, 0)));

  function fieldFor(view) {
    const key = view === "highpass" ? "highpass:" + S.radius
      : view === "paper" ? ["paper", S.dark, S.paperSigma, S.gain].join(":")
      : view === "bit" ? ["bit", S.channel, S.bit].join(":") : view;
    if (key === fieldKey) return field;
    const grey = () => L.scalar(rgba, L.VIEWS.grey);
    invalid = null;
    if (view === "highpass") field = L.highPass(grey(), W, H, S.radius);
    else if (view === "levels") field = grey();
    else if (view === "paper") field = L.paperHighPass(grey(), W, H, S.dark, S.paperSigma, S.gain);
    else if (view === "bit") field = L.bitPlane(rgba, S.channel, S.bit);
    else if (view === "faint") {
      const r = L.faintLayer(grey(), W, H);
      field = r.residual;
      invalid = r.invalid;
    } else field = L.scalar(rgba, L.VIEWS[view]);
    fieldKey = key;
    return field;
  }

  function visibleBox() {
    const corners = [[0, 0], [canvas.width, 0], [0, canvas.height], [canvas.width, canvas.height]].map(([a, b]) => toPicture(a, b));
    const x0 = Math.max(0, Math.floor(Math.min(...corners.map((c) => c[0])))), x1 = Math.min(W, Math.ceil(Math.max(...corners.map((c) => c[0]))));
    const y0 = Math.max(0, Math.floor(Math.min(...corners.map((c) => c[1])))), y1 = Math.min(H, Math.ceil(Math.max(...corners.map((c) => c[1]))));
    return x1 - x0 > 1 && y1 - y0 > 1 ? [x0, y0, x1, y1] : [0, 0, W, H];
  }

  function shade(values) {
    const view = current();
    if (S.view === "levels") return L.grey(values, S.low, Math.max(S.high, S.low + 1));
    if (S.view === "paper" || S.view === "bit") return L.grey(values, 0, 255);
    if (S.view === "faint") return L.grey(values, S.faintLo, Math.max(S.faintHi, S.faintLo + 0.5), invalid);
    if (!view.scalar) return null;
    if (S.stretch === "none") return L.grey(values, S.view === "highpass" ? -127.5 : 0, S.view === "highpass" ? 127.5 : 255);
    const part = S.stretch === "screen" && !S.polar ? L.region(values, W, ...visibleBox()) : values;
    const [lo, hi] = L.percentiles(part);
    return L.grey(values, lo, hi);
  }

  function pixels() {
    if (S.view === "original") return rgba;
    if (S.view === "negative") return L.negative(rgba);
    return shade(fieldFor(S.view));
  }

  function paint(data, w, h) {
    if (output.width !== w || output.height !== h) { output.width = w; output.height = h; }
    outCtx.putImageData(new ImageData(new Uint8ClampedArray(data.buffer, data.byteOffset, data.length), w, h), 0, 0);
  }

  async function refresh() {
    if (!rgba || busy) return;
    const slow = S.view === "faint" && !fieldKey.startsWith("faint");
    if (slow) {
      busy = true;
      $("busy").hidden = false;
      await nextFrame();
    }
    const data = pixels();
    busy = false;
    $("busy").hidden = true;
    lastRgba = data;
    if (S.polar) {
      const p = L.polar(data, W, H, { cx: S.pcx, cy: S.pcy, phi0: 0, phi1: 360, r0: S.pr0, r1: S.pr1, sx: 4, sy: 4 });
      polarInfo = p;
      paint(p.rgba, p.width, p.height);
    } else {
      polarInfo = null;
      paint(data, W, H);
    }
    draw();
  }

  // ---- drawing

  function matrix() {
    const z = S.zoom * dpr(), t = S.polar ? 0 : S.rotate * Math.PI / 180;
    const fx = !S.polar && S.flipH ? -1 : 1, fy = !S.polar && S.flipV ? -1 : 1;
    const c = Math.cos(t), s = Math.sin(t);
    const a = c * z * fx, b = s * z * fy, d = -s * z * fx, e = c * z * fy;      // turned first, so a mirror is a mirror on screen
    return { a, b, c: d, d: e, e: canvas.width / 2 - (a * S.x + d * S.y), f: canvas.height / 2 - (b * S.x + e * S.y) };
  }

  function toPicture(px, py) {
    const m = matrix(), det = m.a * m.d - m.b * m.c, u = px - m.e, v = py - m.f;
    return [(m.d * u - m.c * v) / det, (m.a * v - m.b * u) / det];
  }

  function toScreen(x, y) {
    const m = matrix();
    return [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f];
  }

  function gridLines() {
    const [w, h] = size(), out = [];
    if (S.polar) {
      for (let phi = 0; phi <= 360; phi += 10) out.push({ x0: phi * 4, y0: 0, x1: phi * 4, y1: h, label: phi + "°" });
      for (let r = Math.ceil(S.pr0 / 10) * 10; r <= S.pr1; r += 10) out.push({ x0: 0, y0: (r - S.pr0) * 4, x1: w, y1: (r - S.pr0) * 4, label: "r" + r });
      return out;
    }
    if (!S.grid) return out;
    for (let x = 0; x <= W; x += S.grid) out.push({ x0: x, y0: 0, x1: x, y1: H, label: String(x) });
    for (let y = 0; y <= H; y += S.grid) out.push({ x0: 0, y0: y, x1: W, y1: y, label: String(y) });
    return out;
  }

  function draw() {
    if (!rgba) return;
    if (!S.zoom) fitView();
    const m = matrix();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(m.a, m.b, m.c, m.d, m.e, m.f);
    ctx.imageSmoothingEnabled = S.zoom * dpr() < 1;
    ctx.drawImage(peeking && !S.polar ? base : output, 0, 0);
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    const lines = gridLines();
    if (lines.length) {
      ctx.lineWidth = 1;
      ctx.font = 11 * dpr() + "px ui-monospace, monospace";
      const every = Math.ceil(44 * dpr() / ((S.polar ? 40 : S.grid) * S.zoom * dpr()));
      lines.forEach((l, n) => {
        const [ax, ay] = toScreen(l.x0, l.y0), [bx, by] = toScreen(l.x1, l.y1);
        ctx.strokeStyle = "rgba(0, 200, 255, .45)";
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke();
        if (n % every) return;
        const lx = Math.min(canvas.width - 40, Math.max(2, ax + 3)), ly = Math.min(canvas.height - 3, Math.max(12, ay + 12));
        ctx.lineWidth = 3; ctx.strokeStyle = "rgba(0, 0, 0, .75)"; ctx.fillStyle = "#fff";
        ctx.strokeText(l.label, lx, ly); ctx.fillText(l.label, lx, ly);
        ctx.lineWidth = 1;
      });
    }
    drawn += 1;
  }

  function turnedBox(w, h) {
    const t = S.polar ? 0 : S.rotate * Math.PI / 180, c = Math.abs(Math.cos(t)), s = Math.abs(Math.sin(t));
    return [w * c + h * s, w * s + h * c];
  }

  function fitView() {
    const [w, h] = size(), [bw, bh] = turnedBox(w, h);
    S.zoom = Math.min(canvas.width / dpr() / bw, canvas.height / dpr() / bh);
    S.x = w / 2; S.y = h / 2;
  }

  function resize() {
    const r = $("stage").getBoundingClientRect();
    canvas.width = Math.max(1, Math.round(r.width * dpr()));
    canvas.height = Math.max(1, Math.round(r.height * dpr()));
    draw();
  }

  function zoomAt(px, py, factor) {
    const [wx, wy] = toPicture(px, py);
    S.zoom = Math.min(40, Math.max(0.05, S.zoom * factor));
    const [sx, sy] = toScreen(wx, wy);
    const m = matrix(), det = m.a * m.d - m.b * m.c, u = px - sx, v = py - sy;
    S.x -= (m.d * u - m.c * v) / det;
    S.y -= (m.a * v - m.b * u) / det;
    viewMoved();
  }

  function viewMoved() {
    draw();
    if (S.stretch === "screen" && current().scalar) {
      clearTimeout(stretchTimer);
      stretchTimer = setTimeout(refresh, 140);
    }
  }

  // ---- pointer and keys

  const pointers = new Map();
  let pinch = 0;

  function stagePoint(e) {
    const r = canvas.getBoundingClientRect();
    return [(e.clientX - r.left) * dpr(), (e.clientY - r.top) * dpr()];
  }

  function read(e) {
    const [px, py] = stagePoint(e);
    const [x, y] = toPicture(px, py);
    if (S.polar) {
      $("readout").textContent = "bearing " + (x / 4).toFixed(1) + "°  radius " + (S.pr0 + y / 4).toFixed(1) + " px";
      return;
    }
    const ix = Math.floor(x), iy = Math.floor(y);
    if (ix < 0 || iy < 0 || ix >= W || iy >= H) { $("readout").textContent = ""; return; }
    const j = (iy * W + ix) * 4;
    let text = "x " + ix + "  y " + iy + "  rgb " + rgba[j] + " " + rgba[j + 1] + " " + rgba[j + 2];
    if (field && fieldKey.split(":")[0] === S.view) text += "  value " + field[iy * W + ix].toFixed(1);
    $("readout").textContent = text;
  }

  canvas.addEventListener("pointerdown", (e) => {
    canvas.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, stagePoint(e));
    canvas.classList.add("panning");
    pinch = 0;
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!rgba) return;
    read(e);
    const old = pointers.get(e.pointerId);
    if (!old) return;
    const now = stagePoint(e);
    pointers.set(e.pointerId, now);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (pinch) zoomAt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, d / pinch);
      pinch = d;
      return;
    }
    const [ax, ay] = toPicture(old[0], old[1]), [bx, by] = toPicture(now[0], now[1]);
    S.x += ax - bx; S.y += ay - by;
    viewMoved();
  });
  const release = (e) => {
    pointers.delete(e.pointerId);
    pinch = 0;
    if (!pointers.size) canvas.classList.remove("panning");
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    const [px, py] = stagePoint(e);
    zoomAt(px, py, Math.exp(-e.deltaY * (e.deltaMode === 1 ? 16 : 1) * 0.0015));
  }, { passive: false });

  function peek(on) {
    peeking = on;
    draw();
  }
  $("peek").addEventListener("pointerdown", () => peek(true));
  ["pointerup", "pointerleave", "pointercancel"].forEach((n) => $("peek").addEventListener(n, () => peek(false)));
  addEventListener("keydown", (e) => { if ((e.key === "o" || e.key === "O") && !e.repeat && e.target.tagName !== "INPUT") peek(true); });
  addEventListener("keyup", (e) => { if (e.key === "o" || e.key === "O") peek(false); });

  // ---- the panel

  function buildControls() {
    const box = $("controls");
    box.replaceChildren();
    for (const name of current().controls || []) {
      if (name === "channel") {
        const label = document.createElement("label"), span = document.createElement("span"), sel = document.createElement("select");
        label.className = "slider";
        span.textContent = "Channel";
        ["Red", "Green", "Blue", "Alpha"].forEach((t, i) => sel.add(new Option(t, String(i), false, i === S.channel)));
        sel.addEventListener("change", () => { S.channel = Number(sel.value); refresh(); });
        label.append(span, sel);
        box.append(label);
        continue;
      }
      const c = SLIDERS[name], label = document.createElement("label"), span = document.createElement("span");
      const input = document.createElement("input"), show = () => { span.textContent = c.name + ": " + S[name] + (c.unit ? " " + c.unit : ""); };
      label.className = "slider";
      Object.assign(input, { type: "range", min: c.min, max: c.max, step: c.step, value: S[name] });
      input.addEventListener("input", () => { S[name] = Number(input.value); show(); refresh(); });
      show();
      label.append(span, input);
      box.append(label);
    }
    $("stretchRow").hidden = !current().scalar;
    $("viewNote").textContent = current().note;
  }

  function setView(id) {
    S.view = id;
    $("viewSel").value = id;
    buildControls();
    refresh();
  }

  VIEWS.forEach((v) => $("viewSel").add(new Option(v.name, v.id)));
  $("viewSel").addEventListener("change", () => setView($("viewSel").value));
  $("stretch").addEventListener("change", () => { S.stretch = $("stretch").value; refresh(); });
  $("grid").addEventListener("change", () => { S.grid = Number($("grid").value); draw(); });
  $("fit").addEventListener("click", () => { S.zoom = 0; draw(); viewMoved(); });
  $("actual").addEventListener("click", () => { S.zoom = 1; viewMoved(); });
  $("zoomIn").addEventListener("click", () => zoomAt(canvas.width / 2, canvas.height / 2, 1.5));
  $("zoomOut").addEventListener("click", () => zoomAt(canvas.width / 2, canvas.height / 2, 1 / 1.5));
  $("flipH").addEventListener("click", () => { S.flipH = !S.flipH; $("flipH").classList.toggle("on", S.flipH); viewMoved(); });
  $("flipV").addEventListener("click", () => { S.flipV = !S.flipV; $("flipV").classList.toggle("on", S.flipV); viewMoved(); });
  function setRotate(deg) {
    S.rotate = deg;
    $("rotate").value = deg;
    $("rotateOut").textContent = deg;
  }
  $("rotate").addEventListener("input", () => { setRotate(Number($("rotate").value)); viewMoved(); });

  $("jump").add(new Option("Choose a part of the picture", ""));
  window.LAB_REGIONS.forEach((r, i) => $("jump").add(new Option(r.name, String(i))));
  $("jump").addEventListener("change", () => {
    const r = window.LAB_REGIONS[Number($("jump").value)];
    if (!r) return;
    leavePolar();
    const [x0, y0, x1, y1] = r.box, sideways = Math.abs(r.rotate) === 90;
    setRotate(r.rotate);
    S.x = (x0 + x1) / 2; S.y = (y0 + y1) / 2;
    const bw = sideways ? y1 - y0 : x1 - x0, bh = sideways ? x1 - x0 : y1 - y0;
    S.zoom = 0.92 * Math.min(canvas.width / dpr() / bw, canvas.height / dpr() / bh);
    viewMoved();
    if (S.stretch === "screen") refresh();
    $("jump").value = "";
  });

  const syncCircle = () => { ["pcx", "pcy", "pr0", "pr1"].forEach((k) => { $(k).value = S[k]; }); };
  CIRCLES.forEach((c, i) => $("polarPreset").add(new Option(c.name, String(i))));
  $("polarPreset").addEventListener("change", () => {
    const c = CIRCLES[Number($("polarPreset").value)];
    Object.assign(S, { pcx: c.cx, pcy: c.cy, pr0: c.r0, pr1: c.r1 });
    syncCircle();
    if (S.polar) { S.zoom = 0; refresh(); }
  });
  const MAX_RADIUS = 2000;
  function safeCircle() {
    S.pr0 = Math.min(Math.max(0, S.pr0), MAX_RADIUS - 1);
    S.pr1 = Math.min(Math.max(1, S.pr1), MAX_RADIUS);
    return S.pr1 > S.pr0;
  }
  ["pcx", "pcy", "pr0", "pr1"].forEach((k) => $(k).addEventListener("change", () => {
    const v = Number($(k).value);
    if ($(k).value !== "" && Number.isFinite(v)) S[k] = v;
    safeCircle();
    syncCircle();
    if (S.polar) { S.zoom = 0; refresh(); }
  }));
  function leavePolar() {
    if (!S.polar) return;
    S.polar = false;
    S.zoom = 0;
    refresh();
  }
  $("polarOn").addEventListener("click", () => {
    if (!safeCircle()) { setStatus("The outer radius must be larger than the inner radius."); return; }
    setStatus("");
    S.polar = true;
    S.zoom = 0;
    refresh();
  });
  $("polarOff").addEventListener("click", leavePolar);
  $("polarHere").addEventListener("click", () => {
    const [x, y] = S.polar ? [S.pcx, S.pcy] : toPicture(canvas.width / 2, canvas.height / 2);
    S.pcx = Math.round(x * 10) / 10; S.pcy = Math.round(y * 10) / 10;
    syncCircle();
  });

  // The picture as it is turned and mirrored now, at full size. The grid is not saved.
  function orientedCopy() {
    if (S.polar || (!S.rotate && !S.flipH && !S.flipV)) return output;
    const [w, h] = size(), [bw, bh] = turnedBox(w, h), t = S.rotate * Math.PI / 180, copy = document.createElement("canvas");
    copy.width = Math.ceil(bw); copy.height = Math.ceil(bh);
    const c = copy.getContext("2d");
    c.translate(copy.width / 2, copy.height / 2);
    c.scale(S.flipH ? -1 : 1, S.flipV ? -1 : 1);
    c.rotate(t);
    c.imageSmoothingEnabled = false;
    c.drawImage(output, -w / 2, -h / 2);
    return copy;
  }

  $("save").addEventListener("click", () => {
    orientedCopy().toBlob((blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "bnw-" + S.view + (S.polar ? "-polar" : "") + ".png";
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    });
  });

  const KEEP = ["view", "stretch", "radius", "low", "high", "dark", "paperSigma", "gain", "channel", "bit", "faintLo", "faintHi",
                "flipH", "flipV", "rotate", "grid", "polar", "pcx", "pcy", "pr0", "pr1", "x", "y", "zoom"];
  function stateLink() {
    const q = new URLSearchParams();
    KEEP.forEach((k) => q.set(k, typeof S[k] === "number" ? String(Math.round(S[k] * 1000) / 1000) : String(S[k])));
    return location.origin + location.pathname + "#" + q;
  }
  $("link").addEventListener("click", async () => {
    const url = stateLink();
    history.replaceState(null, "", url);
    try { await navigator.clipboard.writeText(url); setStatus("Link copied."); }
    catch (e) { setStatus("The link is now in the address bar."); }
  });
  function readLink() {
    const q = new URLSearchParams(location.hash.slice(1));
    for (const k of KEEP) {
      if (!q.has(k)) continue;
      const v = q.get(k);
      if (typeof S[k] === "boolean") S[k] = v === "true";
      else if (typeof S[k] === "number") { if (Number.isFinite(Number(v))) S[k] = Number(v); }
      else if (k === "view" ? VIEWS.some((x) => x.id === v) : ["whole", "screen", "none"].includes(v)) S[k] = v;
    }
    if (!safeCircle()) S.polar = false;
    $("stretch").value = S.stretch;
    $("grid").value = String(S.grid);
    $("viewSel").value = S.view;
    $("flipH").classList.toggle("on", S.flipH);
    $("flipV").classList.toggle("on", S.flipV);
    setRotate(S.rotate);
    syncCircle();
  }

  // ---- start

  window.LabState = () => ({ view: S.view, zoom: S.zoom, drawn, polar: polarInfo && [polarInfo.width, polarInfo.height], output: lastRgba });
  addEventListener("resize", resize);
  syncCircle();
  readLink();
  buildControls();
  resize();
  const img = new Image();
  img.onload = () => {
    baseCtx.drawImage(img, 0, 0);
    rgba = baseCtx.getImageData(0, 0, W, H).data;
    $("readout").textContent = "Move over the picture to read x, y and colour.";
    refresh();
  };
  img.onerror = () => { $("readout").textContent = "The picture did not load."; };
  img.src = "img/puzzle.png";
})();
