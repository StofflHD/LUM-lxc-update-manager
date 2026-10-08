// Loaded in <head> before the page renders, so a chosen theme never flashes.
// "lum-theme" = "light" | "dark"; missing = follow the operating system.
(function () {
  try {
    var t = localStorage.getItem("lum-theme");
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  } catch (e) { /* storage blocked: follow the system */ }
})();
