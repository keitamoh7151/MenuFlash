/*
 * MenuFlash Mini — « Modifier la commande » (menu client).
 *
 * UN SEUL bouton « Modifier la commande », sur chaque carte de « Mes commandes » qui peut encore
 * être modifiée et sur la page de suivi d'une commande « Nouvelle ». Il ouvre une fenêtre :
 *   − / +       change la quantité d'un plat
 *   Retirer     enlève le plat (confirmation en deux temps)
 *   Ajouter d'autres plats   ouvre le menu en mode ajout (parcours « Ajouter des plats » existant)
 * Le serveur (order_edit.py) revérifie tout : propriétaire, statut, dernier plat, disponibilité.
 *
 * Script autonome : il ne dépend pas du contenu d'app.js, seulement de la structure des cartes
 * (.mf-myorder-card, .mf-myorder-actions, .mf-cancel-order-trigger, .mf-addto-btn) et de la page
 * de suivi (#order-status-root, #cancel-order-btn). L'ancien bouton « Ajouter des plats » des
 * cartes est masqué (il est conservé dans la page : « Ajouter d'autres plats » le déclenche).
 * Français et anglais intégrés.
 */
(function () {
  "use strict";
  if (window.__mfOrderEditLoaded) return;
  window.__mfOrderEditLoaded = true;

  var MAX_QTY = 50;
  var TEXT = {
    fr: {
      btn: "Modifier la commande", title: "Modifier ma commande",
      help: "Changez les quantités, retirez un plat ou ajoutez-en d'autres.",
      remove: "Retirer", confirm: "Confirmer ?", minus: "Une unité de moins : {name}", plus: "Une unité de plus : {name}",
      done: "Terminé", loading: "Chargement…", total: "Nouveau total", each: "{price} l'unité",
      addMore: "Ajouter d'autres plats", added: "{qty} × {name} ajouté.", removed: "{qty} × {name} retiré de votre commande.",
      last: "C'est le dernier plat de votre commande. Pour le retirer, annulez la commande.", cancel: "Annuler la commande",
      locked: "Trop tard : la cuisine a déjà commencé la préparation.", stale: "La commande a changé : la liste a été mise à jour.",
      error: "Impossible de modifier la commande. Vérifiez votre connexion et réessayez.",
      notmine: "Cette commande n'a pas été passée depuis cet appareil.",
      noAdd: "Impossible d'ouvrir le menu pour ajouter des plats. Commandez ces plats dans une nouvelle commande."
    },
    en: {
      btn: "Edit order", title: "Edit my order",
      help: "Change quantities, remove an item or add more.",
      remove: "Remove", confirm: "Confirm?", minus: "One less: {name}", plus: "One more: {name}",
      done: "Done", loading: "Loading…", total: "New total", each: "{price} each",
      addMore: "Add more items", added: "{qty} × {name} added.", removed: "{qty} × {name} removed from your order.",
      last: "This is the last item of your order. To remove it, cancel the order.", cancel: "Cancel the order",
      locked: "Too late: the kitchen has already started preparing it.", stale: "The order changed: the list was updated.",
      error: "Could not change the order. Check your connection and try again.",
      notmine: "This order was not placed from this device.",
      noAdd: "Could not open the menu to add items. Please place these items as a new order."
    }
  };

  function lang() {
    var b = document.querySelector(".mf-lang-btn.active");
    var l = b && b.getAttribute("data-lang");
    if (l === "fr" || l === "en") return l;
    return ((document.documentElement.lang || "fr").slice(0, 2) === "en") ? "en" : "fr";
  }
  function t(key, vars) {
    var s = (TEXT[lang()] || TEXT.fr)[key] || TEXT.fr[key] || key;
    Object.keys(vars || {}).forEach(function (k) { s = s.split("{" + k + "}").join(vars[k]); });
    return s;
  }
  function pathSlug() {
    var m = location.pathname.match(/^\/m\/([^\/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }
  function slug() {
    var f = document.getElementById("order-form");
    return (f && f.getAttribute("data-slug")) || pathSlug();
  }
  function currency() {
    var f = document.getElementById("order-form");
    return (f && f.getAttribute("data-currency")) || "";
  }
  function csrf() {
    var m = document.querySelector('meta[name="csrf-token"]');
    return m ? m.getAttribute("content") : "";
  }
  function money(n) {
    var s;
    try { s = new Intl.NumberFormat(lang() === "en" ? "en-US" : "fr-FR").format(n); } catch (e) { s = String(n); }
    return currency() ? s + " " + currency() : s;
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;          // textContent : jamais de HTML venant du menu
    return e;
  }

  /* ------------------------------------------------------------------ API */
  function api(orderId, path, body) {
    var url = "/m/" + encodeURIComponent(slug()) + "/commande/" + orderId + path;
    var opts = { cache: "no-store", headers: { "X-Requested-With": "fetch" } };
    if (body) {
      opts.method = "POST";
      opts.headers["X-CSRFToken"] = csrf();
      var fd = new URLSearchParams();
      Object.keys(body).forEach(function (k) { fd.append(k, body[k]); });
      opts.body = fd;
      opts.headers["Content-Type"] = "application/x-www-form-urlencoded";
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { d.__status = r.status; return d; });
    });
  }

  /* ------------------------------------------------------------------ petit message hors fenêtre */
  function flash(message) {
    var box = el("div", "alert alert-warning shadow-sm mf-oe-flash", message);
    box.setAttribute("role", "alert");
    document.body.appendChild(box);
    setTimeout(function () { box.remove(); }, 6000);
  }

  /* ------------------------------------------------------------------ fenêtre */
  var CSS = ".mf-oe-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 12px;padding:10px 0;border-bottom:1px solid var(--mf-border,#E7E4DD)}" +
    ".mf-oe-info{min-width:0;flex:1 1 140px}.mf-oe-name{font-weight:600;overflow-wrap:anywhere}" +
    ".mf-oe-actions{display:flex;align-items:center;gap:6px;flex-shrink:0}" +
    ".mf-oe-step{display:inline-flex;align-items:center;gap:2px;border:1px solid var(--mf-border,#E7E4DD);border-radius:999px;padding:2px}" +
    ".mf-oe-step .btn{width:36px;height:36px;border-radius:50%;padding:0;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:1.1rem;line-height:1}" +
    ".mf-oe-qty{min-width:30px;text-align:center;font-weight:700;font-variant-numeric:tabular-nums}" +
    ".mf-oe-remove{min-height:40px}.mf-oe-total{font-weight:700;color:var(--mf-accent-dark,#B5730F)}" +
    ".mf-oe-more{margin-top:12px}.mf-oe-last{padding:10px 12px;border-radius:10px;background:var(--mf-bg,#F7F6F3)}" +
    ".mf-myorder-actions .mf-myorder-addbtn{display:none!important}" +
    ".mf-oe-flash{position:fixed;left:12px;right:12px;bottom:12px;z-index:2000;max-width:520px;margin:0 auto}" +
    "@media (max-width:575.98px){.mf-oe-modal .modal-dialog{position:fixed;left:0;right:0;bottom:0;margin:0;max-width:none;" +
    "transform:translateY(100%);transition:transform .25s ease-out}.mf-oe-modal.show .modal-dialog{transform:none}" +
    ".mf-oe-modal .modal-content{border-radius:20px 20px 0 0;max-height:92vh}.mf-oe-modal .modal-footer{padding-bottom:max(12px,env(safe-area-inset-bottom))}}";

  var modalEl = null, modal = null, state = null;

  function injectCss() {
    if (document.getElementById("mf-oe-style")) return;
    var st = document.createElement("style");
    st.id = "mf-oe-style"; st.textContent = CSS;
    document.head.appendChild(st);
  }

  function ensureModal() {
    if (modalEl) return;
    injectCss();
    modalEl = el("div", "modal fade mf-oe-modal");
    modalEl.tabIndex = -1;
    modalEl.setAttribute("aria-hidden", "true");
    modalEl.setAttribute("aria-labelledby", "mf-oe-title");
    var dialog = el("div", "modal-dialog modal-dialog-centered modal-dialog-scrollable");
    var content = el("div", "modal-content");
    var head = el("div", "modal-header border-0 pb-1");
    var titleBox = el("div");
    var title = el("h5", "modal-title", "");
    title.id = "mf-oe-title";
    var help = el("div", "small text-secondary", "");
    help.setAttribute("data-oe", "help");
    titleBox.appendChild(title); titleBox.appendChild(help);
    var closeX = el("button", "btn-close");
    closeX.type = "button";
    closeX.setAttribute("data-bs-dismiss", "modal");
    head.appendChild(titleBox); head.appendChild(closeX);
    var body = el("div", "modal-body pt-2");
    var notice = el("div", "small mb-2 d-none");
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    notice.setAttribute("data-oe", "notice");
    var list = el("div", "mf-oe-list");
    list.setAttribute("data-oe", "list");
    var more = el("button", "btn btn-outline-primary w-100 mf-oe-more");
    more.type = "button";
    more.setAttribute("data-oe", "more");
    more.addEventListener("click", function () { addMore(); });
    var foot = el("div", "modal-footer border-0 d-flex justify-content-between align-items-center");
    var total = el("div", "mf-oe-total");
    total.setAttribute("data-oe", "total");
    var close = el("button", "btn btn-primary");
    close.type = "button";
    close.setAttribute("data-bs-dismiss", "modal");
    close.setAttribute("data-oe", "close");
    foot.appendChild(total); foot.appendChild(close);
    body.appendChild(notice); body.appendChild(list); body.appendChild(more);
    content.appendChild(head); content.appendChild(body); content.appendChild(foot);
    dialog.appendChild(content); modalEl.appendChild(dialog);
    document.body.appendChild(modalEl);
    modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    // Le focus doit entrer dans la fenêtre (clavier, lecteur d'écran, touche Échap), même ouverte
    // depuis le panneau « Mes commandes » qui a son propre piège à focus.
    modalEl.addEventListener("shown.bs.modal", function () { modalEl.focus(); });
    modalEl.addEventListener("hidden.bs.modal", function () {
      if (state && state.changed && state.onChanged) state.onChanged();
      state = null;
    });
  }
  function q(name) { return modalEl.querySelector('[data-oe="' + name + '"]'); }

  function setNotice(message, kind) {
    var n = q("notice");
    n.textContent = message || "";
    n.className = "small mb-2 " + (message ? "alert py-2 alert-" + (kind || "success") : "d-none");
  }

  function render() {
    modalEl.querySelector(".modal-title").textContent = t("title");
    q("help").textContent = t("help");
    q("close").textContent = t("done");
    q("more").textContent = "";
    var plusIcon = el("i", "bi bi-plus-lg me-1"); plusIcon.setAttribute("aria-hidden", "true");
    q("more").appendChild(plusIcon); q("more").appendChild(document.createTextNode(t("addMore")));
    q("more").classList.toggle("d-none", !state || !state.lines || !state.lines.length);
    q("more").disabled = !!(state && state.busy);
    var list = q("list");
    list.textContent = "";
    if (!state || !state.lines) { list.appendChild(el("p", "text-secondary small mb-0", t("loading"))); return; }
    var onlyOne = state.lines.length <= 1;
    state.lines.forEach(function (line) {
      var row = el("div", "mf-oe-row");
      row.setAttribute("data-line-id", line.id);
      var info = el("div", "mf-oe-info");
      info.appendChild(el("div", "mf-oe-name", line.name));
      info.appendChild(el("div", "small text-secondary", t("each", { price: money(line.price) })));
      var actions = el("div", "mf-oe-actions");
      var step = el("div", "mf-oe-step");
      var minus = el("button", "btn btn-light mf-oe-minus", "−");
      minus.type = "button";
      minus.disabled = line.quantity <= 1;                       // à 1 : « Retirer » (ou annuler s'il n'y a plus que ce plat)
      minus.setAttribute("aria-label", t("minus", { name: line.name }));
      minus.addEventListener("click", function () { changeLine(line, line.quantity - 1); });
      var qty = el("span", "mf-oe-qty", String(line.quantity));
      qty.setAttribute("aria-live", "polite");
      var plus = el("button", "btn btn-light mf-oe-plus", "+");
      plus.type = "button";
      plus.disabled = !line.can_increase || line.quantity >= MAX_QTY;
      plus.setAttribute("aria-label", t("plus", { name: line.name }));
      plus.addEventListener("click", function () { changeLine(line, line.quantity + 1); });
      step.appendChild(minus); step.appendChild(qty); step.appendChild(plus);
      actions.appendChild(step);
      if (!onlyOne) {
        var rm = el("button", "btn btn-outline-danger btn-sm mf-oe-remove", t("remove"));
        rm.type = "button";
        var armed = null;
        rm.addEventListener("click", function () {
          if (!armed) {                                            // 1er appui : on demande confirmation
            rm.textContent = t("confirm");
            rm.classList.remove("btn-outline-danger"); rm.classList.add("btn-danger");
            armed = setTimeout(function () {
              armed = null; rm.textContent = t("remove");
              rm.classList.add("btn-outline-danger"); rm.classList.remove("btn-danger");
            }, 4000);
            return;
          }
          clearTimeout(armed); armed = null;
          changeLine(line, 0);
        });
        actions.appendChild(rm);
      }
      row.appendChild(info); row.appendChild(actions);
      list.appendChild(row);
    });
    if (onlyOne) {                                                 // dernier plat : seule l'annulation le retire
      var last = el("div", "mf-oe-last small mt-2");
      last.appendChild(el("div", "text-secondary mb-2", t("last")));
      var cancel = el("button", "btn btn-danger btn-sm", t("cancel"));
      cancel.type = "button";
      cancel.addEventListener("click", function () { cancelOrder(); });
      last.appendChild(cancel);
      list.appendChild(last);
    }
    q("total").textContent = t("total") + " : " + money(state.total || 0);
    setBusy(state.busy);
  }

  function setBusy(busy) {
    modalEl.querySelectorAll(".mf-oe-minus, .mf-oe-plus, .mf-oe-remove, [data-oe=more]").forEach(function (b) {
      if (busy) { b.setAttribute("data-was-disabled", b.disabled ? "1" : "0"); b.disabled = true; }
    });
    modalEl.querySelector(".mf-oe-list").setAttribute("aria-busy", busy ? "true" : "false");
  }

  function applyData(d) {
    if (d.lines) { state.lines = d.lines; }
    if (typeof d.total === "number") { state.total = d.total; }
    if (d.menu_url) { state.menuUrl = d.menu_url; }
  }

  function changeLine(line, newQty) {
    if (!state || state.busy) return;
    state.busy = true; setBusy(true); setNotice("");
    api(state.orderId, "/modifier", { line_id: line.id, quantity: newQty, expected: line.quantity }).then(function (d) {
      if (!state) return;
      state.busy = false;
      if (d.ok) {
        applyData(d);
        state.changed = true;
        updateHost(state.orderId, d);
        var delta = d.changed.delta;
        setNotice(delta > 0 ? t("added", { qty: delta, name: d.changed.name })
                            : t("removed", { qty: -delta, name: d.changed.name }), "success");
      } else if (d.locked) {
        setNotice(t("locked"), "danger");
        state.changed = true;
        state.lines = []; state.total = 0;
        setTimeout(function () { if (modal) modal.hide(); }, 1800);
      } else if (d.stale || d.last_item || d.refused) {
        applyData(d);
        setNotice(d.last_item ? t("last") : (d.refused ? d.error : t("stale")), d.refused ? "danger" : "warning");
      } else if (d.__status === 404) {
        setNotice(t("notmine"), "danger");
      } else {
        setNotice(d.error || t("error"), "danger");
      }
      render();
    }).catch(function () {
      if (!state) return;
      state.busy = false; setNotice(t("error"), "danger"); render();
    });
  }

  // « Annuler la commande » : on réutilise le bouton existant (même fenêtre de confirmation).
  // Le bouton est cherché AU DERNIER MOMENT : « Mes commandes » reconstruit ses cartes régulièrement.
  function findCancelButton(id) {
    return document.querySelector('.mf-cancel-order-trigger[data-order-id="' + id + '"]') ||
           document.getElementById("cancel-order-btn");
  }
  function cancelOrder() {
    var id = state && state.orderId;
    if (!findCancelButton(id)) return;
    state.changed = false;
    modalEl.addEventListener("hidden.bs.modal", function once() {
      modalEl.removeEventListener("hidden.bs.modal", once);
      var btn = findCancelButton(id);
      if (btn) btn.click();
    });
    modal.hide();
  }

  /* ------------------------------------------------------------------ « Ajouter d'autres plats » */
  function findAddButton(id) {
    return document.querySelector('.mf-addto-btn[data-order-id="' + id + '"]');
  }
  function rememberOrderOnThisDevice(id) {
    // Le panneau « Mes commandes » est construit depuis cette liste (app.js) : sans elle, la commande
    // ouverte depuis un lien de suivi n'y apparaîtrait pas et le mode ajout serait introuvable.
    try {
      var key = "mf_orders_" + slug();
      var ids = JSON.parse(localStorage.getItem(key) || "[]");
      if (!Array.isArray(ids)) ids = [];
      if (ids.indexOf(id) === -1) { ids.push(id); localStorage.setItem(key, JSON.stringify(ids)); }
    } catch (e) { /* stockage indisponible : on tente quand même */ }
  }
  function addMore() {
    if (!state || state.busy) return;
    var id = state.orderId, url = state.menuUrl;
    var onMenu = !!findAddButton(id);
    if (!onMenu && url) state.onChanged = null;                    // on quitte la page : inutile de la recharger avant
    modalEl.addEventListener("hidden.bs.modal", function once() {
      modalEl.removeEventListener("hidden.bs.modal", once);
      var btn = findAddButton(id);
      if (btn) { btn.click(); return; }                             // menu déjà affiché : mode ajout existant
      if (url) { rememberOrderOnThisDevice(id); location.href = url; return; }   // page de suivi : on ouvre le menu
      flash(t("noAdd"));
    });
    modal.hide();
  }

  // Arrivée sur le menu avec ?ajouter=<id> : on déclenche le mode ajout de cette commande.
  function autoAddMode() {
    var m = location.search.match(/[?&]ajouter=(\d+)/);
    if (!m) return;
    var id = m[1], tries = 0;
    function strip() {
      try {
        var u = new URL(location.href); u.searchParams.delete("ajouter");
        history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
      } catch (e) {}
    }
    var timer = setInterval(function () {
      var btn = findAddButton(id);
      if (btn) { clearInterval(timer); strip(); btn.click(); return; }
      if (++tries > 40) { clearInterval(timer); strip(); flash(t("noAdd")); }     // 40 × 250 ms = 10 s
    }, 250);
  }

  function open(orderId, onChanged) {
    if (typeof bootstrap === "undefined") return;
    ensureModal();
    state = { orderId: orderId, lines: null, total: 0, busy: false, changed: false, onChanged: onChanged, menuUrl: null };
    setNotice("");
    render();
    modal.show();
    api(orderId, "/lignes.json").then(function (d) {
      if (!state || state.orderId !== orderId) return;
      if (d.ok && d.editable) { applyData(d); }
      else if (d.ok) { setNotice(t("locked"), "danger"); state.lines = []; setTimeout(function () { if (modal) modal.hide(); }, 1800); }
      else { setNotice(d.__status === 404 ? t("notmine") : t("error"), "danger"); state.lines = []; }
      render();
    }).catch(function () {
      if (!state) return;
      setNotice(t("error"), "danger"); state.lines = []; render();
    });
  }

  /* ------------------------------------------------------------------ mise à jour de la page derrière la fenêtre */
  function updateHost(orderId, d) {
    var card = document.querySelector('.mf-myorder-card[data-order-id="' + orderId + '"]');
    if (!card || !d.items) return;
    var ul = card.querySelector(".mf-myorder-items");
    if (ul) {
      ul.textContent = "";
      d.items.forEach(function (it) { ul.appendChild(el("li", null, it.quantity + " × " + it.name)); });
    }
    var tot = card.querySelector(".mf-myorder-total");
    if (tot) tot.textContent = money(d.total);
  }

  /* ------------------------------------------------------------------ boutons */
  function buttonLabel(button) {
    // On ne réécrit le libellé que s'il a changé (changement de langue) : modifier la page à chaque
    // passage relancerait l'observateur de changements en boucle.
    var wanted = t("btn");
    if (button.getAttribute("data-oe-label") === wanted) return;
    var icon = el("i", "bi bi-pencil-square me-1");
    icon.setAttribute("aria-hidden", "true");
    button.textContent = "";
    button.appendChild(icon);
    button.appendChild(document.createTextNode(wanted));
    button.setAttribute("data-oe-label", wanted);
  }

  function addToCards() {
    document.querySelectorAll(".mf-myorder-card").forEach(function (card) {
      var actions = card.querySelector(".mf-myorder-actions");
      var cancel = card.querySelector(".mf-cancel-order-trigger");
      var existing = card.querySelector(".mf-myorder-editbtn");
      if (!actions || !cancel) { if (existing) existing.remove(); return; }      // plus modifiable
      if (existing) { buttonLabel(existing); return; }
      var id = parseInt(card.getAttribute("data-order-id"), 10);
      if (isNaN(id)) return;
      var b = el("button", "btn btn-primary btn-sm mf-myorder-editbtn");
      b.type = "button";
      buttonLabel(b);
      b.addEventListener("click", function () { open(id); });
      actions.insertBefore(b, cancel);
    });
  }

  function addToTracking() {
    var root = document.getElementById("order-status-root");
    var cancel = document.getElementById("cancel-order-btn");
    if (!root) return;
    var existing = document.getElementById("mf-oe-track-btn");
    var editable = root.getAttribute("data-status") === "nouvelle" && cancel && !cancel.classList.contains("d-none");
    if (!editable) { if (existing) existing.remove(); return; }
    if (existing) { buttonLabel(existing); return; }
    var m = (root.getAttribute("data-cancel-url") || "").match(/\/commande\/(\d+)\//);
    if (!m) return;
    var id = parseInt(m[1], 10);
    var b = el("button", "btn btn-primary btn-sm me-2");
    b.id = "mf-oe-track-btn"; b.type = "button";
    buttonLabel(b);
    b.addEventListener("click", function () { open(id, function () { location.reload(); }); });
    cancel.parentNode.insertBefore(b, cancel);
  }

  function scan() { addToCards(); addToTracking(); }

  function init() {
    injectCss();
    scan();
    autoAddMode();
    var host = document.getElementById("my-orders-offcanvas") || document.body;
    var pending = false;
    new MutationObserver(function () {
      if (pending) return;
      pending = true;
      setTimeout(function () { pending = false; scan(); }, 50);             // regroupe les rafraîchissements
    }).observe(host, { childList: true, subtree: true });
    var root = document.getElementById("order-status-root");
    if (root) new MutationObserver(scan).observe(root, { attributes: true, attributeFilter: ["data-status"], subtree: true, childList: true });
    document.querySelectorAll(".mf-lang-btn").forEach(function (b) { b.addEventListener("click", function () { setTimeout(scan, 0); }); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
