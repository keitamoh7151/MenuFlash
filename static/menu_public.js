/* ==========================================================================
   MenuFlash Mini — menu client : recherche, « Les plus commandés »,
   « Voir les autres plats » et badge « Populaire ».

   - Le bouton + d'un plat populaire déclenche le vrai bouton + du plat dans
     le menu : le panier, le récapitulatif et les suggestions fonctionnent
     exactement comme d'habitude (aucune logique de commande dupliquée).
   - Textes en français ou en anglais selon la langue choisie par le client.
   - Tout le contenu est inséré par textContent (aucune injection HTML).
   ========================================================================== */
(function () {
  "use strict";

  var VISIBLE_ITEMS = 6;       // plats affichés par catégorie avant « Voir plus »
  var MORE_THRESHOLD = 8;      // « Voir plus » seulement au-delà de ce nombre

  var TEXT = {
    fr: { search: "Rechercher un plat…", popular: "Les plus commandés", badge: "Populaire",
          more: function (n) { return "Voir les " + n + " autres plats"; },
          none: function (q) { return "Aucun plat ne correspond à « " + q + " »."; },
          add: function (name) { return "Ajouter " + name; }, clear: "Effacer la recherche" },
    en: { search: "Search a dish…", popular: "Most ordered", badge: "Popular",
          more: function (n) { return "See " + n + " more dishes"; },
          none: function (q) { return "No dish matches “" + q + "”."; },
          add: function (name) { return "Add " + name; }, clear: "Clear search" }
  };
  function lang() { try { return localStorage.getItem("mf_lang") === "en" ? "en" : "fr"; } catch (e) { return "fr"; } }
  function t(key) { return TEXT[lang()][key]; }
  function norm(text) {
    return String(text || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("order-form");
    if (!form) return;

    var sections = Array.prototype.slice.call(document.querySelectorAll(".mf-menu-section"));
    var cards = Array.prototype.slice.call(document.querySelectorAll(".mf-item-card[data-mf-item]"));
    cards.forEach(function (card) { card.dataset.searchNorm = norm(card.dataset.search); });

    var search = document.getElementById("mf-pub-search");
    var clearBtn = document.querySelector(".mf-pub-search-clear");
    var noResult = document.getElementById("mf-pub-noresult");
    var popular = document.getElementById("mf-popular");
    var track = document.getElementById("mf-popular-track");
    var expanded = {};
    var query = "";

    function cardById(id) { return document.querySelector('.mf-item-card[data-mf-item="' + id + '"]'); }

    /* ---------------- « Les plus commandés » ---------------- */
    var popularIds = [];
    try { popularIds = JSON.parse((document.getElementById("mf-popular-data") || {}).textContent || "[]"); }
    catch (e) { popularIds = []; }
    var popularTiles = [];

    function buildPopular() {
      if (!popular || !track || !popularIds.length) return;
      popularIds.forEach(function (id) {
        var card = cardById(id);
        if (!card) return;
        var group = card.querySelector(".mf-qty-group");
        var plus = card.querySelector(".mf-qty-plus");
        var nameText = (card.querySelector("h3") || {}).textContent || "";
        var priceText = (card.querySelector(".mf-item-price") || {}).textContent || "";

        // Badge « Populaire » sur le plat dans le menu.
        var title = card.querySelector("h3");
        if (title && !title.querySelector(".mf-pop-badge")) {
          title.appendChild(el("span", "mf-pop-badge", t("badge")));
        }

        var tile = el("div", "mf-pop-tile");
        tile.setAttribute("role", "button");
        tile.tabIndex = 0;
        var img = card.querySelector("img.mf-item-img");
        var media = el("div", "mf-pop-media");
        if (img) {
          var i = document.createElement("img");
          i.src = img.getAttribute("src");
          i.alt = "";
          i.loading = "lazy";
          media.appendChild(i);
        } else {
          media.appendChild(el("i", "bi bi-egg-fried"));
        }
        var qty = el("span", "mf-pop-qty d-none");
        media.appendChild(qty);
        tile.appendChild(media);
        tile.appendChild(el("div", "mf-pop-name", nameText.replace(t("badge"), "").trim()));
        var foot = el("div", "mf-pop-foot");
        foot.appendChild(el("span", "mf-pop-price", priceText.trim()));
        var add = el("button", "mf-pop-add");
        add.type = "button";
        add.setAttribute("aria-label", t("add")(nameText.trim()));
        add.appendChild(el("i", "bi bi-plus-lg"));
        foot.appendChild(add);
        tile.appendChild(foot);
        track.appendChild(tile);

        function syncQty() {
          var v = parseInt(((group && group.querySelector(".mf-qty-value")) || {}).textContent, 10) || 0;
          qty.textContent = v;
          qty.classList.toggle("d-none", v === 0);
        }
        add.addEventListener("click", function (e) {
          e.stopPropagation();
          if (plus) plus.click();
          syncQty();
          add.classList.remove("mf-pop");
          void add.offsetWidth;
          add.classList.add("mf-pop");
        });
        function goToCard() {
          card.scrollIntoView({ behavior: "smooth", block: "center" });
          card.classList.remove("mf-highlight");
          void card.offsetWidth;
          card.classList.add("mf-highlight");
        }
        tile.addEventListener("click", goToCard);
        tile.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); goToCard(); } });
        // La tuile suit en permanence la quantité du plat dans le menu (ajout,
        // retrait, panier vidé après l'envoi de la commande…).
        var valueEl = group && group.querySelector(".mf-qty-value");
        if (valueEl && window.MutationObserver) {
          new MutationObserver(syncQty).observe(valueEl, { childList: true, characterData: true, subtree: true });
        }
        popularTiles.push({ syncQty: syncQty, add: add, name: nameText.trim() });
      });
      if (popularTiles.length) popular.classList.remove("d-none");
    }

    /* ---------------- « Voir les autres plats » ---------------- */
    function setupMore(section) {
      var list = Array.prototype.slice.call(section.querySelectorAll(".mf-item-card[data-mf-item]"));
      if (list.length <= MORE_THRESHOLD) return;
      var btn = el("button", "btn mf-pub-more");
      btn.type = "button";
      section.appendChild(btn);
      section._more = { cards: list.slice(VISIBLE_ITEMS), btn: btn };
      btn.addEventListener("click", function () {
        expanded[section.id] = true;
        apply();
      });
    }

    /* ---------------- Recherche et affichage ---------------- */
    function apply() {
      var searching = !!query;
      var shown = 0;
      sections.forEach(function (section) {
        var visible = 0;
        section.querySelectorAll(".mf-item-card[data-mf-item]").forEach(function (card) {
          var ok = !searching || card.dataset.searchNorm.indexOf(query) !== -1;
          card.classList.toggle("d-none", !ok);
          card.classList.remove("mf-pub-hidden");
          if (ok) visible++;
        });
        shown += visible;
        section.classList.toggle("d-none", searching && visible === 0);
        var more = section._more;
        if (more) {
          var collapse = !searching && !expanded[section.id];
          more.cards.forEach(function (card) { card.classList.toggle("mf-pub-hidden", collapse); });
          more.btn.classList.toggle("d-none", !collapse);
          more.btn.textContent = t("more")(more.cards.length);
        }
      });
      if (popular && popularTiles.length) popular.classList.toggle("d-none", searching);
      if (noResult) {
        noResult.classList.toggle("d-none", !(searching && shown === 0));
        noResult.textContent = searching ? t("none")(search.value.trim()) : "";
      }
      if (clearBtn) clearBtn.classList.toggle("d-none", !searching);
    }

    function translate() {
      if (search) search.placeholder = t("search");
      if (clearBtn) clearBtn.setAttribute("aria-label", t("clear"));
      var title = document.querySelector("#mf-popular-title span");
      if (title) title.textContent = t("popular");
      document.querySelectorAll(".mf-pop-badge").forEach(function (b) { b.textContent = t("badge"); });
      popularTiles.forEach(function (p) { p.add.setAttribute("aria-label", t("add")(p.name)); });
      apply();
    }

    if (search) {
      var timer = null;
      search.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () { query = norm(search.value); apply(); }, 120);
      });
      search.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); search.blur(); }
        if (e.key === "Escape") { search.value = ""; query = ""; apply(); }
      });
    }
    if (clearBtn) clearBtn.addEventListener("click", function () {
      search.value = ""; query = ""; apply(); search.focus();
    });

    // Un clic sur une catégorie annule la recherche pour afficher tout le menu.
    document.querySelectorAll(".mf-nav-pills .nav-link").forEach(function (link) {
      link.addEventListener("click", function () {
        if (query) { search.value = ""; query = ""; apply(); }
      });
    });

    buildPopular();
    sections.forEach(setupMore);
    document.querySelectorAll(".mf-lang-btn").forEach(function (b) {
      b.addEventListener("click", function () { setTimeout(translate, 0); });
    });
    translate();
  });
})();
