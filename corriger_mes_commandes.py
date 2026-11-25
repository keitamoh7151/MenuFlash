"""
MenuFlash — Correction : la fenêtre « Mes commandes » clignote (boutons qui disparaissent puis reviennent).

CAUSE (reproduite dans un vrai navigateur)
------------------------------------------
Toutes les 6 secondes, static/app.js remplaçait TOUT le contenu du panneau (innerHTML), même quand rien n'avait
changé. À chaque fois :
  1. la carte de la commande était détruite puis recréée, sans le bouton « Modifier la commande » ;
  2. le script order_edit.js, qui ajoute ce bouton, ne le remettait qu'environ 50 ms plus tard : pendant ces
     quelques images, le bouton avait disparu, puis il revenait et poussait « Annuler la commande » (le contenu « bouge ») ;
  3. un appui sur « Modifier » ou « Annuler » pendant le remplacement était PERDU : le bouton changeait sous le doigt.
Mesure avant correction (24 s) : 4 reconstructions, 14 images sans le bouton, 6 décalages, 3 appuis perdus sur 3.

CORRECTION
----------
1. static/app.js : le panneau est « synchronisé » carte par carte. Une carte dont le contenu n'a pas changé n'est
   plus touchée du tout. Si elle a vraiment changé (statut, total…), seule cette carte est remplacée, en gardant le
   bouton « Modifier » déjà en place. Les cartes disparues sont retirées, les nouvelles ajoutées, l'ordre est conservé.
2. static/app.js : une réponse du serveur plus ancienne qu'une réponse déjà affichée est ignorée (deux
   rafraîchissements qui se chevauchent pouvaient faire revenir un ancien état) ; plus de requête quand l'onglet est
   caché, rafraîchissement immédiat au retour sur l'onglet ; les réponses ne sont plus mises en cache.
3. static/order_edit.js : le bouton « Modifier la commande » est placé dans la MÊME image que la carte (plus de
   délai de 50 ms) grâce à une fonction exposée à app.js (window.MFOrderEditScan).
4. templates/base.html : numéros de version de app.js et order_edit.js +1 (les navigateurs prennent les nouveaux fichiers).

UTILISATION (à la racine du projet) :  python corriger_mes_commandes.py
Contrôle :  python corriger_mes_commandes.py --verifier
Vérifie tout avant d'écrire ; copies : *.avant-panneau
"""
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_JS = os.path.join(ROOT, "static", "app.js")
EDIT_JS = os.path.join(ROOT, "static", "order_edit.js")
BASE = os.path.join(ROOT, "templates", "base.html")
SUFFIX = ".avant-panneau"

START = "    function refreshMyOrders() {\n"
END = '    if (myOrdersList) {\n      myOrdersList.addEventListener("click", function (e) {\n'
EXPECTED_INSIDE = ['"/commandes/etats.json?ids="', "myOrdersList.innerHTML = orders.slice().reverse().map(renderMyOrder).join(\"\");",
                   "updateBadge(activeCount);"]

NEW_FUNCTIONS = '''    // ------------------------------------------------------------------------------------------------
    // Panneau « Mes commandes » : synchronisé carte par carte (audit « panneau instable »).
    // Avant : tout le panneau était reconstruit toutes les 6 s (innerHTML), même sans changement : les boutons
    // clignotaient et un appui en cours était perdu. Maintenant : une carte inchangée n'est jamais touchée.
    // ------------------------------------------------------------------------------------------------
    var renderedById = {};            // numéro de commande -> dernier HTML construit pour sa carte
    var refreshSeq = 0, appliedSeq = 0;

    function htmlToElement(html) {
      var tpl = document.createElement("template");
      tpl.innerHTML = html.trim();
      return tpl.content.firstElementChild;
    }

    // Garde le bouton « Modifier la commande » (ajouté par order_edit.js, avec son action) quand une carte est remplacée.
    function carryEditButton(oldCard, newCard) {
      var btn = oldCard.querySelector(".mf-myorder-editbtn");
      var actions = newCard.querySelector(".mf-myorder-actions");
      var cancel = newCard.querySelector(".mf-cancel-order-trigger");
      if (btn && actions && cancel && !newCard.querySelector(".mf-myorder-editbtn")) {
        actions.insertBefore(btn, cancel);
      }
    }

    function syncMyOrders(orders) {
      var wanted = orders.slice().reverse();            // même ordre qu'avant : la plus récente en haut
      var existing = {};
      Array.prototype.forEach.call(myOrdersList.children, function (card) {
        existing[card.getAttribute("data-order-id")] = card;
      });
      var seen = {};
      wanted.forEach(function (order, index) {
        var key = String(order.id);
        seen[key] = true;
        var html = renderMyOrder(order);
        var card = existing[key];
        if (!card || renderedById[key] !== html) {      // nouvelle carte, ou contenu réellement modifié
          var fresh = htmlToElement(html);
          if (card) {
            carryEditButton(card, fresh);
            myOrdersList.replaceChild(fresh, card);
          }
          renderedById[key] = html;
          card = fresh;
        }
        var current = myOrdersList.children[index];
        if (current !== card) myOrdersList.insertBefore(card, current || null);
      });
      Object.keys(existing).forEach(function (key) {   // commandes qui ne sont plus à afficher
        if (!seen[key]) {
          myOrdersList.removeChild(existing[key]);
          delete renderedById[key];
        }
      });
      // Le bouton « Modifier la commande » apparaît dans la même image que la carte (pas 50 ms plus tard).
      if (typeof window.MFOrderEditScan === "function") window.MFOrderEditScan();
    }

    function refreshMyOrders() {
      if (!myOrdersList) return;
      var ids = getStoredIds();
      if (ids.length === 0) {
        if (myOrdersEmpty) myOrdersEmpty.style.display = "";
        updateBadge(0);
        return;
      }
      var mySeq = ++refreshSeq;
      fetch("/m/" + slug + "/commandes/etats.json?ids=" + ids.join(","), { cache: "no-store" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (mySeq < appliedSeq) return;               // réponse plus ancienne que celle déjà affichée : ignorée
          appliedSeq = mySeq;
          var orders = data.orders || [];
          if (orders.length === 0) {
            if (myOrdersEmpty) myOrdersEmpty.style.display = "";
            syncMyOrders([]);
            updateBadge(0);
            return;
          }
          if (myOrdersEmpty) myOrdersEmpty.style.display = "none";
          syncMyOrders(orders);

          var activeCount = orders.filter(function (o) {
            return o.status !== "servie" && o.status !== "annulee";
          }).length;
          updateBadge(activeCount);
        })
        .catch(function () {});
    }

'''

TIMER_OLD = "    setInterval(refreshMyOrders, 6000);\n"
TIMER_NEW = '''    setInterval(function () { if (!document.hidden) refreshMyOrders(); }, 6000);      // rien à faire onglet caché
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refreshMyOrders(); });
'''
SCAN_OLD = "  function scan() { addToCards(); addToTracking(); }\n"
SCAN_NEW = ("  function scan() { addToCards(); addToTracking(); }\n"
            "  window.MFOrderEditScan = scan;      // utilisé par app.js : le bouton « Modifier » apparaît avec sa carte\n")


def read(path):
    if not os.path.exists(path):
        sys.exit(f"ARRÊT : {os.path.relpath(path, ROOT)} introuvable (lancez le script à la racine du projet).")
    return open(path, encoding="utf-8").read()


def verifier():
    app, edit = read(APP_JS), read(EDIT_JS)
    checks = [
        ("app.js : synchronisation carte par carte", "function syncMyOrders(orders)" in app),
        ("app.js : réponses anciennes ignorées", "if (mySeq < appliedSeq) return;" in app),
        ("app.js : plus de innerHTML sur la liste des commandes", "myOrdersList.innerHTML = orders" not in app),
        ("app.js : pause onglet caché", "if (!document.hidden) refreshMyOrders();" in app),
        ("order_edit.js : window.MFOrderEditScan", "window.MFOrderEditScan = scan;" in edit),
    ]
    for label, ok in checks:
        print(("  OK     " if ok else "  MANQUE ") + label)
    sys.exit(0 if all(ok for _, ok in checks) else 1)


def main():
    if "--verifier" in sys.argv:
        verifier()
    app, edit, base = read(APP_JS), read(EDIT_JS), read(BASE)
    if "function syncMyOrders(orders)" in app:
        print("Cette correction est déjà appliquée : rien à faire.")
        return
    if app.count(START) != 1 or app.count(END) != 1 or app.index(START) > app.index(END):
        sys.exit("ARRÊT : fonction refreshMyOrders introuvable ou en double dans static/app.js. Rien n'a été modifié.")
    old_block = app[app.index(START):app.index(END)]
    for needle in EXPECTED_INSIDE:
        if needle not in old_block:
            sys.exit("ARRÊT : la fonction refreshMyOrders de static/app.js a été modifiée (élément attendu absent : "
                     f"{needle[:50]}). Rien n'a été modifié. Envoyez-moi le fichier.")
    if app.count(TIMER_OLD) != 1:
        sys.exit("ARRÊT : « setInterval(refreshMyOrders, 6000) » introuvable dans static/app.js. Rien n'a été modifié.")
    if edit.count(SCAN_OLD) != 1:
        sys.exit("ARRÊT : fonction scan() introuvable dans static/order_edit.js. Rien n'a été modifié.")
    new_base, n_app = re.subn(r"(filename='app\.js'\) }}\?v=)(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + 1), base)
    new_base, n_edit = re.subn(r"(filename='order_edit\.js'\) }}\?v=)(\d+)", lambda m: m.group(1) + str(int(m.group(2)) + 1), new_base)
    if n_app != 1 or n_edit != 1:
        sys.exit("ARRÊT : numéros de version de app.js / order_edit.js introuvables dans templates/base.html. Rien n'a été modifié.")

    new_app = app.replace(old_block, NEW_FUNCTIONS).replace(TIMER_OLD, TIMER_NEW)
    new_edit = edit.replace(SCAN_OLD, SCAN_NEW)
    for path, text in ((APP_JS, new_app), (EDIT_JS, new_edit), (BASE, new_base)):
        shutil.copy2(path, path + SUFFIX)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"  modifié : {os.path.relpath(path, ROOT)}")
    print(f"\nCorrection appliquée. Copies : *{SUFFIX}. Contrôle :  python corriger_mes_commandes.py --verifier")


if __name__ == "__main__":
    main()
