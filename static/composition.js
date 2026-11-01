/* ==========================================================================
   MenuFlash Mini — composition des plats (tableau de bord et menu public).
   Toutes les données sont insérées via textContent : aucun HTML venant de
   la base n'est jamais interprété par le navigateur.
   ========================================================================== */
(function () {
  "use strict";

  var dataEl = document.getElementById("mf-composition-data");
  if (!dataEl) return;
  var data;
  try { data = JSON.parse(dataEl.textContent); } catch (e) { return; }
  var items = data.items || {};
  var labels = data.labels || {};

  function lang() {
    try { return localStorage.getItem("mf_lang") === "en" ? "en" : "fr"; } catch (e) { return "fr"; }
  }
  function label(kind, key) {
    var entry = (labels[kind] || {})[key];
    return entry ? (entry[lang()] || entry.fr) : key;
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function chip(kind, key) {
    var c = el("span", "mf-comp-chip mf-comp-chip--" + (kind === "allergens" ? "allergen" : "tag"));
    c.appendChild(el("i", "bi " + (kind === "allergens" ? "bi-exclamation-triangle-fill" : "bi-check2")));
    c.appendChild(document.createTextNode(label(kind, key)));
    return c;
  }
  function splitIngredients(raw) {
    var seen = {}, out = [];
    String(raw || "").split(/[\n\r,;]+/).forEach(function (part) {
      var t = part.replace(/\s+/g, " ").trim();
      if (t && !seen[t.toLowerCase()]) { seen[t.toLowerCase()] = 1; out.push(t); }
    });
    return out;
  }

  /* ----------------------------- Menu public ----------------------------- */
  function renderPublic() {
    document.querySelectorAll(".mf-qty-group[data-item-id]").forEach(function (group) {
      var comp = items[group.dataset.itemId];
      var card = group.closest(".card-body");
      if (!card) return;
      var info = group.parentElement.querySelector(".flex-grow-1") || card;
      var box = info.querySelector(".mf-item-composition");
      if (box) box.remove();
      if (!comp) return;

      box = el("div", "mf-item-composition");
      if (comp.ingredients && comp.ingredients.length) {
        var p = el("p", "mf-comp-ingredients");
        p.appendChild(el("strong", null, ((labels.ui || {})[lang()] || {}).composition + " : "));
        p.appendChild(document.createTextNode(comp.ingredients.join(", ")));
        box.appendChild(p);
      }
      if ((comp.allergens || []).length || (comp.tags || []).length) {
        var chips = el("div", "mf-comp-chips");
        (comp.allergens || []).forEach(function (k) { chips.appendChild(chip("allergens", k)); });
        (comp.tags || []).forEach(function (k) { chips.appendChild(chip("tags", k)); });
        box.appendChild(chips);
      }
      info.appendChild(box);
    });
  }

  /* --------------------------- Tableau de bord --------------------------- */
  function renderAdmin() {
    var modalEl = document.getElementById("mf-composition-modal");
    var form = document.getElementById("mf-composition-form");
    if (!modalEl || !form) return;
    var textarea = document.getElementById("mf-composition-ingredients");
    var preview = document.getElementById("mf-composition-preview");
    var counter = document.getElementById("mf-composition-count");
    var nameEl = document.getElementById("mf-composition-name");

    function updatePreview() {
      var list = splitIngredients(textarea.value);
      counter.textContent = list.length;
      counter.parentElement.classList.toggle("text-danger", list.length > (data.max_ingredients || 30));
      preview.textContent = "";
      list.slice(0, 40).forEach(function (t) { preview.appendChild(el("span", "mf-comp-chip", t)); });
    }
    textarea.addEventListener("input", updatePreview);

    function open(itemId, itemName) {
      var comp = items[itemId] || { ingredients: [], allergens: [], tags: [] };
      form.action = "/admin/items/" + encodeURIComponent(itemId) + "/composition";
      nameEl.textContent = itemName || "";
      textarea.value = (comp.ingredients || []).join("\n");
      form.querySelectorAll('input[name="allergens"]').forEach(function (c) { c.checked = (comp.allergens || []).indexOf(c.value) !== -1; });
      form.querySelectorAll('input[name="tags"]').forEach(function (c) { c.checked = (comp.tags || []).indexOf(c.value) !== -1; });
      updatePreview();
      bootstrap.Modal.getOrCreateInstance(modalEl).show();
    }

    document.querySelectorAll(".mf-edit-item-btn[data-item-id]").forEach(function (editBtn) {
      if (editBtn.parentElement.querySelector(".mf-comp-btn")) return;
      var id = editBtn.dataset.itemId, name = editBtn.dataset.itemName || "";
      var comp = items[id];
      var btn = el("button", "btn btn-sm btn-outline-secondary border-0 mf-comp-btn" + (comp ? " has-data" : ""));
      btn.type = "button";
      btn.title = "Composition et allergènes";
      btn.setAttribute("aria-label", "Composition et allergènes de " + name);
      btn.appendChild(el("i", "bi bi-list-check"));
      btn.addEventListener("click", function () { open(id, name); });
      editBtn.insertAdjacentElement("afterend", btn);

      if (comp) {
        var cell = editBtn.closest("tr") && editBtn.closest("tr").querySelector(".fw-semibold");
        if (cell && !cell.parentElement.querySelector(".mf-comp-summary")) {
          var parts = [];
          if (comp.ingredients.length) parts.push(comp.ingredients.length + " ingrédient" + (comp.ingredients.length > 1 ? "s" : ""));
          if (comp.allergens.length) parts.push(comp.allergens.length + " allergène" + (comp.allergens.length > 1 ? "s" : ""));
          var s = el("div", "mf-comp-summary");
          s.appendChild(el("i", "bi bi-list-check me-1"));
          s.appendChild(document.createTextNode(parts.join(" · ") || "mentions renseignées"));
          cell.parentElement.appendChild(s);
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (data.mode === "public") {
      renderPublic();
      document.querySelectorAll(".mf-lang-btn").forEach(function (b) {
        b.addEventListener("click", function () { setTimeout(renderPublic, 0); });
      });
    } else if (data.mode === "admin") {
      renderAdmin();
    }
  });
})();
