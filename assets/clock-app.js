(function () {
  "use strict";
  const S = window.ClockSlices;
  const D = window.CLOCK_DATA;
  const NS = "http://www.w3.org/2000/svg";
  const [W, H] = D.size;
  const STORE = "btc02-clock-slices-v1";
  const CHANCE_N = 20000;
  const $ = (id) => document.getElementById(id);
  const svg = $("svg");
  const mod = (a, n) => ((a % n) + n) % n;
  const r1 = (v) => Math.round(v * 10) / 10;
  const r2 = (v) => Math.round(v * 100) / 100;

  const painted = D.presets[0];
  const place = { cx: painted.cx, cy: painted.cy, t12: painted.t12, r: painted.r, mirrored: false,
                  rule: "mid_literal", insideOnly: false, edgeDeg: 1.5 };
  const things = [];       // {id, group, name, x, y, note, on}
  D.groups.forEach((g) => g.items.forEach((t) =>
    things.push({ id: g.id + ":" + t.name, group: g.id, name: t.name, x: t.x, y: t.y, note: t.note, on: g.ticked })));
  const view = { x: 0, y: 0, w: W, h: H };
  let selSlice = -1, hotId = null, marking = false, raf = 0, hashTimer = 0, chanceSig = null, last = null;

  const turnOf = (t12) => mod(t12 + 90, 360);
  const t12Of = (turn) => mod(turn - 90, 360);

  function save() {
    try {
      localStorage.setItem(STORE, JSON.stringify({
        on: Object.fromEntries(things.filter((t) => t.group !== "marks").map((t) => [t.id, t.on])),
        marks: things.filter((t) => t.group === "marks").map(({ name, x, y, on }) => ({ name, x, y, on })),
      }));
    } catch (e) { /* storage blocked: nothing to keep */ }
  }

  function load() {
    let s = null;
    try { s = JSON.parse(localStorage.getItem(STORE) || "null"); } catch (e) { s = null; }
    if (!s) return;
    things.forEach((t) => { if (s.on && t.id in s.on) t.on = !!s.on[t.id]; });
    (s.marks || []).forEach((m, i) => addMark(m.name, m.x, m.y, m.on !== false, i));
  }

  function addMark(name, x, y, on, n) {
    things.push({ id: "marks:" + Date.now() + ":" + n, group: "marks", name, x, y, note: "my mark at " + x + ", " + y, on });
  }

  function readHash() {
    const q = new URLSearchParams(location.hash.slice(1));
    const num = (k) => (q.has(k) && isFinite(parseFloat(q.get(k))) ? parseFloat(q.get(k)) : null);
    if (num("cx") !== null) place.cx = num("cx");
    if (num("cy") !== null) place.cy = num("cy");
    if (num("t12") !== null) place.t12 = mod(num("t12"), 360);
    if (num("r") !== null) place.r = Math.max(5, num("r"));
    if (num("edge") !== null) place.edgeDeg = Math.max(0, num("edge"));
    if (S.RULES.some((r) => r.id === q.get("rule"))) place.rule = q.get("rule");
    if (q.has("mir")) place.mirrored = q.get("mir") === "1";
    if (q.has("in")) place.insideOnly = q.get("in") === "1";
  }

  function hashText() {
    return new URLSearchParams({ cx: r1(place.cx), cy: r1(place.cy), t12: r2(place.t12), r: r1(place.r),
      rule: place.rule, mir: place.mirrored ? 1 : 0, in: place.insideOnly ? 1 : 0, edge: place.edgeDeg }).toString();
  }

  function writeHash() {
    clearTimeout(hashTimer);
    hashTimer = setTimeout(() => {
      try { history.replaceState(null, "", "#" + hashText()); } catch (e) {
        try { location.replace("#" + hashText()); } catch (e2) { /* the address stays as it was */ }
      }
    }, 300);
  }

  function el(tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }

  function scale() {                       // picture pixels per screen pixel
    const m = svg.getScreenCTM();
    return m && m.a ? 1 / m.a : 1;
  }

  function at(deg, rad) {
    const a = (deg * Math.PI) / 180;
    return [place.cx + rad * Math.cos(a), place.cy + rad * Math.sin(a)];
  }

  const dialAngle = (d) => (place.mirrored ? place.t12 - 30 * d : place.t12 + 30 * d);

  function farRadius() {
    return Math.max(...[[0, 0], [W, 0], [0, H], [W, H]].map(([x, y]) => Math.hypot(x - place.cx, y - place.cy))) + 50;
  }

  function drawWedges(k, res) {
    const g = $("gWedges");
    g.replaceChildren();
    const n = S.sliceCount(place.rule);
    const w = 12 / n;
    const R = place.insideOnly ? place.r : farRadius();
    const sweep = place.mirrored ? 0 : 1;
    for (let i = 0; i < n; i++) {
      const s = S.sliceStart(i, place.rule);
      const p0 = at(dialAngle(s), R), p1 = at(dialAngle(s + w), R);
      const cls = ["wedge", i % 2 ? "w1" : "w0"];
      if (res.slices[i].length > 1) cls.push("crowded");
      if (i === selSlice) cls.push("sel");
      g.append(el("path", { d: `M${place.cx},${place.cy} L${p0} A${R},${R} 0 0 ${sweep} ${p1} Z`, class: cls.join(" ") }));
    }
    for (let i = 0; i < n; i++) {
      const p = at(dialAngle(S.sliceStart(i, place.rule)), R);
      const line = { x1: place.cx, y1: place.cy, x2: p[0], y2: p[1] };
      g.append(el("line", Object.assign({ class: "edge-halo", "stroke-width": 3 * k }, line)));
      g.append(el("line", Object.assign({ class: "edge-line", "stroke-width": 1.2 * k }, line)));
    }
  }

  function drawRing(k) {
    const g = $("gRing");
    g.replaceChildren();
    const c = { cx: place.cx, cy: place.cy, r: place.r };
    g.append(el("circle", Object.assign({ class: "ring-halo", "stroke-width": 4 * k }, c)));
    g.append(el("circle", Object.assign({ class: "ring", "stroke-width": 1.6 * k }, c)));
    for (let n = 0; n < 12; n++) {        // a short tick at each midpoint
      const a = dialAngle(n + 0.5), p = at(a, place.r - 7 * k), q = at(a, place.r + 7 * k);
      g.append(el("line", { x1: p[0], y1: p[1], x2: q[0], y2: q[1], class: "ring", "stroke-width": 1.6 * k }));
    }
    const fs = Math.min(Math.max(place.r * 0.13, 11 * k), 30 * k);
    for (let n = 1; n <= 12; n++) {
      const p = at(dialAngle(n % 12), place.r);
      g.append(el("text", { x: p[0], y: p[1], class: "numeral", "font-size": fs, "stroke-width": 4 * k }, String(n)));
    }
    if (place.rule === "numeral_nearest") return;   // its slots are the numerals themselves
    const count = S.sliceCount(place.rule), w = 12 / count, lf = fs * (count === 24 ? 0.7 : 0.85);
    for (let i = 0; i < count; i++) {
      const p = at(dialAngle(S.sliceStart(i, place.rule) + w / 2), place.r * 0.68);
      g.append(el("text", { x: p[0], y: p[1], class: "slotlab", "font-size": lf, "stroke-width": 3.5 * k },
        String(S.sliceLabel(i, place.rule))));
    }
  }

  function status(res, i) {
    const s = res.where[i];
    return s < 0 ? "out" : res.slices[s].length === 1 ? "alone" : "crowded";
  }

  function drawThings(k, on, res) {
    const g = $("gThings");
    g.replaceChildren();
    const edge = new Set(res.edge);
    on.forEach((t, i) => {
      const st = status(res, i), hot = t.id === hotId;
      if (edge.has(i)) g.append(el("circle", { cx: t.x, cy: t.y, r: 10 * k, class: "edge-ring", "stroke-width": 1.6 * k }));
      const c = el("circle", { cx: t.x, cy: t.y, r: (hot ? 7 : 5.5) * k, class: "thing " + st + (hot ? " hot" : ""),
                               "stroke-width": (hot ? 3 : 1.5) * k, "data-thing": t.id });
      c.append(el("title", {}, t.name + (t.note ? ": " + t.note : "")));
      g.append(c);
      const left = t.x > W - 110;            // names near the right edge read leftward so they stay on the picture
      g.append(el("text", { x: t.x + (left ? -8 : 8) * k, y: t.y + 4 * k, "text-anchor": left ? "end" : "start",
                            class: "tname" + (st === "out" ? " out" : ""), "font-size": 12 * k, "stroke-width": 3 * k }, t.name));
    });
  }

  function drawHandles(k) {
    const g = $("gHandles");
    g.replaceChildren();
    const [hx, hy] = D.hub, a = 7 * k;      // where the painted clock's hands cross, once the clock leaves it
    if (Math.hypot(place.cx - hx, place.cy - hy) > 4) {
      g.append(el("path", { d: `M${hx - a},${hy} H${hx + a} M${hx},${hy - a} V${hy + a}`, class: "hub-ref", "stroke-width": 1.5 * k }));
      g.append(el("text", { x: hx + 9 * k, y: hy - 7 * k, class: "hub-ref-lab", "font-size": 10 * k, "stroke-width": 2.5 * k }, "painted centre"));
    }
    g.append(el("circle", { cx: place.cx, cy: place.cy, r: place.r, class: "ring-hit", "stroke-width": 14 * k, "data-handle": "size" }));
    const q = at(place.t12, place.r + 10 * k), p = at(place.t12, place.r + 36 * k);
    g.append(el("line", { x1: q[0], y1: q[1], x2: p[0], y2: p[1], class: "stem", "stroke-width": 2 * k }));
    g.append(el("circle", { cx: p[0], cy: p[1], r: 8 * k, class: "handle turn", "stroke-width": 2 * k, "data-handle": "turn" }));
    g.append(el("circle", { cx: place.cx, cy: place.cy, r: 8 * k, class: "handle", "stroke-width": 2 * k, "data-handle": "move" }));
  }

  const plural = (n, one, many) => n + " " + (n === 1 ? one : many);

  function writeFit(on, res) {
    const p = $("fit");
    if (!on.length) { p.textContent = "Tick some things below to see how they fall."; return; }
    const bits = [plural(res.crowded, "slice holds", "slices hold") + " two or more", res.empty + " empty"];
    if (place.insideOnly) bits.push(res.outside + " outside the ring");
    p.innerHTML = "<b>" + res.alone + " of " + on.length + "</b> ticked things sit alone in a slice; " + bits.join(", ") + "." +
      (res.edge.length ? " " + plural(res.edge.length, "thing sits", "things sit") + " near a slice edge." : "");
  }

  function writeSlices(on, res) {
    const body = $("sliceRows");
    body.replaceChildren();
    res.slices.forEach((idx, i) => {
      const tr = document.createElement("tr");
      tr.className = (idx.length === 0 ? "empty" : idx.length === 1 ? "alone" : "crowded") + (i === selSlice ? " sel" : "");
      [String(S.sliceLabel(i, place.rule)), S.sliceName(i, place.rule), idx.map((j) => on[j].name).join(", ") || "none"]
        .forEach((txt) => { const td = document.createElement("td"); td.textContent = txt; tr.append(td); });
      tr.addEventListener("click", () => { selSlice = selSlice === i ? -1 : i; schedule(); });
      body.append(tr);
    });
  }

  const rowOf = new Map();                 // thing id -> {li, box, slot}

  function buildThingList() {
    const box = $("groups");
    box.replaceChildren();
    rowOf.clear();
    const groups = D.groups.map((g) => ({ id: g.id, name: g.name })).concat([{ id: "marks", name: "My marks" }]);
    groups.forEach((g) => {
      const mine = things.filter((t) => t.group === g.id);
      if (g.id === "marks" && !mine.length) return;
      const div = document.createElement("div");
      div.className = "group";
      const head = document.createElement("label");
      const all = document.createElement("input");
      all.type = "checkbox";
      all.dataset.group = g.id;
      all.addEventListener("change", () => { mine.forEach((t) => { t.on = all.checked; }); save(); changed(); });
      head.append(all, document.createTextNode(g.name + " (" + mine.length + ")"));
      const ul = document.createElement("ul");
      ul.className = "items";
      mine.forEach((t) => {
        const li = document.createElement("li");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.addEventListener("change", () => { t.on = cb.checked; save(); changed(); });
        let name;
        if (g.id === "marks") {                // a mark is renamed in place
          name = document.createElement("input");
          name.type = "text";
          name.value = t.name;
          name.addEventListener("change", () => { t.name = name.value.trim() || t.name; name.value = t.name; save(); schedule(); });
        } else {
          name = document.createElement("span");
          name.textContent = t.name;
          name.title = t.note || "";
        }
        const slot = document.createElement("span");
        slot.className = "slot";
        li.append(cb, name, slot);
        if (g.id === "marks") {
          const x = document.createElement("button");
          x.className = "x";
          x.textContent = "×";
          x.title = "Delete this mark";
          x.addEventListener("click", () => { things.splice(things.indexOf(t), 1); save(); buildThingList(); changed(); });
          li.append(x);
        }
        li.addEventListener("mouseenter", () => { hotId = t.id; schedule(); });
        li.addEventListener("mouseleave", () => { if (hotId === t.id) { hotId = null; schedule(); } });
        rowOf.set(t.id, { li, box: cb, slot });
        ul.append(li);
      });
      div.append(head, ul);
      box.append(div);
    });
  }

  function writeThingList(on, res) {
    const idx = new Map(on.map((t, i) => [t.id, i]));
    things.forEach((t) => {
      const row = rowOf.get(t.id);
      if (!row) return;
      row.box.checked = t.on;
      row.li.classList.toggle("hot", t.id === hotId);
      const i = idx.get(t.id);
      if (i === undefined) { row.slot.textContent = ""; return; }
      const s = res.where[i];
      row.slot.textContent = s < 0 ? "outside" : S.sliceLabel(s, place.rule) + " (" + S.sliceName(s, place.rule) + ")";
    });
    document.querySelectorAll("input[data-group]").forEach((all) => {
      const mine = things.filter((t) => t.group === all.dataset.group);
      const n = mine.filter((t) => t.on).length;
      all.checked = n === mine.length && n > 0;
      all.indeterminate = n > 0 && n < mine.length;
    });
  }

  function signature(on) {
    return hashText() + "|" + on.map((t) => t.id).join(",");
  }

  function render() {
    raf = 0;
    const k = scale();
    const on = things.filter((t) => t.on);
    const res = S.fit(on.map((t) => [t.x, t.y]), place);
    last = { on, res };
    drawWedges(k, res);
    drawRing(k);
    drawThings(k, on, res);
    drawHandles(k);
    writeFit(on, res);
    writeSlices(on, res);
    writeThingList(on, res);
    if (chanceSig !== null && chanceSig !== signature(on)) { $("chanceOut").textContent = ""; chanceSig = null; }
    writeHash();
  }

  function schedule() {                    // a hidden or embedded view may get no animation frames: the timer covers it
    if (raf) return;
    raf = 1;
    const run = () => { if (raf) render(); };
    requestAnimationFrame(run);
    setTimeout(run, 50);
  }

  function syncInputs() {
    const put = (id, v) => { if (document.activeElement !== $(id)) $(id).value = v; };
    put("cx", r1(place.cx));
    put("cy", r1(place.cy));
    put("turn", r2(turnOf(place.t12)));
    put("size", r1(place.r));
    put("edgeDeg", place.edgeDeg);
    $("rule").value = place.rule;
    $("mirrored").checked = place.mirrored;
    $("inside").checked = place.insideOnly;
  }

  function changed() {
    syncInputs();
    schedule();
  }

  function tidy() {
    place.cx = r1(place.cx);
    place.cy = r1(place.cy);
    place.t12 = r2(mod(place.t12, 360));
    place.r = Math.max(5, r1(place.r));
  }

  function bindNumber(id, apply) {
    $(id).addEventListener("input", () => {
      const v = parseFloat($(id).value);
      if (!isFinite(v)) return;
      apply(v);
      schedule();
    });
    $(id).addEventListener("change", syncInputs);
  }

  bindNumber("cx", (v) => { place.cx = v; });
  bindNumber("cy", (v) => { place.cy = v; });
  bindNumber("turn", (v) => { place.t12 = t12Of(v); });
  bindNumber("size", (v) => { place.r = Math.max(5, v); });
  bindNumber("edgeDeg", (v) => { place.edgeDeg = Math.max(0, v); });
  S.RULES.forEach((r) => $("rule").append(new Option(r.text, r.id)));
  $("rule").addEventListener("change", () => { place.rule = $("rule").value; selSlice = -1; changed(); });
  $("mirrored").addEventListener("change", () => { place.mirrored = $("mirrored").checked; changed(); });
  $("inside").addEventListener("change", () => { place.insideOnly = $("inside").checked; changed(); });

  D.presets.forEach((p) => {
    const b = document.createElement("button");
    b.textContent = p.name;
    b.addEventListener("click", () => {
      Object.assign(place, { cx: p.cx, cy: p.cy, t12: p.t12, r: p.r, mirrored: p.mirrored });
      changed();
    });
    $("presets").append(b);
  });

  function setStatus(msg) {
    $("status").textContent = msg;
  }

  function copyText(text, what) {
    const fallback = () => {
      const ta = document.createElement("textarea");
      ta.value = text;
      document.body.append(ta);
      ta.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      ta.remove();
      setStatus(ok ? "Copied " + what + "." : "Could not copy; your browser blocked it.");
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => setStatus("Copied " + what + "."), fallback);
    } else fallback();
  }

  function placementText() {
    const { on, res } = last;
    const rule = S.RULES.find((r) => r.id === place.rule).text;
    const lines = [
      "btc-02 clock slices",
      "centre (" + r1(place.cx) + ", " + r1(place.cy) + "); 12 points " + r2(turnOf(place.t12)) +
        " deg clockwise from straight up (record t12 " + r2(place.t12) + "); size " + r1(place.r) + " px",
      "slices: " + rule + "; mirrored " + (place.mirrored ? "yes" : "no") + "; only inside the ring " +
        (place.insideOnly ? "yes" : "no"),
      res.alone + " of " + on.length + " ticked things alone; " + res.crowded + " crowded slices; " + res.empty + " empty",
    ];
    res.slices.forEach((idx, i) => {
      if (idx.length) lines.push(S.sliceLabel(i, place.rule) + " (" + S.sliceName(i, place.rule) + "): " + idx.map((j) => on[j].name).join(", "));
    });
    if (res.outside) lines.push("outside the ring: " + on.filter((t, i) => res.where[i] < 0).map((t) => t.name).join(", "));
    lines.push("link: " + location.href.split("#")[0] + "#" + hashText());
    return lines.join("\n");
  }

  $("copyPlace").addEventListener("click", () => copyText(placementText(), "the placement"));
  $("copyMarks").addEventListener("click", () => {
    const mine = things.filter((t) => t.group === "marks");
    if (!mine.length) { setStatus("No marks yet: use Add a mark, then click the picture."); return; }
    copyText(["name\tx\ty"].concat(mine.map((t) => t.name + "\t" + t.x + "\t" + t.y)).join("\n"), "your marks");
  });

  function setMarking(onOff) {
    marking = onOff;
    $("addMark").classList.toggle("on", marking);
    svg.classList.toggle("marking", marking);
    setStatus(marking ? "Click the picture to place a mark (Esc cancels)." : "");
  }

  $("addMark").addEventListener("click", () => setMarking(!marking));

  $("chance").addEventListener("click", () => {
    const on = things.filter((t) => t.on);
    if (!on.length) { $("chanceOut").textContent = "Tick some things first."; return; }
    $("chanceOut").textContent = "Checking " + CHANCE_N.toLocaleString() + " random placements...";
    setTimeout(() => {
      const res = S.chance(on.map((t) => [t.x, t.y]), place, CHANCE_N, (Math.random() * 4294967296) >>> 0, D.size);
      $("chanceOut").textContent = res.atLeast.toLocaleString() + " of " + res.n.toLocaleString() +
        " random placements (" + (100 * res.share).toFixed(1) + "%) leave at least " + res.current +
        " alone. Random means the centre anywhere on the picture and any turn, with this size and these slices." +
        " You found this spot by looking, so a small share is a reason to look closer, not proof.";
      chanceSig = signature(on);
    }, 30);
  });

  function toPic(e) {
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(svg.getScreenCTM().inverse());
    return [p.x, p.y];
  }

  function applyView() {
    svg.setAttribute("viewBox", [view.x, view.y, view.w, view.h].join(" "));
  }

  function fitView() {
    Object.assign(view, { x: 0, y: 0, w: W, h: H });
    applyView();
    schedule();
  }

  $("fitView").addEventListener("click", fitView);

  let drag = null;
  svg.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const p = toPic(e);
    const h = e.target.closest("[data-handle]");
    const th = e.target.closest("[data-thing]");
    try { svg.setPointerCapture(e.pointerId); } catch (err) { /* a pointer the browser no longer tracks */ }
    if (h) drag = { kind: h.dataset.handle, ox: place.cx - p[0], oy: place.cy - p[1] };
    else drag = { kind: "pan", sx: e.clientX, sy: e.clientY, v0: Object.assign({}, view), moved: false,
                  thing: th ? th.dataset.thing : null };
    e.preventDefault();
  });

  svg.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (drag.kind === "pan") {
      const dx = e.clientX - drag.sx, dy = e.clientY - drag.sy;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      if (drag.moved) {
        const k = scale();
        view.x = drag.v0.x - dx * k;
        view.y = drag.v0.y - dy * k;
        applyView();
        svg.classList.add("panning");
      }
      return;
    }
    const p = toPic(e);
    if (drag.kind === "move") { place.cx = p[0] + drag.ox; place.cy = p[1] + drag.oy; }
    else if (drag.kind === "turn") place.t12 = S.ang([place.cx, place.cy], p);
    else if (drag.kind === "size") place.r = Math.hypot(p[0] - place.cx, p[1] - place.cy);
    tidy();
    changed();
  });

  function endDrag(e) {
    if (drag && drag.kind === "pan" && !drag.moved && e.type === "pointerup") {
      if (marking) {
        const p = toPic(e);
        const n = things.filter((t) => t.group === "marks").length + 1;
        addMark("mark " + n, r1(p[0]), r1(p[1]), true, n);
        save();
        buildThingList();
        setMarking(false);
        setStatus("Added mark " + n + "; rename it in the list under My marks.");
      } else hotId = drag.thing && drag.thing !== hotId ? drag.thing : null;
      schedule();
    }
    drag = null;
    svg.classList.remove("panning");
  }

  svg.addEventListener("pointerup", endDrag);
  svg.addEventListener("pointercancel", endDrag);

  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    const p = toPic(e);
    const w = Math.min(Math.max(view.w * Math.exp(e.deltaY * 0.0015), 60), W * 2.5);
    const z = view.w / w;
    view.x = p[0] - (p[0] - view.x) / z;
    view.y = p[1] - (p[1] - view.y) / z;
    view.w = w;
    view.h = view.h / z;
    applyView();
    schedule();
  }, { passive: false });

  window.addEventListener("keydown", (e) => {
    if (e.target.closest && e.target.closest("input, select, textarea")) return;
    const step = e.shiftKey ? 10 : 1, turn = e.shiftKey ? 5 : 0.5;
    const keys = {
      ArrowLeft: () => { place.cx -= step; }, ArrowRight: () => { place.cx += step; },
      ArrowUp: () => { place.cy -= step; }, ArrowDown: () => { place.cy += step; },
      "[": () => { place.t12 -= turn; }, "{": () => { place.t12 -= turn; },
      "]": () => { place.t12 += turn; }, "}": () => { place.t12 += turn; },
      "-": () => { place.r -= step; }, _: () => { place.r -= step; },
      "=": () => { place.r += step; }, "+": () => { place.r += step; },
    };
    if (e.key === "Escape" && marking) { setMarking(false); return; }
    if (!keys[e.key]) return;
    e.preventDefault();
    keys[e.key]();
    tidy();
    changed();
  });

  window.addEventListener("resize", schedule);

  const pic = $("pic");
  pic.addEventListener("error", () => setStatus("The picture did not load from " + D.picture + "."));
  pic.setAttribute("href", D.picture);
  load();
  readHash();
  buildThingList();
  syncInputs();
  applyView();
  schedule();
})();
