/*
 * MenuFlash Mini - choix « Sur place / À emporter » sur le menu client.
 * Le serveur revalide tout : ce script ne fait que l'affichage.
 */
(function () {
  "use strict";
  var COUNTRIES = [["ML", "Mali (+223)"], ["SN", "Sénégal (+221)"], ["CI", "Côte d'Ivoire (+225)"],
    ["BF", "Burkina Faso (+226)"], ["GN", "Guinée (+224)"], ["NE", "Niger (+227)"], ["MR", "Mauritanie (+222)"],
    ["BJ", "Bénin (+229)"], ["TG", "Togo (+228)"], ["CM", "Cameroun (+237)"], ["MA", "Maroc (+212)"],
    ["DZ", "Algérie (+213)"], ["TN", "Tunisie (+216)"], ["FR", "France (+33)"], ["BE", "Belgique (+32)"],
    ["CH", "Suisse (+41)"], ["CA", "Canada (+1)"], ["US", "États-Unis (+1)"]];

  function init() {
    var form = document.getElementById("order-form");
    var slug = form && form.getAttribute("data-slug");
    if (!form || !slug) return;
    fetch("/m/" + encodeURIComponent(slug) + "/a-emporter/config.json", { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (c) { if (c && (c.enabled || c.paused)) build(form, c); })
      .catch(function () {});
  }

  function build(form, c) {
    var notes = document.getElementById("notes");
    var anchor = notes ? notes.parentNode : null;
    if (!anchor || !anchor.parentNode) return;
    var atTable = !!(form.querySelector('[name="table_label"]') || {}).value;
    var opts = COUNTRIES.map(function (x) { return '<option value="' + x[0] + '">' + x[1] + "</option>"; }).join("");
    var box = document.createElement("section");
    box.className = "mf-fulfillment mt-4";
    if (c.paused) {
      box.innerHTML = '<div class="small text-secondary"><i class="bi bi-pause-circle me-1"></i>' +
        "Les commandes à emporter sont suspendues pour le moment.</div>";
      anchor.parentNode.insertBefore(box, anchor);
      return;
    }
    box.innerHTML =
      '<div class="fw-bold mb-2">Comment souhaitez-vous recevoir votre commande ?</div>' +
      '<div class="mf-fulfillment-choice">' +
      '<label><input type="radio" name="fulfillment" value="table"' + (atTable ? " checked" : "") + '> Sur place</label>' +
      '<label><input type="radio" name="fulfillment" value="takeaway"' + (atTable ? "" : " checked") + '> À emporter</label></div>' +
      '<div class="mf-takeaway-fields mt-3"><div class="row g-2">' +
      '<div class="col-6"><label class="form-label small mb-1" for="tk-name">Votre prénom</label>' +
      '<input id="tk-name" class="form-control" name="customer_name" maxlength="60" autocomplete="given-name"></div>' +
      '<div class="col-6"><label class="form-label small mb-1" for="tk-phone">Téléphone</label>' +
      '<input id="tk-phone" class="form-control" type="tel" name="customer_phone" maxlength="30" autocomplete="tel-national" inputmode="tel"></div>' +
      '<div class="col-12"><select class="form-select form-select-sm" name="customer_phone_country" aria-label="Pays du téléphone">' + opts + "</select></div>" +
      '<div class="col-12"><label class="form-label small mb-1" for="tk-email">Email pour être prévenu quand c\'est prêt <span class="text-secondary">(optionnel)</span></label>' +
      '<input id="tk-email" class="form-control" type="email" name="customer_email" maxlength="160" autocomplete="email" inputmode="email"></div>' +
      '<div class="col-12"><label class="form-label small mb-1" for="tk-when">Retrait</label>' +
      '<select id="tk-when" class="form-select" name="pickup_type">' +
      (c.allow_asap ? '<option value="asap">Dès que possible (environ ' + c.prep_minutes + " min)</option>" : "") +
      (c.allow_scheduled ? '<option value="scheduled">Choisir une heure</option>' : "") + "</select></div>" +
      '<div class="col-12 d-none" data-pickup-wrap><input class="form-control" type="datetime-local" name="pickup_at" aria-label="Heure de retrait"></div>' +
      '</div><div class="form-text mt-2">Paiement au retrait. Annulation possible tant que la préparation n\'a pas commencé.</div></div>';
    anchor.parentNode.insertBefore(box, anchor);

    var fields = box.querySelector(".mf-takeaway-fields");
    var when = box.querySelector("[name=pickup_type]");
    var wrap = box.querySelector("[data-pickup-wrap]");
    var dt = box.querySelector("[name=pickup_at]");
    if (c.now) dt.min = c.now;

    function refresh() {
      var take = box.querySelector("[name=fulfillment]:checked").value === "takeaway";
      fields.classList.toggle("d-none", !take);
      wrap.classList.toggle("d-none", !(take && when.value === "scheduled"));
      // En mode « sur place », ces champs ne doivent pas être envoyés.
      fields.querySelectorAll("input, select").forEach(function (el) { el.disabled = !take; });
    }
    box.addEventListener("change", refresh);
    refresh();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
