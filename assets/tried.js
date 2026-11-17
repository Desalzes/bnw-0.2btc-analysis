// The was-it-tried page: a search over data/tried.js, the index build.py makes from the public configs and run ledger.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const D = window.TRIED;
  const REPO = "https://github.com/Desalzes/bnw-0.2btc-analysis/blob/main/puzzles/btc-02-picture/harness/templates/";
  const VERDICT = { NO_MATCH: "Finished, no match", STARTED: "Started, not finished", NOT_CLEAN: "Started, not finished", FOUND: "FOUND",
                    never: "No run tied to it", HELD: "No run tied to it" };
  const PAGE = 40;
  const passKeys = Object.keys(D.plain).concat(Object.keys(D.electrum).filter((k) => !(k in D.plain)));
  const english = new Set(D.bip39.split(" "));               // the words an open slot can take
  const own = (table, key) => (Object.hasOwn(table, key) ? table[key] : []);
  const wordKeys = Object.keys(D.words);

  function writeScope() {
    $("scope").textContent = T("{configs} published configs and {runs} finished runs, up to {cutoff}. Repository commit {commit}.",
      { configs: D.configs.length.toLocaleString("en-US"), runs: D.runs.toLocaleString("en-US"), cutoff: D.cutoff, commit: D.commit });
  }

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function foundAs(role, slot) {                              // what the search found in a config, as one phrase
    if (role === 0) return T("word fixed at slot {slot}", { slot });
    return role === 1 ? T("word required, any slot") : T("word in the word pool");
  }

  function lookup(q) {
    const word = q.normalize("NFKD");
    const postings = own(D.words, word);
    const hits = new Map();                                  // config index -> what the search found in it
    for (const [i, role, slot] of postings) hits.set(i, (hits.get(i) || []).concat(foundAs(role, slot)));
    const passes = new Set(own(D.plain, q.normalize("NFKD")).concat(own(D.electrum, BTC.electrumText(q))));
    for (const i of passes) hits.set(i, (hits.get(i) || []).concat(T("passphrase")));
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
    if (r.roles[0].size) add(T("As a word: fixed in a slot in {n} configs.", { n: r.roles[0].size }));
    if (r.roles[1].size) add(T("As a word: required, in any slot, in {n} configs.", { n: r.roles[1].size }));
    if (r.roles[2].size) add(T("As a word: in the word pool of {n} configs.", { n: r.roles[2].size }));
    if (r.passCount) add(T("As a passphrase or seed extension: in {n} configs.", { n: r.passCount }));
    if (r.inList) {
      const done = D.everyWord.filter((i) => D.configs[i].verdict === "NO_MATCH").length;
      add(T("It is a BIP39 word, so it could also fill an open slot in the {total} configs that left a slot open to the whole list. {done} of those finished with no match, and the rest have no finished run tied to them. They are not listed below.",
            { total: D.everyWord.length, done }));
    }
    return list;
  }

  function table(rows, shown) {
    const t = el("table"), head = el("tr");
    ["Config", "Found as", "Seed type", "Words", "Result", "Candidates"].forEach((h) => head.append(el("th", "", T(h))));
    t.append(head);
    for (const [i, found] of rows.slice(0, shown)) {
      const c = D.configs[i], tr = el("tr"), name = el("td", "mono"), a = el("a", "", c.rel.replace(/\.conf$/, ""));
      a.href = REPO + c.rel;
      name.append(a);
      tr.append(name, el("td", "", [...new Set(found)].join(T(", "))), el("td", "", c.scheme), el("td", "", String(c.words)),
                el("td", "", VERDICT[c.verdict] ? T(VERDICT[c.verdict]) : c.verdict), el("td", "", c.tested ? c.tested.toLocaleString("en-US") : ""));
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
    const rows = [...r.hits.entries()].sort((a, b) => (D.configs[a[0]].verdict === "NO_MATCH" ? 0 : 1) - (D.configs[b[0]].verdict === "NO_MATCH" ? 0 : 1) || a[0] - b[0]);
    const finished = rows.filter((row) => D.configs[row[0]].verdict === "NO_MATCH").length;
    box.append(rows.length
      ? el("p", "banner", T("Found in {n} configs, {finished} of them finished with no match.", { n: rows.length, finished }))
      : el("p", "banner", r.variants.length ? T("Not written this way in any config, but it is under another spelling.") :
                          r.inList ? T("Not named in any config.") : T("Not found in the published configs.")));
    box.append(summary(q, r));
    if (r.variants.length) box.append(...pills(T("Also written in configs as:"), r.variants));
    if (r.similar.length) box.append(...pills(T("Passphrases that contain this text:"), r.similar));
    if (!rows.length) return;
    const scroll = el("div", "scroll");
    let shown = PAGE;
    const draw = () => {
      scroll.replaceChildren(table(rows, shown));
      if (shown < rows.length) {
        const more = el("button", "more", T("Show all {n}", { n: rows.length }));
        more.addEventListener("click", () => { shown = rows.length; draw(); });
        scroll.append(more);
      }
    };
    draw();
    box.append(scroll);
  }

  writeScope();
  let timer = 0;
  $("q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(render, 120); });
  I18N.onChange(() => { writeScope(); render(); });
})();
