/* ==========================================================================
   MenuFlash Mini — complément JavaScript des corrections pour téléphone.

   Entoure automatiquement chaque tableau qui ne l'est pas déjà d'un cadre
   défilant (.table-responsive) : sur un petit écran, c'est le tableau qui
   défile horizontalement, jamais la page entière. Fonctionne aussi pour
   les tableaux ajoutés plus tard par JavaScript (historique, kanban…).

   Exception : le tableau des plats du tableau de bord, qui est transformé
   en cartes empilées par responsive.css (pas besoin de défilement).
   ========================================================================== */
(function () {
  "use strict";

  function wrapTables(root) {
    (root || document).querySelectorAll("table").forEach(function (table) {
      if (table.closest(".table-responsive")) return;
      if (table.querySelector("tbody.mf-items-tbody")) return;
      var wrapper = document.createElement("div");
      wrapper.className = "table-responsive";
      table.parentNode.insertBefore(wrapper, table);
      wrapper.appendChild(table);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    wrapTables(document);
    if (typeof MutationObserver === "undefined") return;
    var pending = false;
    new MutationObserver(function () {
      if (pending) return;
      pending = true;
      requestAnimationFrame(function () { pending = false; wrapTables(document); });
    }).observe(document.body, { childList: true, subtree: true });
  });
})();
