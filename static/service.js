/* ==========================================================================
   MenuFlash Mini — horaires d'ouverture + appel du serveur (navigateur).
   Toutes les données sont insérées via textContent : aucune injection HTML.
   ========================================================================== */
(function () {
  "use strict";

  var dataEl = document.getElementById("mf-service-data");
  if (!dataEl) return;
  var cfg;
  try { cfg = JSON.parse(dataEl.textContent); } catch (e) { return; }

  var TEXT = {
    fr: { closed: "Fermé actuellement", suspended: "Commandes momentanément suspendues",
          waiter: "Appeler le serveur", bill: "L'addition",
          waiterSent: "Serveur prévenu", billSent: "Addition demandée",
          sentToast: "C'est noté : un serveur arrive à votre table.",
          error: "Impossible d'envoyer la demande. Vérifiez votre connexion." },
    en: { closed: "Closed right now", suspended: "Orders temporarily paused",
          waiter: "Call the waiter", bill: "The bill",
          waiterSent: "Waiter notified", billSent: "Bill requested",
          sentToast: "Got it: a waiter is coming to your table.",
          error: "Could not send the request. Check your connection." }
  };
  function lang() { try { return localStorage.getItem("mf_lang") === "en" ? "en" : "fr"; } catch (e) { return "fr"; } }
  function t(k) { return TEXT[lang()][k]; }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function icon(name) { return el("i", "bi " + name); }
  function toast(msg, isError) { if (window.MFShowToast) window.MFShowToast(msg, !!isError); }
  function csrf() { var m = document.querySelector('meta[name="csrf-token"]'); return m ? m.getAttribute("content") : ""; }

  /* ============================ Menu public ============================ */
  function initPublic() {
    document.body.classList.add("mf-is-public");
    var hero = document.querySelector(".mf-hero");
    var host = el("div", "mf-service-host");
    // Dans l'en-tête, sous le nom du restaurant et le badge de la table.
    if (hero) hero.appendChild(host);
    else document.body.insertBefore(host, document.body.firstChild);

    var buttons = {};

    function render() {
      host.textContent = "";
      buttons = {};
      if (!cfg.open) {
        document.body.classList.add("mf-is-closed");
        var b = el("div", "mf-closed-banner");
        b.setAttribute("role", "status");
        b.appendChild(icon(cfg.reason === "suspended" ? "bi-pause-circle-fill" : "bi-moon-fill"));
        var txt = el("div", null, cfg.reason === "suspended" ? t("suspended") : t("closed"));
        var sub = cfg.reason === "suspended" ? (cfg.message || "") : (lang() === "en" ? cfg.next_en : cfg.next_fr);
        if (sub) txt.appendChild(el("small", null, sub));
        b.appendChild(txt);
        host.appendChild(b);
        return;
      }
      if (!cfg.table) { host.remove(); return; }
      var bar = el("div", "mf-call-bar");
      [["serveur", "bi-bell-fill", "waiter", "waiterSent"], ["addition", "bi-receipt", "bill", "billSent"]].forEach(function (def) {
        var btn = el("button", "mf-call-btn");
        btn.type = "button";
        btn.appendChild(icon(def[1]));
        var label = el("span", null, t(def[2]));
        btn.appendChild(label);
        btn.addEventListener("click", function () { send(btn, label, def); });
        buttons[def[0]] = { btn: btn, label: label, def: def };
        bar.appendChild(btn);
      });
      host.appendChild(bar);
    }

    function send(btn, label, def) {
      btn.disabled = true;
      var body = new FormData();
      body.append("kind", def[0]);
      if (cfg.qr) body.append("qr", cfg.qr);
      if (cfg.t) body.append("t", cfg.t);
      fetch(cfg.call_url, { method: "POST", body: body,
        headers: { "X-Requested-With": "fetch", "X-CSRFToken": csrf() } })
        .then(function (r) { return r.json().then(function (d) { return { status: r.status, d: d }; }); })
        .then(function (res) {
          if (res.d && res.d.ok) {
            btn.querySelector("i").className = "bi bi-check-circle-fill";
            label.textContent = t(def[3]);
            toast(t("sentToast"), false);
            setTimeout(function () {       // réactivable après 60 s
              btn.disabled = false;
              btn.querySelector("i").className = "bi " + def[1];
              label.textContent = t(def[2]);
            }, 60000);
          } else {
            btn.disabled = false;
            toast((res.d && res.d.error) || t("error"), true);
            if (res.status === 409) { cfg.open = false; render(); }
          }
        })
        .catch(function () { btn.disabled = false; toast(t("error"), true); });
    }

    render();
    document.querySelectorAll(".mf-lang-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        setTimeout(function () {
          Object.keys(buttons).forEach(function (k) {
            var o = buttons[k];
            o.label.textContent = t(o.btn.disabled ? o.def[3] : o.def[2]);
          });
          if (!cfg.open) render();
        }, 0);
      });
    });
  }

  /* ========================= Équipe (back-office) ========================= */
  function beep() {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      var ctx = new Ctx();
      [660, 880, 660].forEach(function (f, i) {
        var o = ctx.createOscillator(), g = ctx.createGain(), s = ctx.currentTime + i * 0.18;
        o.type = "triangle"; o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, s);
        g.gain.exponentialRampToValueAtTime(0.4, s + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, s + 0.16);
        o.connect(g); g.connect(ctx.destination); o.start(s); o.stop(s + 0.18);
      });
      setTimeout(function () { ctx.close(); }, 1000);
    } catch (e) { /* son bloqué tant qu'on n'a pas cliqué dans la page */ }
  }

  function initStaff() {
    var panel = null, list = null, countEl = null, timer = null;
    var KEY = "mf_last_call_id";
    var lastId = null;
    try { var s = sessionStorage.getItem(KEY); if (s !== null) lastId = parseInt(s, 10); } catch (e) {}

    function ensurePanel() {
      if (panel) return;
      panel = el("div", "mf-calls-panel");
      panel.setAttribute("role", "region");
      panel.setAttribute("aria-label", "Appels des clients");
      var head = el("div", "mf-calls-head");
      countEl = el("span");
      var toggle = el("button", "mf-calls-toggle");
      toggle.type = "button";
      toggle.setAttribute("aria-label", "Réduire");
      toggle.appendChild(icon("bi-chevron-down"));
      toggle.addEventListener("click", function () {
        panel.classList.toggle("is-collapsed");
        toggle.firstChild.className = "bi " + (panel.classList.contains("is-collapsed") ? "bi-chevron-up" : "bi-chevron-down");
      });
      head.appendChild(icon("bi-bell-fill mf-calls-bell")); head.appendChild(countEl); head.appendChild(toggle);
      list = el("div", "mf-calls-list");
      list.setAttribute("aria-live", "polite");
      panel.appendChild(head); panel.appendChild(list);
      document.body.appendChild(panel);
    }

    function render(calls) {
      if (!calls.length) { if (panel) { panel.remove(); panel = null; } return; }
      ensurePanel();
      countEl.textContent = calls.length + " appel" + (calls.length > 1 ? "s" : "") + " en attente";
      list.textContent = "";
      calls.forEach(function (c) {
        var row = el("div", "mf-call-item" + (c.kind === "addition" ? " is-bill" : ""));
        var ic = el("div", "mf-call-icon"); ic.appendChild(icon(c.kind === "addition" ? "bi-receipt" : "bi-bell-fill"));
        var text = el("div", "mf-call-text");
        text.appendChild(el("strong", null, c.table));
        text.appendChild(el("small", null, c.label + " · " + (c.minutes < 1 ? "à l'instant" : "il y a " + c.minutes + " min")));
        var done = el("button", "btn btn-sm btn-success mf-call-done", "Traité");
        done.type = "button";
        done.addEventListener("click", function () {
          done.disabled = true;
          fetch(cfg.handle_url.replace("{id}", encodeURIComponent(c.id)), {
            method: "POST", headers: { "X-Requested-With": "fetch", "X-CSRFToken": csrf() }
          }).then(poll).catch(function () { done.disabled = false; });
        });
        row.appendChild(ic); row.appendChild(text); row.appendChild(done);
        list.appendChild(row);
      });
    }

    function poll() {
      return fetch(cfg.feed_url, { cache: "no-store", headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) return;
          render(d.calls || []);
          if (lastId !== null && d.latest_id > lastId) {
            var fresh = (d.calls || []).filter(function (c) { return c.id > lastId; });
            if (fresh.length) {
              beep();
              var c = fresh[fresh.length - 1];
              toast("🔔 " + c.table + " : " + c.label.toLowerCase(), false);
            }
          }
          lastId = d.latest_id;
          try { sessionStorage.setItem(KEY, String(lastId)); } catch (e) {}
        })
        .catch(function () {});
    }

    function schedule() { clearInterval(timer); timer = setInterval(poll, document.hidden ? 20000 : 5000); }
    document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); schedule(); });
    poll(); schedule();
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (cfg.mode === "public") initPublic();
    else if (cfg.mode === "staff") initStaff();
  });
})();
