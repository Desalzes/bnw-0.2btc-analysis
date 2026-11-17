// The language switch for the tools site: English as written, or Persian from i18n-fa.js. Loaded first on every page.
// Static text carries data-i18n="id" (the English stays in the HTML as the default); text a script builds goes through
// T("English sentence", {name: value}), which falls back to the English when there is no Persian for it.
// The only thing kept is the language itself, under one key in this browser's local storage.
(function () {
  "use strict";
  const KEY = "bnw-tools-lang";
  const FA = window.I18N_FA || {};
  const handlers = [];
  const english = new WeakMap();           // element -> its English markup and attributes, kept when it is first translated
  let lang = "en";
  try { if (localStorage.getItem(KEY) === "fa") lang = "fa"; } catch (e) { /* storage blocked: English */ }

  // A value dropped into a Persian sentence keeps its own direction (a path, a word, a number) through the bidi isolate marks.
  function fill(text, vars) {
    if (!vars) return text;
    return text.replace(/\{(\w+)\}/g, (hole, name) => {
      if (!(name in vars)) return hole;
      const value = String(vars[name]);
      return lang === "fa" ? "\u2068" + value + "\u2069" : value;
    });
  }

  const misses = new Set();                // sentences asked for in Persian that have no Persian yet, for checking a page by hand

  function T(key, vars) {
    if (lang === "fa" && key && !Object.hasOwn(FA, key)) misses.add(key);
    return fill(lang === "fa" && Object.hasOwn(FA, key) ? FA[key] : key, vars);
  }

  function setNode(el, text) {
    if (el.tagName === "TITLE") el.textContent = text;
    else el.innerHTML = text;
  }

  function translateStatic() {
    document.querySelectorAll("[data-i18n]").forEach((el) => {
      if (!english.has(el)) english.set(el, { html: el.tagName === "TITLE" ? el.textContent : el.innerHTML, attrs: {} });
      const was = english.get(el).html, id = el.dataset.i18n;
      setNode(el, lang === "fa" && Object.hasOwn(FA, id) ? FA[id] : was);
    });
    document.querySelectorAll("[data-i18n-attr]").forEach((el) => {
      if (!english.has(el)) english.set(el, { html: null, attrs: {} });
      const was = english.get(el).attrs;
      el.dataset.i18nAttr.split(/\s+/).filter(Boolean).forEach((pair) => {
        const [attr, id] = pair.split(":");
        if (!(attr in was)) was[attr] = el.getAttribute(attr);
        el.setAttribute(attr, lang === "fa" && Object.hasOwn(FA, id) ? FA[id] : was[attr]);
      });
    });
  }

  function direction() {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "fa" ? "rtl" : "ltr";
  }

  let button = null;
  function mountToggle() {
    const top = document.querySelector(".top");
    if (!top) return;
    button = document.createElement("button");
    button.type = "button";
    button.id = "langToggle";
    button.className = "lang";
    button.addEventListener("click", () => setLang(lang === "fa" ? "en" : "fa"));
    top.append(button);
    labelToggle();
  }

  function labelToggle() {             // the button names the language it switches to, in that language
    if (!button) return;
    button.textContent = lang === "fa" ? "English" : "فارسی";
    button.lang = lang === "fa" ? "en" : "fa";
    button.title = lang === "fa" ? "Switch to English" : "تغییر به فارسی";
    button.setAttribute("aria-label", button.title);
  }

  function setLang(next) {
    if (next === lang) return;
    lang = next;
    try { localStorage.setItem(KEY, lang); } catch (e) { /* the choice lasts until the page closes */ }
    direction();
    translateStatic();
    labelToggle();
    handlers.forEach((fn) => { try { fn(lang); } catch (e) { console.error(e); } });
  }

  direction();
  window.T = T;
  window.I18N = { get lang() { return lang; }, setLang, onChange: (fn) => handlers.push(fn), misses };
  document.addEventListener("DOMContentLoaded", () => {
    mountToggle();
    translateStatic();
  });
})();
