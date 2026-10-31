/* ==========================================================================
   MenuFlash Mini — aide à la saisie sécurisée des formulaires (navigateur)

   Ce script AIDE l'utilisateur, il ne remplace pas les contrôles du serveur
   (security_forms.py), qui restent la seule vraie protection.

   - input[data-mf-password="new"]     : nouveau mot de passe -> liste des
     règles mise à jour en direct, blocage des espaces / ponctuation /
     accents à la frappe, message clair si collage d'un texte non autorisé.
   - input[data-mf-password="current"] : mot de passe existant -> seulement
     le bouton afficher/masquer et l'alerte majuscules.
   - input[data-mf-confirm="<id>"]     : confirmation, doit être identique.
   - form[data-mf-guard]               : empêche le double envoi.
   ========================================================================== */
(function () {
  "use strict";

  var MIN = 10, MAX = 64;
  var ALLOWED = /^[A-Za-z0-9]*$/;
  var RULES = [
    { key: "len",   label: MIN + " à " + MAX + " caractères",
      test: function (v) { return v.length >= MIN && v.length <= MAX; } },
    { key: "upper", label: "Une lettre majuscule", test: function (v) { return /[A-Z]/.test(v); } },
    { key: "lower", label: "Une lettre minuscule", test: function (v) { return /[a-z]/.test(v); } },
    { key: "digit", label: "Un chiffre",           test: function (v) { return /[0-9]/.test(v); } },
    { key: "chars", label: "Lettres sans accent et chiffres uniquement (ni espace, ni ponctuation)",
      test: function (v) { return v.length > 0 && ALLOWED.test(v); } }
  ];

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }

  function addToggle(input) {
    var wrap = el("div", "mf-pwd-wrap");
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var btn = el("button", "mf-pwd-toggle");
    btn.type = "button";
    btn.setAttribute("aria-label", "Afficher le mot de passe");
    btn.innerHTML = '<i class="bi bi-eye"></i>';
    btn.addEventListener("click", function () {
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.innerHTML = show ? '<i class="bi bi-eye-slash"></i>' : '<i class="bi bi-eye"></i>';
      btn.setAttribute("aria-label", show ? "Masquer le mot de passe" : "Afficher le mot de passe");
      input.focus();
    });
    wrap.appendChild(btn);
    return wrap;
  }

  function addCapsWarning(input, after) {
    var warn = el("div", "mf-caps-warning d-none", "Attention : la touche Verr. Maj est activée.");
    after.parentNode.insertBefore(warn, after.nextSibling);
    function check(e) {
      if (e.getModifierState) warn.classList.toggle("d-none", !e.getModifierState("CapsLock"));
    }
    input.addEventListener("keydown", check);
    input.addEventListener("keyup", check);
    input.addEventListener("blur", function () { warn.classList.add("d-none"); });
    return warn;
  }

  function setupNewPassword(input, wrap, after) {
    var list = el("ul", "mf-pwd-rules");
    list.setAttribute("aria-live", "polite");
    var items = {};
    RULES.forEach(function (r) {
      var li = el("li", "", r.label);
      items[r.key] = li;
      list.appendChild(li);
    });
    var hint = el("div", "mf-pwd-hint d-none");
    after.parentNode.insertBefore(list, after.nextSibling);
    list.parentNode.insertBefore(hint, list);

    function showHint(text) {
      hint.textContent = text;
      hint.classList.remove("d-none");
      clearTimeout(hint._t);
      hint._t = setTimeout(function () { hint.classList.add("d-none"); }, 3500);
    }

    // Bloque à la frappe les caractères non autorisés.
    input.addEventListener("beforeinput", function (e) {
      if (e.inputType === "insertText" && e.data && !ALLOWED.test(e.data)) {
        e.preventDefault();
        showHint("Caractère « " + e.data + " » non autorisé : uniquement des lettres sans accent et des chiffres.");
      }
    });

    function update() {
      var v = input.value;
      var allOk = true;
      RULES.forEach(function (r) {
        var ok = r.test(v);
        items[r.key].classList.toggle("ok", ok);
        if (!ok) allOk = false;
      });
      input.setCustomValidity(allOk || v === "" ? "" :
        "Le mot de passe ne respecte pas toutes les règles affichées.");
      if (v && !ALLOWED.test(v)) {
        showHint("Le texte contient des espaces, de la ponctuation ou des accents, qui ne sont pas autorisés.");
      }
      document.querySelectorAll('[data-mf-confirm="' + input.id + '"]').forEach(function (c) {
        c.dispatchEvent(new Event("input"));
      });
    }
    input.addEventListener("input", update);
    update();
  }

  function setupConfirm(confirm) {
    var target = document.getElementById(confirm.getAttribute("data-mf-confirm"));
    if (!target) return;
    var msg = el("div", "mf-confirm-msg d-none", "Les deux mots de passe ne correspondent pas.");
    var anchor = confirm.closest(".mf-pwd-wrap") || confirm;
    anchor.parentNode.insertBefore(msg, anchor.nextSibling);
    confirm.addEventListener("input", function () {
      var mismatch = confirm.value !== "" && confirm.value !== target.value;
      confirm.setCustomValidity(mismatch ? "Les deux mots de passe ne correspondent pas." : "");
      msg.classList.toggle("d-none", !mismatch);
    });
  }

  function setupNoDoubleSubmit(form) {
    form.addEventListener("submit", function (e) {
      if (form.dataset.mfSubmitting === "1") { e.preventDefault(); return; }
      if (typeof form.checkValidity === "function" && !form.checkValidity()) return;
      form.dataset.mfSubmitting = "1";
      form.querySelectorAll('button[type="submit"]').forEach(function (b) {
        b.disabled = true;
        b.dataset.mfLabel = b.innerHTML;
        b.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Envoi…';
      });
      // Si l'utilisateur revient sur la page (bouton retour), on réactive.
      setTimeout(function () {
        form.dataset.mfSubmitting = "0";
        form.querySelectorAll('button[type="submit"]').forEach(function (b) {
          b.disabled = false;
          if (b.dataset.mfLabel) b.innerHTML = b.dataset.mfLabel;
        });
      }, 15000);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("input[data-mf-password]").forEach(function (input) {
      input.setAttribute("autocapitalize", "off");
      input.setAttribute("autocorrect", "off");
      input.setAttribute("spellcheck", "false");
      var wrap = addToggle(input);
      var warn = addCapsWarning(input, wrap);
      if (input.getAttribute("data-mf-password") === "new") setupNewPassword(input, wrap, warn);
    });
    document.querySelectorAll("input[data-mf-confirm]").forEach(setupConfirm);
    document.querySelectorAll("form[data-mf-guard]").forEach(setupNoDoubleSubmit);
  });
})();
