/*
 * MenuFlash Mini — champ téléphone avec sélecteur de pays.
 * Vérifie le numéro pendant la saisie avec la MÊME règle que le serveur
 * (/telephone/verifier), affiche l'exemple du pays choisi et bloque l'envoi
 * du formulaire tant que le numéro n'est pas valide pour ce pays.
 * Si la vérification en direct est impossible (réseau), l'envoi n'est pas
 * bloqué : le serveur vérifie de toute façon.
 */
(function () {
  "use strict";

  function setup(field) {
    if (field.dataset.mfPhoneReady === "1") return;
    field.dataset.mfPhoneReady = "1";

    var select = field.querySelector("[data-mf-phone-country]");
    var input = field.querySelector("[data-mf-phone-input]");
    var msg = field.querySelector("[data-mf-phone-msg]");
    var url = field.dataset.checkUrl;
    var defaultHelp = msg.textContent;
    var timer = null;
    var seq = 0;

    function option() { return select.options[select.selectedIndex]; }

    function updatePlaceholder() {
      var ex = option().getAttribute("data-example");
      input.placeholder = ex ? "Ex : " + ex : "";
    }

    function show(state, text) {
      field.classList.toggle("is-valid", state === "ok");
      field.classList.toggle("is-invalid", state === "error");
      msg.textContent = text || defaultHelp;
      input.setCustomValidity(state === "error" ? (text || "Numéro invalide") : "");
    }

    function check() {
      var raw = input.value.trim();
      if (!raw) { show("", ""); return; }
      var mine = ++seq;
      var q = url + "?numero=" + encodeURIComponent(raw) + "&pays=" + encodeURIComponent(select.value);
      fetch(q, { headers: { "Accept": "application/json" }, credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (mine !== seq || !data) return;            // réponse périmée ou erreur serveur
          if (data.ok) show("ok", "✓ " + data.formatted);
          else show("error", data.error);
        })
        .catch(function () { if (mine === seq) show("", ""); });
    }

    function schedule() {
      clearTimeout(timer);
      input.setCustomValidity("");                      // pas de blocage pendant la frappe
      timer = setTimeout(check, 350);
    }

    input.addEventListener("input", function () {
      var cleaned = input.value.replace(/[^0-9+\-(). ]/g, "");
      if (cleaned !== input.value) input.value = cleaned;
      schedule();
    });
    input.addEventListener("blur", function () { clearTimeout(timer); check(); });
    select.addEventListener("change", function () { updatePlaceholder(); clearTimeout(timer); check(); });

    updatePlaceholder();
    if (input.value.trim()) check();
  }

  function init() {
    document.querySelectorAll("[data-mf-phone]").forEach(setup);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
