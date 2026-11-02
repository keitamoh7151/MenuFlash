/* ==========================================================================
   MenuFlash Mini — gestion du menu (tableau de bord du restaurateur)

   - Recherche instantanée (nom + description, sans tenir compte des accents)
   - Filtres : Tous / Épuisés / À compléter (photo, description, composition)
   - Catégories repliables (état mémorisé par restaurant)
   - « Afficher les N autres plats » au-delà de 8 plats par catégorie
   - Interrupteur Disponible / Épuisé sans rechargement de la page
   - Rappel des plats incomplets
   - Info-bulles sur les boutons icône, catégorie présélectionnée à l'ajout

   Toutes les données affichées passent par textContent (aucune injection HTML).
   ========================================================================== */
(function () {
  "use strict";

  var VISIBLE_ROWS = 8;        // plats affichés par catégorie avant « Afficher plus »
  var MORE_THRESHOLD = 10;     // « Afficher plus » seulement au-delà de ce nombre

  function norm(text) {
    return String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  }
  function toast(msg, isError) { if (window.MFShowToast) window.MFShowToast(msg, !!isError); }
  function plural(n, one, many) { return n + " " + (n > 1 ? many : one); }

  document.addEventListener("DOMContentLoaded", function () {
    var list = document.getElementById("categories-list");
    if (!list) return;

    var blocks = Array.prototype.slice.call(list.querySelectorAll(".mf-category-block"));
    var rows = Array.prototype.slice.call(list.querySelectorAll("tr.mf-item-row"));
    var search = document.getElementById("mf-menu-search");
    var result = document.getElementById("mf-search-result");
    var noResults = document.getElementById("mf-no-results");
    var noResultsText = document.getElementById("mf-no-results-text");
    var state = { query: "", filter: "all", expanded: {} };

    /* ---------------- Info-bulles ---------------- */
    if (window.bootstrap && bootstrap.Tooltip) {
      document.querySelectorAll("[data-mf-tip]").forEach(function (el) {
        el.setAttribute("title", el.dataset.mfTip);
        new bootstrap.Tooltip(el, { trigger: "hover", container: "body" });
      });
    }

    /* ---------------- Plats incomplets ---------------- */
    var compItems = {};
    try {
      var compEl = document.getElementById("mf-composition-data");
      if (compEl) compItems = JSON.parse(compEl.textContent).items || {};
    } catch (e) { compItems = {}; }

    var missingCount = { photo: 0, desc: 0, comp: 0 };
    var incompleteTotal = 0;
    rows.forEach(function (row) {
      var missing = [];
      if (row.dataset.hasPhoto !== "1") { missing.push("photo"); missingCount.photo++; }
      if (row.dataset.hasDesc !== "1") { missing.push("description"); missingCount.desc++; }
      if (!compItems[row.dataset.id]) { missing.push("composition"); missingCount.comp++; }
      row.dataset.incomplete = missing.length ? "1" : "0";
      // Le texte de recherche sans accents est préparé une seule fois.
      row.dataset.searchNorm = norm(row.dataset.search);
      if (missing.length) {
        incompleteTotal++;
        var pill = row.querySelector("[data-mf-incomplete]");
        if (pill) {
          pill.textContent = "";
          var icon = document.createElement("i");
          icon.className = "bi bi-exclamation-circle";
          pill.appendChild(icon);
          pill.appendChild(document.createTextNode("Sans " + missing.join(", ")));
          pill.classList.remove("d-none");
        }
      }
    });

    var incCount = document.getElementById("mf-count-incomplete");
    if (incCount) incCount.textContent = incompleteTotal;
    var tip = document.getElementById("mf-incomplete-tip");
    if (tip && incompleteTotal && rows.length) {
      document.getElementById("mf-incomplete-title").textContent =
        plural(incompleteTotal, "plat peut", "plats peuvent") + " être amélioré" + (incompleteTotal > 1 ? "s" : "");
      var parts = [];
      if (missingCount.photo) parts.push(plural(missingCount.photo, "sans photo", "sans photo"));
      if (missingCount.desc) parts.push(plural(missingCount.desc, "sans description", "sans description"));
      if (missingCount.comp) parts.push(plural(missingCount.comp, "sans composition", "sans composition"));
      document.getElementById("mf-incomplete-detail").textContent =
        parts.join(" · ") + ". Un menu complet rassure les clients et fait vendre davantage.";
      tip.classList.remove("d-none");
    }

    /* ---------------- Repli des catégories ---------------- */
    var storeKey = "mf_collapsed_" + (list.dataset.restaurant || "0");
    var collapsed = {};
    try { collapsed = JSON.parse(localStorage.getItem(storeKey) || "{}") || {}; } catch (e) { collapsed = {}; }

    function saveCollapsed() {
      try { localStorage.setItem(storeKey, JSON.stringify(collapsed)); } catch (e) {}
    }
    function setCollapsed(block, value) {
      block.classList.toggle("is-collapsed", value);
      var btn = block.querySelector(".mf-cat-toggle");
      if (btn) btn.setAttribute("aria-expanded", value ? "false" : "true");
      if (value) collapsed[block.dataset.id] = 1; else delete collapsed[block.dataset.id];
    }
    blocks.forEach(function (block) {
      if (collapsed[block.dataset.id]) setCollapsed(block, true);
      var btn = block.querySelector(".mf-cat-toggle");
      if (btn) btn.addEventListener("click", function () {
        setCollapsed(block, !block.classList.contains("is-collapsed"));
        saveCollapsed();
        updateToggleAll();
      });
    });

    var toggleAll = document.getElementById("mf-toggle-all");
    function updateToggleAll() {
      if (!toggleAll) return;
      var allCollapsed = blocks.length && blocks.every(function (b) { return b.classList.contains("is-collapsed"); });
      toggleAll.querySelector("span").textContent = allCollapsed ? "Tout déplier" : "Tout replier";
      toggleAll.querySelector("i").className = "bi " + (allCollapsed ? "bi-arrows-expand" : "bi-arrows-collapse") + " me-1";
    }
    if (toggleAll) {
      toggleAll.addEventListener("click", function () {
        var collapse = !blocks.every(function (b) { return b.classList.contains("is-collapsed"); });
        blocks.forEach(function (b) { setCollapsed(b, collapse); });
        saveCollapsed();
        updateToggleAll();
      });
      updateToggleAll();
    }

    /* ---------------- Filtrage, recherche et « Afficher plus » ---------------- */
    function matches(row) {
      if (state.query && row.dataset.searchNorm.indexOf(state.query) === -1) return false;
      if (state.filter === "soldout" && !row.classList.contains("is-soldout")) return false;
      if (state.filter === "incomplete" && row.dataset.incomplete !== "1") return false;
      return true;
    }

    function apply() {
      var filtering = !!state.query || state.filter !== "all";
      list.classList.toggle("mf-filtering", filtering);
      var shown = 0;

      blocks.forEach(function (block) {
        var blockRows = Array.prototype.slice.call(block.querySelectorAll("tr.mf-item-row"));
        var visible = 0;
        blockRows.forEach(function (row) {
          var ok = matches(row);
          row.classList.toggle("d-none", !ok);
          row.classList.remove("mf-row-more");
          if (ok) visible++;
        });
        shown += visible;

        // Pendant une recherche, on cache les catégories sans résultat.
        block.classList.toggle("d-none", filtering && visible === 0);

        // « Afficher plus » : seulement hors recherche/filtre.
        var more = block.querySelector(".mf-show-more");
        if (!more) return;
        var hiddenRows = [];
        if (!filtering && blockRows.length > MORE_THRESHOLD && !state.expanded[block.dataset.id]) {
          hiddenRows = blockRows.slice(VISIBLE_ROWS);
          hiddenRows.forEach(function (row) { row.classList.add("mf-row-more"); });
        }
        more.classList.toggle("d-none", hiddenRows.length === 0);
        if (hiddenRows.length) {
          more.querySelector("button").textContent = "Afficher les " + hiddenRows.length + " autres plats";
        }
      });

      if (result) {
        result.classList.toggle("d-none", !filtering || shown === 0);
        result.textContent = filtering ? plural(shown, "plat trouvé", "plats trouvés") : "";
      }
      if (noResults) {
        noResults.classList.toggle("d-none", !(filtering && shown === 0));
        noResultsText.textContent = state.query
          ? "Aucun plat ne correspond à « " + search.value.trim() + " »."
          : "Aucun plat dans ce filtre.";
      }
    }

    blocks.forEach(function (block) {
      var more = block.querySelector(".mf-show-more button");
      if (more) more.addEventListener("click", function () {
        state.expanded[block.dataset.id] = true;
        apply();
      });
    });

    if (search) {
      var timer = null;
      search.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () { state.query = norm(search.value); apply(); }, 120);
      });
      search.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { search.value = ""; state.query = ""; apply(); }
      });
      // Raccourci « / » pour aller dans la recherche.
      document.addEventListener("keydown", function (e) {
        var tag = (e.target && e.target.tagName) || "";
        if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(tag) && !document.querySelector(".modal.show")) {
          e.preventDefault();
          search.focus();
        }
      });
    }

    function setFilter(value) {
      state.filter = value;
      document.querySelectorAll("[data-mf-filter]").forEach(function (chip) {
        chip.classList.toggle("is-active", chip.dataset.mfFilter === value);
        chip.setAttribute("aria-pressed", chip.dataset.mfFilter === value ? "true" : "false");
      });
      apply();
    }
    document.querySelectorAll("[data-mf-filter]").forEach(function (chip) {
      chip.addEventListener("click", function () { setFilter(chip.dataset.mfFilter); });
    });
    document.querySelectorAll("[data-mf-filter-shortcut]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        setFilter(btn.dataset.mfFilterShortcut);
        var toolbar = document.querySelector(".mf-menu-toolbar");
        if (toolbar) toolbar.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
    var clear = document.getElementById("mf-clear-filters");
    if (clear) clear.addEventListener("click", function () {
      if (search) search.value = "";
      state.query = "";
      setFilter("all");
    });

    /* ---------------- Disponible / Épuisé sans rechargement ---------------- */
    var soldoutCount = document.getElementById("mf-count-soldout");
    function refreshSoldoutCount() {
      if (soldoutCount) soldoutCount.textContent = list.querySelectorAll("tr.mf-item-row.is-soldout").length;
    }
    function renderAvailability(row, available) {
      var input = row.querySelector(".mf-avail-switch");
      var label = row.querySelector(".mf-avail-label");
      if (input) input.checked = available;
      if (label) label.textContent = available ? "Disponible" : "Épuisé";
      row.classList.toggle("is-soldout", !available);
      refreshSoldoutCount();
    }

    list.querySelectorAll(".mf-avail-form").forEach(function (form) {
      var input = form.querySelector(".mf-avail-switch");
      var row = form.closest("tr");
      if (!input || !row) return;
      form.addEventListener("submit", function (e) { e.preventDefault(); });
      input.addEventListener("change", function () {
        var wanted = input.checked;
        input.disabled = true;
        renderAvailability(row, wanted);
        fetch(form.action, {
          method: "POST",
          headers: { "X-Requested-With": "fetch" },
          body: new FormData(form)
        })
          .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function (data) {
            renderAvailability(row, !!data.is_available);
            var name = (row.querySelector(".mf-item-name") || {}).textContent || "Plat";
            toast(name + (data.is_available ? " est de nouveau disponible." : " est marqué épuisé : il est masqué aux clients."), false);
            if (state.filter === "soldout") apply();
          })
          .catch(function () {
            renderAvailability(row, !wanted);
            toast("Impossible de changer la disponibilité. Vérifiez votre connexion.", true);
          })
          .finally(function () { input.disabled = false; });
      });
    });

    /* ---------------- Ajout d'un plat : catégorie présélectionnée ---------------- */
    var addModal = document.getElementById("mf-add-item-modal");
    if (addModal) {
      addModal.addEventListener("show.bs.modal", function (e) {
        var trigger = e.relatedTarget;
        var select = document.getElementById("add-item-category");
        if (trigger && trigger.dataset.categoryId && select) select.value = trigger.dataset.categoryId;
      });
      addModal.addEventListener("shown.bs.modal", function () {
        var name = document.getElementById("add-item-name");
        if (name) name.focus();
      });
    }
    var addCat = document.getElementById("mf-add-category-modal");
    if (addCat) addCat.addEventListener("shown.bs.modal", function () {
      var input = document.getElementById("add-category-name");
      if (input) input.focus();
    });

    apply();
  });
})();
