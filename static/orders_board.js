/* ==========================================================================
   MenuFlash Mini — écran des commandes (cuisine) et historique.

   - Tableau en direct (mise à jour toutes les 4 s, et instantanément grâce
     au temps réel déjà présent dans app.js : son + notification).
   - Temps d'attente coloré, notes du client bien visibles, table en grand.
   - Annulation avec confirmation (plus d'annulation par erreur).
   - Bouton « Activer le son » : les navigateurs bloquent le son tant qu'on
     n'a pas touché la page.
   - Mode cuisine : plein écran, texte agrandi, écran toujours allumé.
   - Sur téléphone : onglets Nouvelles / En préparation / Prêtes.
   - Historique : plus récentes en premier, heure, filtres, pages de 30,
     « Remettre en Prête » pendant 30 min après un service par erreur.

   Tout le contenu est inséré par textContent (aucune injection HTML).
   ========================================================================== */
(function () {
  "use strict";

  var COLS = ["nouvelle", "en_preparation", "prete"];
  var NEXT_LABEL = { nouvelle: "Commencer la préparation", en_preparation: "Marquer prête", prete: "Marquer servie" };
  var NEXT_ICON = { nouvelle: "bi-fire", en_preparation: "bi-check2-circle", prete: "bi-hand-thumbs-up" };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function icon(name) { return el("i", "bi " + name); }
  function fmt(n) { return Math.round(n || 0).toString().replace(/\B(?=(\d{3})+(?!\d))/g, " "); }
  function toast(msg, isError) { if (window.MFShowToast) window.MFShowToast(msg, !!isError); }
  function url(tpl, id) { return tpl.replace("{id}", encodeURIComponent(id)); }
  // Référence « 7KM4XP92DT » : les 3 derniers caractères (code de retrait) en gras.
  function refEl(ref, cls) {
    var span = el("span", cls || "ob-ref");
    if (!ref) return span;
    span.appendChild(document.createTextNode(ref.slice(0, -3)));
    span.appendChild(el("b", null, ref.slice(-3)));
    span.title = "Code de retrait : " + ref.slice(-3);
    return span;
  }

  function minutes(n) { return n < 1 ? "à l'instant" : (n < 60 ? n + " min" : Math.floor(n / 60) + " h " + (n % 60 ? (n % 60) + " min" : "")); }

  document.addEventListener("DOMContentLoaded", function () {
    var root = document.getElementById("ob-root");
    if (!root) return;

    var currency = root.dataset.currency || "";
    var board = document.getElementById("ob-board");
    var summary = document.getElementById("ob-summary");
    var empty = document.getElementById("ob-empty");
    var known = null;                     // ids déjà vus (pour l'animation "nouvelle")
    var confirmCancel = {};               // id -> true pendant la confirmation
    var busy = {};                        // id -> action en cours
    var timer = null;
    var view = "board";

    /* =============================== Son =============================== */
    var soundBtn = document.getElementById("ob-sound");
    function soundAllowed() {
      return !!(navigator.userActivation && navigator.userActivation.hasBeenActive) || soundBtn.dataset.on === "1";
    }
    function renderSound() {
      var on = soundAllowed();
      soundBtn.classList.toggle("is-off", !on);
      soundBtn.classList.toggle("is-on", on);
      soundBtn.querySelector("i").className = "bi " + (on ? "bi-volume-up-fill" : "bi-volume-mute-fill");
      soundBtn.querySelector("span").textContent = on ? "Son activé" : "Activer le son";
      soundBtn.title = on ? "Cliquez pour tester le son" : "Le navigateur bloque le son tant que vous n'avez pas touché la page";
    }
    function beep() {
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return;
        var ctx = new Ctx();
        [880, 1046.5].forEach(function (f, i) {
          var o = ctx.createOscillator(), g = ctx.createGain(), s = ctx.currentTime + i * 0.16;
          o.type = "sine"; o.frequency.value = f;
          g.gain.setValueAtTime(0.0001, s);
          g.gain.exponentialRampToValueAtTime(0.35, s + 0.02);
          g.gain.exponentialRampToValueAtTime(0.0001, s + 0.32);
          o.connect(g); g.connect(ctx.destination); o.start(s); o.stop(s + 0.35);
        });
        setTimeout(function () { ctx.close(); }, 900);
      } catch (e) { /* audio indisponible */ }
    }
    soundBtn.addEventListener("click", function () {
      soundBtn.dataset.on = "1";
      beep();
      renderSound();
      toast("Son activé : vous entendrez ce signal à chaque nouvelle commande.", false);
    });
    document.addEventListener("pointerdown", function () { setTimeout(renderSound, 0); }, { passive: true });
    document.addEventListener("keydown", function () { setTimeout(renderSound, 0); });
    renderSound();
    setInterval(renderSound, 3000);

    /* =========================== Mode cuisine =========================== */
    var kitchenBtn = document.getElementById("ob-kitchen");
    var wakeLock = null;
    function requestWake() {
      if (!("wakeLock" in navigator) || !document.body.classList.contains("ob-kitchen")) return;
      navigator.wakeLock.request("screen").then(function (l) { wakeLock = l; }).catch(function () {});
    }
    function setKitchen(on) {
      document.body.classList.toggle("ob-kitchen", on);
      kitchenBtn.querySelector("span").textContent = on ? "Quitter le mode cuisine" : "Mode cuisine";
      kitchenBtn.querySelector("i").className = "bi " + (on ? "bi-fullscreen-exit" : "bi-arrows-fullscreen") + " me-1";
      try { localStorage.setItem("mf_kitchen_mode", on ? "1" : "0"); } catch (e) {}
      if (on) {
        if (document.documentElement.requestFullscreen && !document.fullscreenElement) {
          document.documentElement.requestFullscreen().catch(function () {});
        }
        requestWake();
      } else {
        if (document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(function () {});
        if (wakeLock) { wakeLock.release().catch(function () {}); wakeLock = null; }
      }
    }
    kitchenBtn.addEventListener("click", function () {
      soundBtn.dataset.on = "1"; renderSound();
      setKitchen(!document.body.classList.contains("ob-kitchen"));
    });
    document.addEventListener("visibilitychange", function () { if (!document.hidden) requestWake(); });
    try { if (localStorage.getItem("mf_kitchen_mode") === "1") setKitchen(true); } catch (e) {}

    /* ======================= Onglets (téléphone) ======================= */
    document.querySelectorAll("[data-ob-col]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        board.dataset.activeCol = tab.dataset.obCol;
        document.querySelectorAll("[data-ob-col]").forEach(function (t) {
          t.classList.toggle("is-active", t === tab);
        });
      });
    });

    /* ============================ Impression ============================ */
    // Réglages PROPRES À CET APPAREIL (mémorisés dans le navigateur) : seul le
    // PC ou la tablette relié(e) à l'imprimante imprime automatiquement, pas le
    // téléphone du gérant qui affiche aussi les commandes.
    var rid = root.dataset.restaurant || "0";
    var PRINT_KEY = "mf_print_" + rid;
    var printCfg = { auto: false, width: 80, printed: [] };
    try {
      var saved = JSON.parse(localStorage.getItem(PRINT_KEY) || "{}");
      printCfg.auto = saved.auto === true;
      printCfg.width = saved.width === 58 ? 58 : 80;
      printCfg.printed = Array.isArray(saved.printed) ? saved.printed.slice(-200) : [];
    } catch (e) {}
    function savePrintCfg() {
      try {
        localStorage.setItem(PRINT_KEY, JSON.stringify({ auto: printCfg.auto, width: printCfg.width,
                                                          printed: printCfg.printed.slice(-200) }));
      } catch (e) {}
    }
    function markPrinted(id) {
      if (printCfg.printed.indexOf(id) === -1) { printCfg.printed.push(id); savePrintCfg(); }
    }
    var coarse = !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
    var printQueue = [], printing = false;

    function ticketUrl(id, autoprint) {
      return url(root.dataset.ticketUrl, id) + "?w=" + printCfg.width + (autoprint ? "&print=1" : "");
    }
    function runQueue() {
      if (printing || !printQueue.length) return;
      printing = true;
      var id = printQueue.shift();
      var frame = document.createElement("iframe");
      frame.className = "ob-print-frame";
      frame.setAttribute("aria-hidden", "true");
      frame.tabIndex = -1;
      frame.src = ticketUrl(id, true);        // le ticket lance lui-même l'impression
      document.body.appendChild(frame);
      // Laisse le temps à l'impression de partir, puis passe au ticket suivant.
      setTimeout(function () { frame.remove(); printing = false; runQueue(); }, 8000);
    }
    function printTicket(id, manual) {
      markPrinted(id);
      if (manual && coarse) {
        // Tablette / téléphone : l'impression d'un cadre est peu fiable, on ouvre un onglet.
        var w = window.open(ticketUrl(id, true), "_blank");
        if (!w) toast("Autorisez les fenêtres pop-up pour imprimer le ticket.", true);
        return;
      }
      if (printQueue.indexOf(id) === -1) printQueue.push(id);
      runQueue();
    }
    function autoPrint(list) {
      if (!printCfg.auto) return;
      list.forEach(function (o) {
        if (o.takeaway && o.status === "nouvelle" && o.elapsed <= 30 && printCfg.printed.indexOf(o.id) === -1) {
          printTicket(o.id, false);
        }
      });
    }

    function renderPrintLabel() {
      var lbl = document.getElementById("ob-print-state");
      if (lbl) lbl.textContent = printCfg.auto ? "Impression auto" : "Impression";
      var btn = document.getElementById("ob-print-menu");
      if (btn) btn.classList.toggle("is-auto", printCfg.auto);
    }
    var autoSwitch = document.getElementById("ob-autoprint");
    var widthSelect = document.getElementById("ob-paper");
    if (autoSwitch) {
      autoSwitch.checked = printCfg.auto;
      autoSwitch.addEventListener("change", function () {
        printCfg.auto = autoSwitch.checked;
        if (printCfg.auto) {
          // Les commandes déjà affichées ne sont pas réimprimées.
          (lastData.orders || []).forEach(function (o) { markPrinted(o.id); });
          toast("Impression automatique activée sur cet appareil pour les nouvelles commandes à emporter.", false);
        }
        savePrintCfg();
        renderPrintLabel();
      });
    }
    if (widthSelect) {
      widthSelect.value = String(printCfg.width);
      widthSelect.addEventListener("change", function () {
        printCfg.width = widthSelect.value === "58" ? 58 : 80;
        savePrintCfg();
      });
    }
    var testBtn = document.getElementById("ob-print-test");
    if (testBtn) testBtn.addEventListener("click", function () {
      var first = (lastData.orders || [])[0];
      if (!first) { toast("Aucune commande en cours à imprimer pour le test.", true); return; }
      printTicket(first.id, true);
    });
    renderPrintLabel();

    /* ============================ Cartes ============================ */
    function level(o) {
      if (o.status === "prete") return o.status_minutes <= 5 ? "ok" : (o.status_minutes <= 10 ? "warn" : "late");
      if (o.elapsed <= o.prep) return "ok";
      return o.elapsed <= o.prep + 10 ? "warn" : "late";
    }

    function act(o, kind) {
      if (busy[o.id]) return;
      busy[o.id] = true;
      var target = kind === "cancel" ? root.dataset.cancelUrl : root.dataset.advanceUrl;
      fetch(url(target, o.id), { method: "POST", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function () {
          if (kind === "cancel") toast("Commande de " + o.table + " annulée.", false);
          else if (o.status === "prete") toast(o.table + " : commande servie.", false);
        })
        .catch(function () { toast("Action impossible. Vérifiez votre connexion.", true); })
        .finally(function () { delete busy[o.id]; delete confirmCancel[o.id]; refresh(); });
    }

    function card(o, isNew) {
      var lv = level(o);
      var c = el("article", "ob-card ob-card--" + lv + (isNew ? " is-new" : ""));
      c.dataset.id = o.id;

      if (o.takeaway) c.classList.add("ob-card--takeaway");
      var head = el("div", "ob-card-head");
      if (o.takeaway) {
        var who = el("div", "ob-table");
        who.appendChild(el("span", "ob-tk-badge", "À emporter"));
        who.appendChild(el("span", "ob-tk-name", o.takeaway.name));
        head.appendChild(who);
      } else {
        head.appendChild(el("div", "ob-table", o.table));
      }
      var wait = el("div", "ob-wait ob-wait--" + lv);
      wait.appendChild(icon("bi-clock"));
      wait.appendChild(document.createTextNode(" " + minutes(o.status === "prete" ? o.status_minutes : o.elapsed)));
      wait.title = o.status === "prete" ? "Prête depuis" : "Commandée à " + o.time + " · préparation prévue ~" + o.prep + " min";
      head.appendChild(wait);
      c.appendChild(head);

      var meta = el("div", "ob-meta");
      if (o.reference) { meta.appendChild(refEl(o.reference)); meta.appendChild(document.createTextNode(" · ")); }
      else meta.appendChild(document.createTextNode("#" + o.id + " · "));
      meta.appendChild(document.createTextNode(o.time + (o.status === "prete"
        ? (o.takeaway ? " · prête, en attente du client" : " · prête, en attente de service")
        : " · prévu ~" + o.prep + " min")));
      c.appendChild(meta);

      if (o.takeaway) {
        var tk = el("div", "ob-tk");
        if (o.takeaway.pickup) {
          var pk = el("div", "ob-tk-line");
          pk.appendChild(icon("bi-bag-check"));
          pk.appendChild(document.createTextNode(" Retrait : " + o.takeaway.pickup));
          tk.appendChild(pk);
        }
        if (o.takeaway.phone) {
          var ph = el("div", "ob-tk-line ob-tk-phone");
          ph.appendChild(icon("bi-telephone"));
          ph.appendChild(document.createTextNode(" " + o.takeaway.phone));
          tk.appendChild(ph);
        }
        c.appendChild(tk);
      }

      var ul = el("ul", "ob-items");
      o.items.forEach(function (it) {
        var li = el("li");
        li.appendChild(el("b", "ob-qty", it.quantity + "×"));
        li.appendChild(document.createTextNode(" " + it.name));
        ul.appendChild(li);
      });
      c.appendChild(ul);

      if (o.notes) {
        var note = el("div", "ob-note");
        note.appendChild(icon("bi-exclamation-triangle-fill"));
        note.appendChild(el("span", null, o.notes));
        c.appendChild(note);
      }

      var foot = el("div", "ob-foot");
      foot.appendChild(el("span", "ob-total", fmt(o.total) + " " + currency));

      if (confirmCancel[o.id]) {
        var box = el("div", "ob-confirm");
        box.appendChild(el("span", null, "Annuler cette commande ?"));
        var yes = el("button", "btn btn-sm btn-danger", "Oui, annuler");
        yes.type = "button";
        yes.addEventListener("click", function () { act(o, "cancel"); });
        var no = el("button", "btn btn-sm btn-light", "Non");
        no.type = "button";
        no.addEventListener("click", function () { delete confirmCancel[o.id]; render(lastData); });
        box.appendChild(yes); box.appendChild(no);
        c.appendChild(foot);
        c.appendChild(box);
        return c;
      }

      var actions = el("div", "ob-buttons");
      var next = el("button", "btn ob-next ob-next--" + o.status);
      next.type = "button";
      next.disabled = !!busy[o.id];
      next.appendChild(icon(NEXT_ICON[o.status]));
      next.appendChild(document.createTextNode(" " + NEXT_LABEL[o.status]));
      next.addEventListener("click", function () { act(o, "advance"); });
      var cancel = el("button", "btn ob-cancel");
      cancel.type = "button";
      cancel.title = "Annuler la commande";
      cancel.setAttribute("aria-label", "Annuler la commande de " + o.table);
      cancel.appendChild(icon("bi-x-lg"));
      cancel.addEventListener("click", function () {
        confirmCancel[o.id] = true;
        render(lastData);
        setTimeout(function () { if (confirmCancel[o.id]) { delete confirmCancel[o.id]; render(lastData); } }, 8000);
      });
      var printBtn = el("button", "btn ob-print");
      printBtn.type = "button";
      printBtn.title = "Imprimer le ticket";
      printBtn.setAttribute("aria-label", "Imprimer le ticket de " + (o.takeaway ? o.takeaway.name : o.table));
      printBtn.appendChild(icon("bi-printer"));
      printBtn.addEventListener("click", function () { printTicket(o.id, true); });
      actions.appendChild(next);
      actions.appendChild(printBtn);
      actions.appendChild(cancel);
      foot.appendChild(actions);
      c.appendChild(foot);
      return c;
    }

    /* ============================ Tableau ============================ */
    var lastData = { orders: [], summary: {} };

    function render(data) {
      lastData = data;
      var orders = data.orders || [];
      var seen = {};
      COLS.forEach(function (status) {
        var body = document.getElementById("ob-col-" + status);
        var list = orders.filter(function (o) { return o.status === status; });
        document.querySelectorAll('[data-ob-count="' + status + '"]').forEach(function (b) { b.textContent = list.length; });
        body.textContent = "";
        if (!list.length) {
          body.appendChild(el("p", "ob-col-empty", status === "nouvelle" ? "Aucune nouvelle commande." :
            status === "en_preparation" ? "Rien en préparation." : "Aucune commande en attente de service."));
          return;
        }
        list.forEach(function (o) {
          seen[o.id] = true;
          body.appendChild(card(o, known !== null && !known[o.id]));
        });
        autoPrint(list);
      });
      known = seen;
      board.classList.toggle("d-none", orders.length === 0);
      document.querySelector(".ob-coltabs").classList.toggle("d-none", orders.length === 0);
      empty.classList.toggle("d-none", orders.length !== 0);

      var s = data.summary || {};
      if (!s.active) {
        summary.textContent = "Aucune commande en cours";
      } else {
        var parts = [s.active + " en cours"];
        if (s.avg_wait) parts.push("attente moyenne " + s.avg_wait + " min");
        if (s.oldest_table) parts.push("plus ancienne : " + s.oldest_table + " (" + minutes(s.oldest_minutes) + ")");
        summary.textContent = parts.join(" · ");
      }
    }

    function refresh() {
      return fetch(root.dataset.feedUrl, { cache: "no-store", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(render)
        .catch(function () { summary.textContent = "Connexion perdue, nouvelle tentative…"; });
    }

    // Le temps réel de app.js (annulation par un client) rafraîchit aussi le tableau.
    window.MFKanbanRefreshWithCancelAnimation = function () { refresh(); };

    /* =========================== Historique =========================== */
    var hist = { filter: "today", page: 1 };
    var histList = document.getElementById("ob-history");
    var pager = document.getElementById("ob-pager");

    function statusBadge(o) {
      if (o.status === "servie") return el("span", "ob-badge ob-badge--ok", "Servie");
      return el("span", "ob-badge ob-badge--ko", o.cancelled_by === "client" ? "Annulée par le client" : "Annulée");
    }

    function renderHistory(data) {
      histList.textContent = "";
      document.getElementById("ob-history-count").textContent = data.total + " commande" + (data.total > 1 ? "s" : "");
      if (!data.orders.length) {
        histList.appendChild(el("p", "ob-col-empty text-center py-4",
          hist.filter === "today" ? "Aucune commande terminée aujourd'hui." : "Aucune commande dans ce filtre."));
      }
      data.orders.forEach(function (o) {
        var row = el("div", "ob-hrow");
        var left = el("div", "ob-hmain");
        var top = el("div", "ob-htop");
        top.appendChild(el("strong", null, o.takeaway ? "À emporter · " + o.takeaway.name : o.table));
        top.appendChild(statusBadge(o));
        if (o.reference) top.appendChild(refEl(o.reference, "ob-ref ob-ref--small"));
        left.appendChild(top);
        left.appendChild(el("div", "ob-hitems", o.items.map(function (it) { return it.quantity + "× " + it.name; }).join(", ")));
        if (o.notes) left.appendChild(el("div", "ob-hnote", "Note : " + o.notes));
        var right = el("div", "ob-hside");
        right.appendChild(el("div", "ob-htime", (o.today ? o.created_time : o.created) + " → " + o.finished_time));
        right.appendChild(el("div", "ob-total", fmt(o.total) + " " + currency));
        if (o.can_undo) {
          var undo = el("button", "btn btn-sm btn-outline-secondary mt-1");
          undo.type = "button";
          undo.appendChild(icon("bi-arrow-counterclockwise"));
          undo.appendChild(document.createTextNode(" Remettre en Prête"));
          undo.title = "Servie par erreur ? Possible pendant 30 minutes.";
          undo.addEventListener("click", function () {
            undo.disabled = true;
            fetch(url(root.dataset.undoUrl, o.id), { method: "POST", headers: { "X-Requested-With": "fetch" } })
              .then(function (r) { return r.json().then(function (d) { return { ok: r.ok && d.ok, d: d }; }); })
              .then(function (res) {
                if (res.ok) { toast(o.table + " : commande remise en « Prête ».", false); refresh(); }
                else toast(res.d.error || "Action impossible.", true);
                loadHistory();
              })
              .catch(function () { undo.disabled = false; toast("Action impossible. Vérifiez votre connexion.", true); });
          });
          right.appendChild(undo);
        }
        row.appendChild(left); row.appendChild(right);
        histList.appendChild(row);
      });
      pager.classList.toggle("d-none", data.pages <= 1);
      document.getElementById("ob-page").textContent = "Page " + data.page + " / " + data.pages;
      document.getElementById("ob-prev").disabled = data.page <= 1;
      document.getElementById("ob-next").disabled = data.page >= data.pages;
      hist.page = data.page;
    }

    function loadHistory() {
      var u = root.dataset.historyUrl + "?filtre=" + encodeURIComponent(hist.filter) + "&page=" + hist.page;
      return fetch(u, { cache: "no-store", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(renderHistory)
        .catch(function () { toast("Impossible de charger l'historique.", true); });
    }

    document.querySelectorAll("[data-ob-filter]").forEach(function (chip) {
      chip.addEventListener("click", function () {
        hist.filter = chip.dataset.obFilter; hist.page = 1;
        document.querySelectorAll("[data-ob-filter]").forEach(function (c) { c.classList.toggle("is-active", c === chip); });
        loadHistory();
      });
    });
    document.getElementById("ob-prev").addEventListener("click", function () { hist.page = Math.max(1, hist.page - 1); loadHistory(); });
    document.getElementById("ob-next").addEventListener("click", function () { hist.page += 1; loadHistory(); });

    /* ============================ Vues ============================ */
    function setView(v) {
      view = v;
      document.getElementById("ob-board-view").classList.toggle("d-none", v !== "board");
      document.getElementById("ob-history-view").classList.toggle("d-none", v !== "history");
      document.querySelectorAll("[data-ob-view]").forEach(function (b) {
        b.classList.toggle("is-active", b.dataset.obView === v);
        b.setAttribute("aria-selected", b.dataset.obView === v ? "true" : "false");
      });
      if (v === "history") loadHistory();
      schedule();
    }
    document.querySelectorAll("[data-ob-view]").forEach(function (b) {
      b.addEventListener("click", function () { setView(b.dataset.obView); });
    });

    function tick() {
      if (view === "board") refresh(); else loadHistory();
    }
    function schedule() {
      clearInterval(timer);
      timer = setInterval(tick, document.hidden ? 20000 : (view === "board" ? 4000 : 15000));
    }
    document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(); schedule(); });

    refresh();
    setView(root.dataset.startView === "history" ? "history" : "board");
  });
})();


