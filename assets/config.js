// The CPU config builder page. Everything stays in this browser tab; nothing leaves to the network.
(function () {
  "use strict";
  const C = window.ConfigCore;
  const $ = (id) => document.getElementById(id);
  const wordlist = (window.BIP39_ENGLISH || "").split(" ").filter(Boolean);
  const knownSet = new Set(wordlist);

  const STORE_TRAY = "bnw-cfg-tray-v1";
  const STORE_GROUPS = "bnw-cfg-groups-v1";
  const STORE_QUEUE = "bnw-cfg-queue-v1";
  const STORE_CPU = "bnw-cfg-cpu-v1";

  function loadStore(key, fallback) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (e) {
      return fallback;
    }
  }

  function saveStore(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      /* storage disabled */
    }
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  let tray = Array.isArray(loadStore(STORE_TRAY, [])) ? loadStore(STORE_TRAY, []) : [];
  let groups = Array.isArray(loadStore(STORE_GROUPS, [])) ? loadStore(STORE_GROUPS, []) : [];
  let queue = Array.isArray(loadStore(STORE_QUEUE, [])) ? loadStore(STORE_QUEUE, []) : [];
  const savedCpu = loadStore(STORE_CPU, { preset: "desktop8", customRate: 5000 });

  let state = {
    name: "my_search",
    note: "",
    scheme: "bip39",
    words: 12,
    fix: {},
    require: [],
    poolAll: false,
    pool: [],
    passes: [],
    paths: C.DEFAULT_PATHS.slice(),
    distinct: false,
    freewords: false,
  };

  let cpuPreset = savedCpu && savedCpu.preset ? savedCpu.preset : "desktop8";
  let customRate = savedCpu && Number(savedCpu.customRate) > 0 ? Number(savedCpu.customRate) : 5000;
  let activeTarget = { kind: "slot", slot: 1 };
  let dragPayload = null;
  let browserRunCancel = false;
  let browserRunning = false;

  function currentCpuRate() {
    if (cpuPreset === "custom") return customRate > 0 ? customRate : 5000;
    const found = C.CPU_PRESETS.find((p) => p.id === cpuPreset);
    return found ? found.rate : 5000;
  }

  function setStatus(key, vars) {
    $("actionStatus").textContent = key ? T(key, vars) : "";
  }

  function isKnown(word) {
    return knownSet.has(word);
  }

  function makeWordChip(word, onRemove, opts) {
    const extra = opts && !isKnown(word) && state.scheme === "bip39" ? " chip-nonbip" : "";
    const chip = el("span", "chip" + extra, word);
    chip.draggable = true;
    chip.addEventListener("dragstart", (e) => {
      dragPayload = { kind: "words", words: [word] };
      if (e.dataTransfer) e.dataTransfer.setData("text/plain", word);
    });
    if (opts && opts.onClick) {
      chip.addEventListener("click", (e) => {
        if (e.target.classList.contains("chip-x")) return;
        opts.onClick(word);
      });
    }
    if (onRemove) {
      const x = el("button", "chip-x", "×");
      x.type = "button";
      x.setAttribute("aria-label", T("Remove"));
      x.addEventListener("click", (e) => {
        e.stopPropagation();
        onRemove();
      });
      chip.append(x);
    }
    return chip;
  }

  function applyTokensToTarget(tokens, target, appendToSlot) {
    if (!tokens || !tokens.length) return;
    if (target.kind === "pool") {
      if (tokens.includes("@WORDLIST")) state.poolAll = true;
      const normal = tokens.filter((w) => w !== "@WORDLIST");
      state.pool = Array.from(new Set(state.pool.concat(normal)));
    } else if (target.kind === "require") {
      const normal = tokens.filter((w) => w !== "@WORDLIST");
      if (!normal.length) return setStatus("@WORDLIST belongs in the filler pool.");
      state.require = state.require.concat(normal);
    } else if (target.kind === "slot") {
      const normal = tokens.filter((w) => w !== "@WORDLIST");
      if (!normal.length) return setStatus("@WORDLIST belongs in the filler pool.");
      const prev = state.fix[target.slot] || [];
      state.fix[target.slot] = appendToSlot ? Array.from(new Set(prev.concat(normal))) : Array.from(new Set(normal));
    }
    renderAll();
  }

  function renderTargetHint() {
    if (activeTarget.kind === "slot") {
      $("targetHint").textContent = T("Click a word to place it into slot {n} (Shift-click to add alternative).", { n: activeTarget.slot });
    } else if (activeTarget.kind === "require") {
      $("targetHint").textContent = T("Click a word to add it to Required anywhere.");
    } else {
      $("targetHint").textContent = T("Click a word to add it to the Filler pool.");
    }
  }

  function renderSearch() {
    const q = $("wordSearch").value.trim().toLowerCase();
    const list = $("searchResults");
    list.replaceChildren();
    const matches = q
      ? wordlist.filter((w) => w.startsWith(q)).concat(wordlist.filter((w) => !w.startsWith(q) && w.includes(q)))
      : wordlist.slice(0, 60);
    const shown = matches.slice(0, 80);
    if (!shown.length) {
      list.append(el("li", "note", T("No BIP39 words match {query}.", { query: q })));
      return;
    }
    for (const w of shown) {
      const li = document.createElement("li");
      const btn = el("button", "chip", w);
      btn.type = "button";
      btn.draggable = true;
      btn.addEventListener("dragstart", (e) => {
        dragPayload = { kind: "words", words: [w] };
        if (e.dataTransfer) e.dataTransfer.setData("text/plain", w);
      });
      btn.addEventListener("click", (e) => {
        if (!tray.includes(w)) {
          tray.push(w);
          saveStore(STORE_TRAY, tray);
        }
        applyTokensToTarget([w], activeTarget, e.shiftKey);
      });
      li.append(btn);
      list.append(li);
    }
  }

  function renderTray() {
    const box = $("tray");
    box.replaceChildren();
    box.classList.toggle("empty", tray.length === 0);
    if (!tray.length) {
      box.textContent = T("Click or add words above to collect them here.");
    } else {
      tray.forEach((w, idx) => {
        box.append(makeWordChip(
          w,
          () => {
            tray.splice(idx, 1);
            saveStore(STORE_TRAY, tray);
            renderTray();
          },
          {
            onClick: (word) => applyTokensToTarget([word], activeTarget, true),
          }
        ));
      });
    }
    renderTargetHint();
  }

  function renderGroups() {
    const list = $("groupsList");
    list.replaceChildren();
    if (!groups.length) {
      list.append(el("p", "note", T("No saved groups yet.")));
      return;
    }
    groups.forEach((g, idx) => {
      const row = el("div", "group-row");
      const chip = el("button", "chip chip-group", T("{name} ({n})", { name: g.name, n: g.words.length }));
      chip.type = "button";
      chip.draggable = true;
      chip.addEventListener("dragstart", (e) => {
        dragPayload = { kind: "words", words: g.words.slice() };
        if (e.dataTransfer) e.dataTransfer.setData("text/plain", g.words.join(" "));
      });
      chip.addEventListener("click", () => applyTokensToTarget(g.words, activeTarget, true));
      const actions = el("div", "group-actions");
      const toTray = el("button", "link-btn", T("To tray"));
      toTray.type = "button";
      toTray.addEventListener("click", () => {
        tray = Array.from(new Set(tray.concat(g.words)));
        saveStore(STORE_TRAY, tray);
        renderTray();
      });
      const del = el("button", "link-btn danger-btn", T("Delete"));
      del.type = "button";
      del.addEventListener("click", () => {
        groups.splice(idx, 1);
        saveStore(STORE_GROUPS, groups);
        renderGroups();
      });
      actions.append(toTray, del);
      row.append(chip, actions);
      list.append(row);
    });
  }

  function wireDropZone(elNode, target, appendDefault) {
    elNode.addEventListener("dragover", (e) => {
      e.preventDefault();
      elNode.classList.add("drag-over");
    });
    elNode.addEventListener("dragleave", () => elNode.classList.remove("drag-over"));
    elNode.addEventListener("drop", (e) => {
      e.preventDefault();
      elNode.classList.remove("drag-over");
      let tokens = dragPayload ? dragPayload.words : [];
      if ((!tokens || !tokens.length) && e.dataTransfer) {
        const raw = e.dataTransfer.getData("text/plain") || "";
        tokens = raw.split(/[\s,]+/).filter(Boolean);
      }
      dragPayload = null;
      applyTokensToTarget(tokens, target, e.shiftKey || appendDefault);
    });
  }

  function renderWordsSelect() {
    const sel = $("wordsSelect");
    const choices = state.scheme === "bip39" ? C.BIP39_LENGTHS : Array.from({ length: 24 }, (_, i) => i + 1);
    if (!choices.includes(state.words)) state.words = state.scheme === "bip39" ? 12 : state.words;
    sel.replaceChildren();
    for (const n of choices) {
      const opt = el("option", "", String(n));
      opt.value = String(n);
      if (n === state.words) opt.selected = true;
      sel.append(opt);
    }
    $("freewordsWrap").hidden = state.scheme !== "electrum";
    $("freewordsCheck").checked = state.freewords;
    $("distinctCheck").checked = state.distinct;
    $("schemeSelect").value = state.scheme;
  }

  function renderSlots() {
    const grid = $("slotsGrid");
    grid.replaceChildren();
    for (let i = 1; i <= state.words; i++) {
      const words = state.fix[i] || [];
      const card = el("div", "slot-card" + (activeTarget.kind === "slot" && activeTarget.slot === i ? " active-target" : ""));
      const head = el("div", "slot-head");
      head.append(el("span", "slot-num", T("Slot {n}", { n: i })));
      if (words.length) {
        const clr = el("button", "link-btn", T("Clear"));
        clr.type = "button";
        clr.addEventListener("click", (e) => {
          e.stopPropagation();
          delete state.fix[i];
          renderAll();
        });
        head.append(clr);
      }
      card.append(head);
      const chips = el("div", "slot-chips");
      if (!words.length) {
        chips.append(el("span", "slot-open", T("open")));
      } else {
        words.forEach((w, idx) => {
          chips.append(makeWordChip(w, () => {
            words.splice(idx, 1);
            if (!words.length) delete state.fix[i];
            renderAll();
          }));
        });
      }
      card.append(chips);
      card.addEventListener("click", () => {
        activeTarget = { kind: "slot", slot: i };
        renderAll();
      });
      wireDropZone(card, { kind: "slot", slot: i }, true);
      grid.append(card);
    }
  }

  function renderRequireAndPool() {
    const reqZone = $("reqZone");
    reqZone.classList.toggle("active-target", activeTarget.kind === "require");
    const reqBox = $("reqChips");
    reqBox.replaceChildren();
    reqBox.classList.toggle("empty", state.require.length === 0);
    if (!state.require.length) {
      reqBox.textContent = T("No required words.");
    } else {
      state.require.forEach((w, idx) => {
        reqBox.append(makeWordChip(w, () => {
          state.require.splice(idx, 1);
          renderAll();
        }));
      });
    }

    const poolZone = $("poolZone");
    poolZone.classList.toggle("active-target", activeTarget.kind === "pool");
    const poolBox = $("poolChips");
    poolBox.replaceChildren();
    poolBox.classList.toggle("empty", !state.poolAll && state.pool.length === 0);
    if (!state.poolAll && !state.pool.length) {
      poolBox.textContent = T("No pool words yet.");
    } else {
      if (state.poolAll) {
        const chip = el("span", "chip chip-special", "@WORDLIST");
        const x = el("button", "chip-x", "×");
        x.type = "button";
        x.setAttribute("aria-label", T("Remove"));
        x.addEventListener("click", (e) => {
          e.stopPropagation();
          state.poolAll = false;
          renderAll();
        });
        chip.append(x);
        poolBox.append(chip);
      }
      state.pool.forEach((w, idx) => {
        poolBox.append(makeWordChip(w, () => {
          state.pool.splice(idx, 1);
          renderAll();
        }));
      });
    }
  }

  function renderPaths() {
    const box = $("pathChecks");
    box.replaceChildren();
    const presets = Array.from(new Set(C.DEFAULT_PATHS.concat(C.ELECTRUM_PATHS)));
    for (const p of presets) {
      const label = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = state.paths.includes(p);
      cb.addEventListener("change", () => {
        if (cb.checked && !state.paths.includes(p)) state.paths.push(p);
        else if (!cb.checked) state.paths = state.paths.filter((x) => x !== p);
        renderEstimateAndOutput();
      });
      label.append(cb, " " + p);
      box.append(label);
    }
    const presetSet = new Set(presets);
    $("customPathsInput").value = state.paths.filter((p) => !presetSet.has(p)).join("\n");
  }

  function renderCpuPresets() {
    const sel = $("cpuPresetSelect");
    sel.replaceChildren();
    for (const preset of C.CPU_PRESETS) {
      const opt = el("option", "", T(preset.label));
      opt.value = preset.id;
      if (preset.id === cpuPreset) opt.selected = true;
      sel.append(opt);
    }
    $("customRateWrap").hidden = cpuPreset !== "custom";
    $("customRateInput").value = String(customRate);
  }

  function renderEstimateAndOutput() {
    const rate = currentCpuRate();
    const stats = C.price(state, wordlist.length, rate);
    const { problems, warnings } = C.validate(state, knownSet);
    const dur = C.formatHours(stats.hours);
    const grid = $("estimateStats");
    grid.replaceChildren();

    const sizeTone = stats.derivations <= 20_000_000 ? "stat-good" : stats.hours > 24 ? "stat-warn" : "";
    const sizeLabel = stats.derivations <= 20_000_000
      ? T("Quick on CPU")
      : stats.hours <= 24 ? T("Multi-hour CPU run") : T("Very large for CPU");

    const cells = [
      [T("Open slots"), String(stats.free) + " (" + T("{r} required, {p} pool", { r: stats.required, p: stats.poolSlots }) + ")", ""],
      [T("Raw phrases"), C.formatCount(stats.raw), ""],
      [T("Valid seeds"), C.formatCount(stats.valid), ""],
      [T("Derivations"), C.formatCount(stats.derivations) + " (" + T("{n} pass", { n: stats.passCount }) + ")", ""],
      [T("Est. CPU time"), T(dur.key, dur.vars), sizeTone],
      [T("Size"), sizeLabel, sizeTone],
    ];
    for (const [label, val, tone] of cells) {
      const cell = el("div", "stat-cell" + (tone ? " " + tone : ""));
      cell.append(el("div", "stat-label", label), el("div", "stat-val", val));
      grid.append(cell);
    }

    const vBox = $("validationBox");
    vBox.replaceChildren();
    for (const err of problems) {
      vBox.append(el("p", "banner error", T(err.key, err.vars)));
    }
    for (const warn of warnings) {
      vBox.append(el("p", "warn", T(warn.key, warn.vars)));
    }

    const confText = C.renderConf(state, stats);
    $("confPreview").value = confText;
    const filename = C.sanitizeName(state.name) + ".conf";
    $("runCmd").textContent = "python3 setperm_solve.py " + filename + " --checkpoint " + C.sanitizeName(state.name) + ".ck";
  }

  function renderQueue() {
    const box = $("queueBox");
    box.replaceChildren();
    if (!queue.length) {
      box.append(el("p", "note", T("No configs in the queue yet.")));
      return;
    }
    const rate = currentCpuRate();
    let totalHours = 0;
    const table = el("table", "queue-table");
    const head = el("tr");
    ["Name", "Summary", "Derivations", "Est. CPU time", "Actions"].forEach((h) => head.append(el("th", "", T(h))));
    table.append(head);

    queue.forEach((item, idx) => {
      const s = C.price(item, wordlist.length, rate);
      totalHours += s.hours;
      const dur = C.formatHours(s.hours);
      const tr = el("tr");
      tr.append(
        el("td", "mono", item.name + ".conf"),
        el("td", "", T("{w}w {scheme}, {f} fixed, {o} open", {
          w: item.words, scheme: item.scheme, f: s.fixed, o: s.free,
        })),
        el("td", "mono", C.formatCount(s.derivations)),
        el("td", "", T(dur.key, dur.vars))
      );
      const tdAct = el("td");
      const wrap = el("div", "queue-actions");
      const loadBtn = el("button", "link-btn", T("Edit"));
      loadBtn.type = "button";
      loadBtn.addEventListener("click", () => {
        state = C.normalizeState(item);
        syncInputsFromState();
        renderAll();
        setStatus("Loaded {name} into editor.", { name: item.name + ".conf" });
      });
      const dlBtn = el("button", "link-btn", T("Download"));
      dlBtn.type = "button";
      dlBtn.addEventListener("click", () => {
        downloadBlob(item.name + ".conf", C.renderConf(item, s), "text/plain;charset=utf-8");
      });
      const upBtn = el("button", "link-btn", "↑");
      upBtn.type = "button";
      upBtn.disabled = idx === 0;
      upBtn.addEventListener("click", () => {
        const tmp = queue[idx - 1];
        queue[idx - 1] = queue[idx];
        queue[idx] = tmp;
        saveStore(STORE_QUEUE, queue);
        renderQueue();
      });
      const delBtn = el("button", "link-btn danger-btn", T("Remove"));
      delBtn.type = "button";
      delBtn.addEventListener("click", () => {
        queue.splice(idx, 1);
        saveStore(STORE_QUEUE, queue);
        renderQueue();
      });
      wrap.append(loadBtn, dlBtn, upBtn, delBtn);
      tdAct.append(wrap);
      tr.append(tdAct);
      table.append(tr);
    });

    const totalDur = C.formatHours(totalHours);
    const scroll = el("div", "scroll");
    scroll.append(table);
    box.append(scroll, el("p", "note", T("Total queued CPU time: {time}", { time: T(totalDur.key, totalDur.vars) })));
  }

  function downloadBlob(filename, content, mime) {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  }

  function syncInputsFromState() {
    $("cfgNameInput").value = state.name;
    $("cfgNoteInput").value = state.note;
    $("passesInput").value = state.passes.join("\n");
  }

  function renderAll() {
    renderWordsSelect();
    renderSearch();
    renderTray();
    renderGroups();
    renderSlots();
    renderRequireAndPool();
    renderPaths();
    renderCpuPresets();
    renderEstimateAndOutput();
    renderQueue();
  }

  // Event wiring
  $("wordSearch").addEventListener("input", renderSearch);
  $("anyWordChip").addEventListener("dragstart", (e) => {
    dragPayload = { kind: "words", words: ["@WORDLIST"] };
    if (e.dataTransfer) e.dataTransfer.setData("text/plain", "@WORDLIST");
  });
  $("anyWordChip").addEventListener("click", () => applyTokensToTarget(["@WORDLIST"], { kind: "pool" }, true));

  function addCustomWords() {
    const tokens = $("customWordInput").value.split(/[\s,]+/).map((w) => w.trim()).filter(Boolean);
    if (!tokens.length) return;
    tray = Array.from(new Set(tray.concat(tokens)));
    saveStore(STORE_TRAY, tray);
    $("customWordInput").value = "";
    renderTray();
  }
  $("addCustomBtn").addEventListener("click", addCustomWords);
  $("customWordInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); addCustomWords(); }
  });

  $("clearTrayBtn").addEventListener("click", () => {
    tray = [];
    saveStore(STORE_TRAY, tray);
    renderTray();
  });
  $("trayToPoolBtn").addEventListener("click", () => applyTokensToTarget(tray, { kind: "pool" }, true));
  $("trayToReqBtn").addEventListener("click", () => applyTokensToTarget(tray, { kind: "require" }, true));

  $("saveGroupBtn").addEventListener("click", () => {
    if (!tray.length) return setStatus("Add words to the tray before saving a group.");
    $("groupForm").hidden = false;
    $("groupNameInput").focus();
  });
  $("cancelGroupBtn").addEventListener("click", () => { $("groupForm").hidden = true; });
  $("confirmGroupBtn").addEventListener("click", () => {
    const name = $("groupNameInput").value.trim();
    if (!name || !tray.length) return;
    groups.push({ name, words: tray.slice() });
    saveStore(STORE_GROUPS, groups);
    $("groupNameInput").value = "";
    $("groupForm").hidden = true;
    renderGroups();
  });

  $("schemeSelect").addEventListener("change", (e) => {
    const next = e.target.value;
    const prev = state.scheme;
    state.scheme = next;
    if (next === "electrum" && prev !== "electrum" && state.paths.join(",") === C.DEFAULT_PATHS.join(",")) {
      state.paths = C.ELECTRUM_PATHS.slice();
    } else if (next === "bip39" && prev === "electrum" && state.paths.join(",") === C.ELECTRUM_PATHS.join(",")) {
      state.paths = C.DEFAULT_PATHS.slice();
    }
    if (next !== "electrum") state.freewords = false;
    renderAll();
  });

  $("wordsSelect").addEventListener("change", (e) => {
    state.words = Number(e.target.value);
    state.fix = C.normalizeState(state).fix;
    if (activeTarget.kind === "slot" && activeTarget.slot > state.words) {
      activeTarget = { kind: "slot", slot: 1 };
    }
    renderAll();
  });

  $("distinctCheck").addEventListener("change", (e) => { state.distinct = e.target.checked; renderEstimateAndOutput(); });
  $("freewordsCheck").addEventListener("change", (e) => { state.freewords = e.target.checked; renderEstimateAndOutput(); });

  $("togglePasteBtn").addEventListener("click", () => { $("pasteBar").hidden = !$("pasteBar").hidden; });
  $("applyPasteBtn").addEventListener("click", () => {
    const tokens = $("pastePhraseInput").value.trim().split(/\s+/).filter(Boolean);
    if (!tokens.length) return;
    const targetLen = state.scheme === "bip39"
      ? (C.BIP39_LENGTHS.find((l) => l >= tokens.length) || 24)
      : Math.min(24, Math.max(1, tokens.length));
    state.words = targetLen;
    state.fix = {};
    tokens.slice(0, targetLen).forEach((tok, idx) => {
      if (tok === "?" || tok === "_" || tok === "*") return;
      state.fix[idx + 1] = tok.split("|").filter(Boolean);
    });
    $("pasteBar").hidden = true;
    renderAll();
  });
  $("clearSlotsBtn").addEventListener("click", () => { state.fix = {}; renderAll(); });

  $("reqZone").addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    activeTarget = { kind: "require" };
    renderAll();
  });
  $("poolZone").addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    activeTarget = { kind: "pool" };
    renderAll();
  });
  wireDropZone($("reqZone"), { kind: "require" }, true);
  wireDropZone($("poolZone"), { kind: "pool" }, true);

  $("clearReqBtn").addEventListener("click", () => { state.require = []; renderAll(); });
  $("clearPoolBtn").addEventListener("click", () => { state.pool = []; state.poolAll = false; renderAll(); });

  $("passesInput").addEventListener("input", () => {
    state.passes = $("passesInput").value.split(/\r?\n/).filter((l) => l.length > 0);
    renderEstimateAndOutput();
  });

  $("customPathsInput").addEventListener("input", () => {
    const presets = new Set(C.DEFAULT_PATHS.concat(C.ELECTRUM_PATHS));
    const checked = state.paths.filter((p) => presets.has(p));
    const custom = $("customPathsInput").value.split(/\r?\n/).map((p) => p.trim()).filter(Boolean);
    state.paths = Array.from(new Set(checked.concat(custom)));
    renderEstimateAndOutput();
  });

  $("cpuPresetSelect").addEventListener("change", (e) => {
    cpuPreset = e.target.value;
    saveStore(STORE_CPU, { preset: cpuPreset, customRate });
    renderCpuPresets();
    renderEstimateAndOutput();
    renderQueue();
  });
  $("customRateInput").addEventListener("input", (e) => {
    const val = Number(e.target.value);
    if (val > 0) customRate = val;
    saveStore(STORE_CPU, { preset: cpuPreset, customRate });
    renderEstimateAndOutput();
    renderQueue();
  });

  $("cfgNameInput").addEventListener("input", (e) => {
    state.name = C.sanitizeName(e.target.value);
    renderEstimateAndOutput();
  });
  $("cfgNoteInput").addEventListener("input", (e) => {
    state.note = e.target.value;
    renderEstimateAndOutput();
  });

  $("downloadConfBtn").addEventListener("click", () => {
    const { problems } = C.validate(state, knownSet);
    if (problems.length) return setStatus("Fix the config errors before downloading.");
    const name = C.sanitizeName(state.name) + ".conf";
    downloadBlob(name, $("confPreview").value, "text/plain;charset=utf-8");
    setStatus("Downloaded {name}.", { name });
  });

  $("copyConfBtn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("confPreview").value);
      setStatus("Copied config to clipboard.");
    } catch (e) {
      $("confPreview").select();
      setStatus("Clipboard blocked; press Ctrl+C in the preview box.");
    }
  });

  $("copyCmdBtn").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("runCmd").textContent);
      setStatus("Copied command.");
    } catch (e) {
      setStatus("Select and copy the command manually.");
    }
  });

  $("addToQueueBtn").addEventListener("click", () => {
    const { problems } = C.validate(state, knownSet);
    if (problems.length) return setStatus("Fix the config errors before queuing.");
    const copy = C.normalizeState(state);
    const existing = queue.findIndex((q) => q.name === copy.name);
    if (existing >= 0) queue[existing] = copy;
    else queue.push(copy);
    saveStore(STORE_QUEUE, queue);
    renderQueue();
    setStatus("Added {name} to queue.", { name: copy.name + ".conf" });
  });

  $("toggleImportBtn").addEventListener("click", () => { $("importBox").hidden = !$("importBox").hidden; });
  $("cancelImportBtn").addEventListener("click", () => { $("importBox").hidden = true; });
  $("applyImportBtn").addEventListener("click", () => {
    const parsed = C.parseConf($("importInput").value);
    if (parsed.errors.length) {
      setStatus("Could not parse config: {err}", { err: parsed.errors[0] });
      return;
    }
    state = C.normalizeState(parsed);
    syncInputsFromState();
    $("importBox").hidden = true;
    renderAll();
    setStatus("Loaded config into editor.");
  });

  $("clearQueueBtn").addEventListener("click", () => {
    queue = [];
    saveStore(STORE_QUEUE, queue);
    renderQueue();
  });

  $("downloadZipBtn").addEventListener("click", () => {
    if (!queue.length) return setStatus("Add at least one config to the queue first.");
    const rate = currentCpuRate();
    const entries = [];
    const filenames = [];
    for (const item of queue) {
      const fname = C.sanitizeName(item.name) + ".conf";
      filenames.push(fname);
      entries.push({ name: fname, data: C.renderConf(item, C.price(item, wordlist.length, rate)) });
    }
    entries.push({ name: "run_queue.py", data: C.runQueuePy(filenames) });
    const zipBytes = C.buildZip(entries);
    downloadBlob("bnw-cpu-queue.zip", zipBytes, "application/zip");
    setStatus("Downloaded bnw-cpu-queue.zip ({n} configs).", { n: filenames.length });
  });

  function renderBrowserResult(result) {
    const out = $("browserCheckOut");
    out.replaceChildren();
    if (!result) return;
    if (result.error) {
      out.append(el("p", "banner error", result.error));
      return;
    }
    if (result.match) {
      out.append(el("p", "banner hit", T("MATCH: {phrase} (passphrase: {pass}, path: {path})", {
        phrase: result.match.phrase,
        pass: result.match.pass || "(empty)",
        path: result.match.path,
      })));
    } else if (result.stopped) {
      out.append(el("p", "banner", T("Stopped. Checked {d} derivations ({v} valid phrases).", {
        d: result.derivations, v: result.valid,
      })));
    } else {
      out.append(el("p", "banner", T("No match. Checked {d} derivations ({v} valid phrases).", {
        d: result.derivations, v: result.valid,
      })));
    }
    if (result.rows && result.rows.length) {
      const table = el("table", "queue-table");
      const head = el("tr");
      ["Phrase", "Passphrase", "First address"].forEach((h) => head.append(el("th", "", T(h))));
      table.append(head);
      for (const row of result.rows) {
        const tr = el("tr", row.hit ? "hit" : "");
        tr.append(
          el("td", "mono", row.phrase),
          el("td", "mono", row.pass || "(empty)"),
          el("td", "mono", row.address)
        );
        table.append(tr);
      }
      const scroll = el("div", "scroll");
      scroll.append(table);
      out.append(scroll);
    }
  }

  $("stopBrowserBtn").addEventListener("click", () => {
    browserRunCancel = true;
  });

  $("runBrowserBtn").addEventListener("click", async () => {
    if (browserRunning) return;
    const { problems } = C.validate(state, knownSet);
    if (problems.length) {
      return renderBrowserResult({ error: T(problems[0].key, problems[0].vars) });
    }
    const stats = C.price(state, wordlist.length, currentCpuRate());
    const gate = C.canRunInBrowser(stats);
    if (!gate.ok) {
      return renderBrowserResult({ error: T(gate.key, gate.vars) });
    }

    const s = C.normalizeState(state);
    const passes = Array.from(new Set([""].concat(s.passes)));
    browserRunning = true;
    browserRunCancel = false;
    $("runBrowserBtn").disabled = true;
    $("stopBrowserBtn").hidden = false;
    $("browserCheckOut").replaceChildren();
    $("browserCheckProgress").textContent = T("Filtering valid candidates...");

    let validCount = 0;
    let derivCount = 0;
    let match = null;
    const previewRows = [];

    try {
      const validPhrases = [];
      let rawSeen = 0;
      for (const phrase of C.enumerateRawPhrases(s, wordlist, C.BROWSER_MAX_RAW)) {
        if (browserRunCancel) break;
        rawSeen++;
        if (rawSeen % 250 === 0) {
          $("browserCheckProgress").textContent = T("Checking raw phrases: {n}...", { n: rawSeen });
          await new Promise((r) => setTimeout(r, 0));
        }
        let ok = true;
        if (s.scheme === "bip39") ok = (await BTC.bip39Checksum(phrase, wordlist)) === true;
        else if (s.scheme === "electrum") ok = await BTC.electrumStandard(phrase);
        if (ok) {
          validPhrases.push(phrase);
          if (validPhrases.length * passes.length > C.BROWSER_MAX_DERIVATIONS) {
            return renderBrowserResult({
              error: T("Save as config, too much compute for the browser (limit: {maxDeriv} derivations or {maxRaw} raw phrases).", {
                maxDeriv: C.BROWSER_MAX_DERIVATIONS.toLocaleString("en-US"),
                maxRaw: C.BROWSER_MAX_RAW.toLocaleString("en-US"),
              }),
            });
          }
        }
      }

      validCount = validPhrases.length;
      const totalDeriv = validCount * passes.length;
      for (const phrase of validPhrases) {
        if (browserRunCancel || match) break;
        for (const pass of passes) {
          if (browserRunCancel || match) break;
          const res = await BTC.check({ text: phrase, pass, scheme: s.scheme, paths: s.paths, wordlist });
          derivCount++;
          const hitRow = res.rows.find((r) => r.match);
          if (previewRows.length < 50 || hitRow) {
            previewRows.push({
              phrase,
              pass,
              address: (hitRow || res.rows[0]).address,
              hit: Boolean(hitRow),
            });
          }
          if (hitRow) {
            match = { phrase, pass, path: hitRow.path };
            break;
          }
          if (derivCount % 5 === 0 || derivCount === totalDeriv) {
            $("browserCheckProgress").textContent = T("Checked {done} / {total} derivations...", { done: derivCount, total: totalDeriv });
            await new Promise((r) => setTimeout(r, 0));
          }
        }
      }

      renderBrowserResult({ match, stopped: browserRunCancel && !match, derivations: derivCount, valid: validCount, rows: previewRows });
    } catch (e) {
      if (e && e.message === "BROWSER_LIMIT") {
        renderBrowserResult({
          error: T("Save as config, too much compute for the browser (limit: {maxDeriv} derivations or {maxRaw} raw phrases).", {
            maxDeriv: C.BROWSER_MAX_DERIVATIONS.toLocaleString("en-US"),
            maxRaw: C.BROWSER_MAX_RAW.toLocaleString("en-US"),
          }),
        });
      } else {
        renderBrowserResult({ error: T("Check failed: {reason}", { reason: e ? e.message : "unknown" }) });
      }
    } finally {
      browserRunning = false;
      $("runBrowserBtn").disabled = false;
      $("stopBrowserBtn").hidden = true;
      $("browserCheckProgress").textContent = "";
    }
  });

  I18N.onChange(renderAll);
  syncInputsFromState();
  renderAll();
})();
