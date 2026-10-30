// The was-it-tried page: a search over data/tried.js, the index build.py makes from the public configs and run ledger.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const T = window.TRIED;
  const REPO = "https://github.com/Desalzes/bnw-0.2btc-analysis/blob/main/puzzles/btc-02-picture/harness/templates/";
  const VERDICT = { NO_MATCH: "Finished, no match", STARTED: "Started, not finished", NOT_CLEAN: "Started, not finished", FOUND: "FOUND",
                    never: "No run tied to it", HELD: "No run tied to it" };
  const ROLE = ["fixed in a slot", "required, any slot", "in the word pool"];
  const PAGE = 40;
  const passKeys = Object.keys(T.plain).concat(Object.keys(T.electrum).filter((k) => !(k in T.plain)));
  const english = new Set(T.bip39.split(" "));               // the words an open slot can take
  const own = (table, key) => (Object.hasOwn(table, key) ? table[key] : []);
  const wordKeys = Object.keys(T.words);

  $("scope").textContent = T.configs.length.toLocaleString("en-US") + " published configs and " +
    T.runs.toLocaleString("en-US") + " finished runs, up to " + T.cutoff + ". Repository commit " + T.commit + ".";

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function lookup(q) {
    const word = q.normalize("NFKD");
    const postings = own(T.words, word);
    const hits = new Map();                                  // config index -> what the search found in it
    for (const [i, role, slot] of postings) {
      const text = role === 0 ? "word fixed at slot " + slot : "word " + ROLE[role];
      hits.set(i, (hits.get(i) || []).concat(text));
    }
    const passes = new Set(own(T.plain, q.normalize("NFKD")).concat(own(T.electrum, BTC.electrumText(q))));
    for (const i of passes) hits.set(i, (hits.get(i) || []).concat("passphrase"));
    const roles = [new Set(), new Set(), new Set()];
    postings.forEach((p) => roles[p[1]].add(p[0]));
    const lower = word.toLowerCase();
    const similar = passKeys.filter((k) => k.toLowerCase().includes(lower) && k !== word).slice(0, 60);
    const variants = wordKeys.filter((k) => k !== word && k.toLowerCase() === lower);
    return { hits, roles, passCount: passes.size, similar, variants, inList: english.has(word) };
  }

  function summary(q, r) {
    const list = el("ul", "facts");
    const add = (s) => list.append(el("li", "", s));
    if (r.roles[0].size) add("As a word: fixed in a slot in " + r.roles[0].size + " configs.");
    if (r.roles[1].size) add("As a word: required, in any slot, in " + r.roles[1].size + " configs.");
    if (r.roles[2].size) add("As a word: in the word pool of " + r.roles[2].size + " configs.");
    if (r.passCount) add("As a passphrase or seed extension: in " + r.passCount + " configs.");
    if (r.inList) {
      const done = T.everyWord.filter((i) => T.configs[i].verdict === "NO_MATCH").length;
      add("It is a BIP39 word, so it could also fill an open slot in the " + T.everyWord.length + " configs that left a slot open " +
          "to the whole list. " + done + " of those finished with no match, and the rest have no finished run tied to them. " +
          "They are not listed below.");
    }
    return list;
  }

  function table(rows, shown) {
    const t = el("table"), head = el("tr");
    ["Config", "Found as", "Seed type", "Words", "Result", "Candidates"].forEach((h) => head.append(el("th", "", h)));
    t.append(head);
    for (const [i, found] of rows.slice(0, shown)) {
      const c = T.configs[i], tr = el("tr"), name = el("td", "mono"), a = el("a", "", c.rel.replace(/\.conf$/, ""));
      a.href = REPO + c.rel;
      name.append(a);
      tr.append(name, el("td", "", [...new Set(found)].join(", ")), el("td", "", c.scheme), el("td", "", String(c.words)),
                el("td", "", VERDICT[c.verdict] || c.verdict), el("td", "", c.tested ? c.tested.toLocaleString("en-US") : ""));
      t.append(tr);
    }
    return t;
  }

  function pills(label, items) {
    const list = el("ul", "pills");
    items.forEach((s) => {
      const li = el("li"), b = el("button", "", s);
      b.addEventListener("click", () => { $("q").value = s; render(); });
      li.append(b);
      list.append(li);
    });
    return [el("p", "note", label), list];
  }

  function render() {
    const q = $("q").value.trim(), box = $("out");
    box.replaceChildren();
    if (!q) return;
    const r = lookup(q);
    const rows = [...r.hits.entries()].sort((a, b) => (T.configs[a[0]].verdict === "NO_MATCH" ? 0 : 1) - (T.configs[b[0]].verdict === "NO_MATCH" ? 0 : 1) || a[0] - b[0]);
    const finished = rows.filter((row) => T.configs[row[0]].verdict === "NO_MATCH").length;
    box.append(rows.length
      ? el("p", "banner", "Found in " + rows.length + " configs, " + finished + " of them finished with no match.")
      : el("p", "banner", r.variants.length ? "Not written this way in any config, but it is under another spelling." :
                          r.inList ? "Not named in any config." : "Not found in the published configs."));
    box.append(summary(q, r));
    if (r.variants.length) box.append(...pills("Also written in configs as:", r.variants));
    if (r.similar.length) box.append(...pills("Passphrases that contain this text:", r.similar));
    if (!rows.length) return;
    const scroll = el("div", "scroll");
    let shown = PAGE;
    const draw = () => {
      scroll.replaceChildren(table(rows, shown));
      if (shown < rows.length) {
        const more = el("button", "more", "Show all " + rows.length);
        more.addEventListener("click", () => { shown = rows.length; draw(); });
        scroll.append(more);
      }
    };
    draw();
    box.append(scroll);
  }

  let timer = 0;
  $("q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(render, 120); });
})();
