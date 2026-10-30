// The try-a-phrase page. All the derivation is in btc.js. Nothing typed here is stored, logged or put in the address.
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const wordlist = window.BIP39_ENGLISH.split(" ");
  $("paths").value = BTC.DEFAULT_PATHS.join("\n");

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function facts(r) {
    const out = [];
    if (r.scheme === "electrum") {
      out.push(r.electrumStandard ? "This is a valid standard Electrum seed." :
        "This is not a standard Electrum seed, the only kind the published searches cover. Its addresses are shown anyway.");
    } else if (r.checksum === true) {
      out.push("The BIP39 checksum is valid.");
    } else if (r.checksum === false) {
      out.push("The BIP39 checksum is not valid: a BIP39 wallet would refuse this phrase. Its addresses are shown anyway.");
    } else {
      out.push("This is not a BIP39 phrase (it needs 12, 15, 18, 21 or 24 list words): a BIP39 wallet would refuse it. Its addresses are shown anyway.");
    }
    if (r.unknown.length) out.push("Not on the BIP39 word list: " + r.unknown.join(", ") + ".");
    return out;
  }

  function show(r) {
    const box = $("out");
    box.replaceChildren();
    box.append(r.match
      ? el("p", "banner hit", "MATCH: this phrase reaches the prize address.")
      : el("p", "banner", "No match. " + r.rows.length + " addresses checked."));
    const list = el("ul", "facts");
    facts(r).forEach((f) => list.append(el("li", "", f)));
    box.append(list);
    const table = el("table"), head = el("tr");
    ["Path", "Key form", "Address"].forEach((h) => head.append(el("th", "", h)));
    table.append(head);
    for (const row of r.rows) {
      const tr = el("tr", row.match ? "hit" : "");
      tr.append(el("td", "mono", row.path), el("td", "", row.compressed ? "compressed" : "uncompressed"), el("td", "mono", row.address));
      table.append(tr);
    }
    const scroll = el("div", "scroll");
    scroll.append(table);
    box.append(scroll);
  }

  function fail(message) {
    $("out").replaceChildren(el("p", "banner error", message));
  }

  async function run() {
    const text = $("phrase").value, pass = $("pass").value;
    const scheme = document.querySelector("input[name=scheme]:checked").value;
    const paths = $("paths").value.split("\n").map((p) => p.trim()).filter(Boolean);
    if (!text.trim()) return fail("Enter a phrase first.");
    if (!paths.length) return fail("Enter at least one path.");
    const bad = paths.find((p) => !BTC.PATH_RE.test(p));
    if (bad) return fail("This is not a path: " + bad + ". A path looks like m/44'/0'/0'/0/0.");
    $("go").disabled = true;
    try {
      show(await BTC.check({ text, pass, scheme, paths, wordlist }));
    } catch (e) {
      fail("That could not be checked: " + e.message);
    } finally {
      $("go").disabled = false;
    }
  }

  $("go").addEventListener("click", run);
  $("phrase").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); }
  });
})();
