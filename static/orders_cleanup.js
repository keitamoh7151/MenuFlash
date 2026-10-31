/* ==========================================================================
   MenuFlash Mini — nettoyage du panneau "Mes commandes" (côté client)

   Le téléphone du client mémorise les numéros de ses commandes dans
   localStorage (clé "mf_orders_<slug>"). Le serveur ne renvoie que les
   commandes encore pertinentes (en cours, annulée depuis moins de 5 min,
   servie depuis moins de 30 min, et jamais plus de 12 h), et indique dans
   `expired_ids` les numéros que le téléphone peut oublier.

   Ce script retire ces numéros expirés de la mémoire du téléphone, pour
   que la liste ne grossisse jamais avec le temps. Il est indépendant de
   app.js : il ne fait que nettoyer le stockage, l'affichage reste géré
   par app.js (qui reçoit déjà la liste filtrée par le serveur).
   ========================================================================== */
(function () {
  "use strict";

  function readIds(key) {
    try {
      var ids = JSON.parse(localStorage.getItem(key) || "[]");
      return Array.isArray(ids) ? ids : [];
    } catch (e) {
      return [];
    }
  }

  function initOrdersCleanup() {
    var form = document.getElementById("order-form");
    if (!form || !form.dataset.slug) return;

    var slug = form.dataset.slug;
    var key = "mf_orders_" + slug;

    function prune() {
      var ids = readIds(key);
      if (ids.length === 0) return;

      fetch("/m/" + encodeURIComponent(slug) + "/commandes/etats.json?ids=" + ids.join(","),
            { cache: "no-store" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || !Array.isArray(data.expired_ids) || data.expired_ids.length === 0) return;

          var expired = {};
          data.expired_ids.forEach(function (id) { expired[String(id)] = true; });

          // On relit la liste au moment d'écrire : une commande envoyée
          // pendant la requête ne doit surtout pas être effacée.
          var kept = readIds(key).filter(function (id) { return !expired[String(id)]; });
          try { localStorage.setItem(key, JSON.stringify(kept)); } catch (e) {}
        })
        .catch(function () { /* réseau indisponible : on réessaiera */ });
    }

    prune();
    setInterval(prune, 60000);
  }

  document.addEventListener("DOMContentLoaded", initOrdersCleanup);
})();
