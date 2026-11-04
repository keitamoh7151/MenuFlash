/*
 * MenuFlash Mini — fenêtre « Commande à emporter » du menu client.
 *
 * Utilisée UNIQUEMENT sur le lien général du menu (data-order-mode="remote").
 * Sur le QR code d'une table, la commande part directement en « sur place ».
 *
 * Parcours : panier -> récapitulatif -> « Continuer » -> cette fenêtre
 * (prénom, téléphone, heure de retrait, email facultatif) -> « Confirmer la
 * commande » -> page de suivi avec le numéro et le code de retrait.
 * Le serveur (takeaway.py) revérifie tout.
 */
(function () {
  "use strict";

  if (window.__mfTakeawayLoaded) return;
  window.__mfTakeawayLoaded = true;

  var NAME_RE = /^\p{L}+(?:[ '’\-]\p{L}+)*$/u;

  function t(key, fallback) {
    return (window.MFTranslate && window.MFTranslate(key)) || fallback;
  }

  function init() {
    var form = document.getElementById("order-form");
    var modalEl = document.getElementById("mf-takeaway-modal");
    if (!form || form.dataset.orderMode !== "remote" || !modalEl || typeof bootstrap === "undefined") return;

    var modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    var nameInput = document.getElementById("tk-name");
    var phoneInput = document.getElementById("tk-phone");
    var emailInput = document.getElementById("tk-email");
    var slotSelect = document.getElementById("tk-slot");
    var errorBox = document.getElementById("mf-tk-error");
    var summaryEl = document.getElementById("mf-tk-summary");
    var confirmBtn = document.getElementById("mf-tk-confirm");
    var labelEl = confirmBtn.querySelector(".confirm-label");
    var spinnerEl = confirmBtn.querySelector(".spinner-border");
    var coarse = !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);

    var send = null;
    var busy = false;

    function pickupType() {
      var checked = modalEl.querySelector('input[name="pickup_type"]:checked');
      return checked ? checked.value : "";
    }
    function syncPickup() {
      if (!slotSelect) return;
      var scheduled = pickupType() === "scheduled";
      slotSelect.classList.toggle("d-none", !scheduled);
      slotSelect.disabled = !scheduled;
      slotSelect.required = scheduled;
      if (scheduled && !coarse) slotSelect.focus();
    }
    modalEl.querySelectorAll('input[name="pickup_type"]').forEach(function (r) {
      r.addEventListener("change", syncPickup);
    });
    syncPickup();

    function showError(message) {
      errorBox.textContent = message || t("tk_error_generic", "Une erreur est survenue. Réessayez.");
      errorBox.classList.remove("d-none");
      errorBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    function hideError() {
      errorBox.textContent = "";
      errorBox.classList.add("d-none");
    }

    nameInput.addEventListener("input", function () { nameInput.setCustomValidity(""); });
    if (slotSelect) slotSelect.addEventListener("change", function () { slotSelect.setCustomValidity(""); });

    function validate() {
      var name = nameInput.value.replace(/\s+/g, " ").trim();
      nameInput.value = name;
      nameInput.setCustomValidity(name.length >= 2 && NAME_RE.test(name) ? "" : t("tk_err_name", "Indiquez votre prénom (lettres uniquement)."));
      if (!nameInput.reportValidity()) return false;

      if (!phoneInput.value.trim()) phoneInput.setCustomValidity(t("tk_err_phone", "Indiquez votre numéro de téléphone."));
      if (!phoneInput.reportValidity()) {
        if (!phoneInput.value.trim()) phoneInput.setCustomValidity("");
        return false;
      }

      if (pickupType() === "scheduled" && slotSelect) {
        slotSelect.setCustomValidity(slotSelect.value ? "" : t("tk_err_slot", "Choisissez un créneau de retrait."));
        if (!slotSelect.reportValidity()) return false;
      }

      emailInput.value = emailInput.value.trim();
      return emailInput.reportValidity();
    }

    function setBusy(state) {
      busy = state;
      confirmBtn.disabled = state;
      modalEl.querySelectorAll('[data-bs-dismiss="modal"]').forEach(function (b) { b.disabled = state; });
      spinnerEl.classList.toggle("d-none", !state);
      labelEl.textContent = state ? t("cart_sending", "Envoi…") : t("tk_confirm", "Confirmer la commande");
    }

    function submit() {
      if (busy || !send) return;
      hideError();
      if (!validate()) return;
      setBusy(true);
      send().then(function (res) {
        if (res && res.ok) return;
        setBusy(false);
        if (res && res.handled) { modal.hide(); return; }
        showError(res && res.error);
      });
    }

    confirmBtn.addEventListener("click", submit);
    modalEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && e.target.tagName === "INPUT") {
        e.preventDefault();
        submit();
      }
    });
    modalEl.addEventListener("hide.bs.modal", function (e) { if (busy) e.preventDefault(); });
    modalEl.addEventListener("shown.bs.modal", function () {
      if (!coarse && !nameInput.value) nameInput.focus();
    });

    window.MFTakeaway = {
      active: true,
      open: function (sendFn, summary) {
        send = sendFn;
        summaryEl.textContent = summary || "";
        hideError();
        setBusy(false);
        modal.show();
      }
    };
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
