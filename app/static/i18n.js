// Translations for the web UI. The texts live in /static/lang/<code>.json (English is the
// reference); /api/i18n returns the chosen language merged over English. Loaded in
// <head>: the page stays hidden until the texts are there, so it never flashes English.
//
// t("key", { name: "x", count: 3 }) - "{name}" placeholders, plural forms "key_one" /
// "key_other" (and e.g. "key_few") chosen by count. Static HTML: data-i18n="key" (text),
// data-i18n-title / -aria / -placeholder, data-i18n-html (our own files only).
(function () {
  var STORE = "lum-lang";
  var stored = null;
  try { stored = localStorage.getItem(STORE); } catch (e) { /* blocked */ }
  var wanted = stored || (navigator.languages || [navigator.language || "en"]).join(",");
  document.documentElement.classList.add("i18n-loading");

  var I18N = window.I18N = { lang: "en", locale: "en-GB", languages: { en: "English" }, strings: {} };

  function t(key, vars) {
    vars = vars || {};
    var s = I18N.strings;
    var text;
    if (typeof vars.count === "number") {
      var form = "other";
      try { form = new Intl.PluralRules(I18N.lang).select(vars.count); } catch (e) { /* old browser */ }
      text = s[key + "_" + form] != null ? s[key + "_" + form] : s[key + "_other"];
    }
    if (text == null) text = s[key] != null ? s[key] : key;
    return String(text).replace(/\{(\w+)\}/g, function (m, k) { return k in vars ? vars[k] : m; });
  }

  function apply(root) {
    root = root || document;
    var each = function (attr, fn) {
      root.querySelectorAll("[" + attr + "]").forEach(function (el) { fn(el, el.getAttribute(attr)); });
    };
    each("data-i18n", function (el, k) { el.textContent = t(k); });
    each("data-i18n-html", function (el, k) { el.innerHTML = t(k); });
    each("data-i18n-title", function (el, k) { el.title = t(k); });
    each("data-i18n-aria", function (el, k) { el.setAttribute("aria-label", t(k)); });
    each("data-i18n-placeholder", function (el, k) { el.placeholder = t(k); });
  }

  function remember(lang) {
    // the server answers in this language too (error messages)
    document.cookie = "lum_lang=" + encodeURIComponent(lang) + "; path=/; max-age=31536000; samesite=lax";
  }

  I18N.t = t;
  I18N.has = function (key) { return I18N.strings[key] != null; };
  I18N.apply = apply;
  I18N.set = function (lang) {
    try { localStorage.setItem(STORE, lang); } catch (e) { /* blocked: this page only */ }
    remember(lang);
    location.reload();
  };
  window.t = t;

  I18N.ready = fetch("/api/i18n?lang=" + encodeURIComponent(wanted), { cache: "no-cache" })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      I18N.lang = d.lang;
      I18N.locale = d.strings._locale || d.lang;
      I18N.languages = d.languages;
      I18N.strings = d.strings;
      document.documentElement.lang = d.lang;
      I18N.loaded = true;
      remember(d.lang);
    })
    .catch(function () { /* server unreachable: English from the page itself */ })
    .then(function () {
      if (document.readyState === "loading") {
        return new Promise(function (r) { document.addEventListener("DOMContentLoaded", r); });
      }
      return undefined;
    })
    .then(function () {
      if (I18N.loaded) apply(); // else keep the English of the page itself
      document.documentElement.classList.remove("i18n-loading");
    });
})();
