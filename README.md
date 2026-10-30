# MenuFlash Mini — Starter technique

MVP de menu par QR code pour restaurants, avec identité visuelle unifiée,
protections de sécurité renforcées, suggestions de vente additionnelle,
mode multi-restaurant, et désormais une **navigation réorganisée** et une
**identité de marque propre à chaque restaurant**.

## Démarrage local (Windows / PowerShell)

```powershell
py -3.13 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
$env:FLASK_SECRET_KEY = "change-moi"
python seed.py
python app.py
```

- **Page de présentation** : http://127.0.0.1:5000/
- **Inscription en libre-service** : http://127.0.0.1:5000/inscription
- Menu public de démo : http://127.0.0.1:5000/m/chez-awa
- Connexion gestionnaire : `awa@example.com` / `motdepasse123`
- Connexion cuisinier (accès limité) : `cuisine@example.com` / `cuisine1234`
- Panel super-admin : `admin@menuflash.com` / `SuperAdmin123!`

⚠️ **Important** : `requirements.txt` épingle `Flask==3.0.3` et
`Werkzeug==3.0.6`. Vérifie toujours avec
`pip show flask werkzeug flask-login` avant de signaler un bug étrange
(une version plus récente de Werkzeug peut provoquer des erreurs de
session difficiles à diagnostiquer avec Flask-Login 0.6.3).

## Nouveau : navigation réorganisée en menus déroulants

La barre de navigation du back-office (qui alignait une dizaine de
boutons et finissait par se replier sur deux lignes) est maintenant
regroupée en :
- **2 liens directs** pour les actions les plus fréquentes : **Menu**
  (gestion des plats) et **Commandes** (avec le badge du nombre de
  commandes en cours, désormais visible sur toutes les pages).
- **3 menus déroulants thématiques** :
  - **Boutique** : Voir le menu public, Codes QR, Apparence & logo.
  - **Gestion** : Statistiques, Équipe, Historique.
  - **Compte** : Journal de sécurité, Mot de passe, Déconnexion.

Cette barre de navigation est désormais centralisée dans un seul fichier
(`templates/_admin_navbar.html`), inclus par les 8 pages du back-office —
une évolution future n'aura qu'un seul endroit à modifier, au lieu de
risquer que les pages divergent entre elles.

## Nouveau : identité visuelle propre à chaque restaurant

Une fois connecté à son tableau de bord, un restaurateur voit désormais
**le nom et le logo de SON restaurant** à la place de la marque
« MenuFlash Mini » dans la barre de navigation — exactement comme s'il
s'agissait de son propre logiciel.

### Ajouter un logo
Depuis **Boutique > Apparence & logo**, un formulaire permet de
téléverser une image (JPG, PNG ou WEBP). Particularité importante : si
l'image a un fond transparent (PNG), cette transparence est **préservée**
dans le fichier final — un logo ne se retrouve jamais avec un carré blanc
disgracieux sur la barre de navigation sombre. Sans transparence, le
fichier est converti en WEBP (plus léger). Le logo apparaît ensuite :
- dans la barre de navigation de tout le back-office,
- sur le menu public que voient les clients (déjà géré par le composant
  existant, aucun changement nécessaire côté menu public),
- peut être supprimé à tout moment (la marque « MenuFlash Mini » reprend
  alors sa place).

### Ce qui garde la marque MenuFlash Mini
- Le **panel super-admin** (qui gère l'ensemble de la plateforme, pas un
  restaurant précis) conserve sa propre barre de navigation inchangée.
- Les pages **avant connexion** (page de présentation, connexion,
  inscription, mot de passe oublié) : logique, puisqu'aucun restaurant
  n'est encore identifié à ce stade.

Ces deux changements ont été vérifiés par une trentaine de tests
automatisés : présence correcte des 3 menus déroulants et des 2 liens
directs sur les 8 pages concernées, affichage du nom/logo du restaurant
(et non de la marque de la plateforme) dans chaque cas, préservation
réelle de la transparence PNG sur le fichier enregistré sur disque,
redimensionnement correct, gestion propre d'un fichier corrompu (sans
erreur serveur), suppression du logo, bascule correcte de l'icône
générique, badge de commandes désormais visible sur toutes les pages
(plus seulement le tableau de bord et la page Commandes), et isolation
confirmée pour le compte super-admin (marque plateforme conservée) et le
compte cuisinier (navigation strictement minimale, badge "Cuisine").

## Fonctionnalités (rappel)

- Page de présentation + inscription en libre-service.
- Commande en ligne, notifications cuisine instantanées, annulation de
  commande par le client.
- Suggestions de vente additionnelle, mode multi-restaurant.
- Thèmes en direct, statistiques de ventes, comptes d'équipe à rôles,
  avis clients par plat, interface FR/EN, mode rush, historique des
  modifications.
- Sécurité : CSRF, limitation de débit, en-têtes HTTP (CSP, HSTS),
  cookies durcis, verrouillage de compte après 5 échecs, invalidation de
  session au changement de mot de passe, vérification d'email,
  réinitialisation sécurisée, journal d'audit.

Les emails (vérification, réinitialisation) nécessitent un serveur SMTP
réel pour être délivrés ; tant qu'aucun n'est configuré, les liens sont
journalisés côté serveur (pratique pour tester en local, à corriger
avant toute mise en production réelle).

## Déploiement sur Render

- Build command : `pip install -r requirements-render.txt`
- Start command : `gunicorn --worker-class gthread --workers 1 --threads 4 app:app`
- Variables d'environnement : `FLASK_SECRET_KEY`, `SECURE_COOKIES=true`,
  `FORCE_HTTPS=true`, configuration `MAIL_*` pour les emails réels.
- Ajouter une base PostgreSQL Render + variable `DATABASE_URL`.
