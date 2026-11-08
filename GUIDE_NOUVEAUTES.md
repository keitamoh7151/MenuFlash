# MenuFlash — Nouveautés du super-admin et import du menu

## Installation (5 minutes)

1. Décompressez le zip **dans le dossier du projet** (`menuflash_mini`) et acceptez de remplacer les fichiers.
2. Dans PowerShell, depuis ce dossier :
   ```powershell
   python ajouter_branchement.py
   ```
   Il ajoute 2 blocs dans `app.py` (juste avant `from hardening import init_hardening`), sans toucher à vos autres modifications.
   Il est sans danger : relancé, il répond « Déjà branché ».
3. Poussez :
   ```powershell
   git add sa_suite.py sa_health.py sa_roles.py sa_support.py sa_privacy.py menu_import.py super_admin.py templates app.py
   git status
   git commit -m "Super-admin : santé d'activité, vue support, rôles, données personnelles, import de menu"
   git push
   ```
   Avant le commit, `git status` doit montrer les 6 fichiers `.py` comme **nouveaux**. Sinon Render plantera avec `ModuleNotFoundError`.

Aucune nouvelle dépendance. Les nouvelles tables (`restaurant_flags`, `staff_roles`, `privacy_requests`) se créent toutes seules au démarrage et sont incluses dans les sauvegardes.

## Où trouver chaque fonction

| Fonction | Où | Qui |
|---|---|---|
| Santé d'activité de tous les clients | Clients > Santé d'activité | Administrateur, Support |
| Santé + diagnostic d'un client | Fiche client, carte « Santé d'activité » | tous les rôles |
| Vue support (lecture seule) | Fiche client > Vue support | Administrateur, Support |
| Type de compte (Client / Pilote offert / Démo) | Fiche client, carte « Santé d'activité » | Administrateur |
| Seuil « sans commande » | Clients > Santé d'activité (bas de page) | Administrateur |
| Équipe d'administration et rôles | Système > Équipe d'administration | Administrateur |
| Données personnelles | Système > Données personnelles | Administrateur |
| Importer un menu | Restaurateur : Boutique > Importer un menu | Gestionnaire |

## Rôles

* **Administrateur** : tout. Votre compte actuel l'est déjà, sans rien faire.
* **Support** : clients, santé, vue support ; notes, déblocage de comptes, lien de réinitialisation, fermeture des sessions.
* **Finances (lecture seule)** : pilotage, paiements, reçus, relances, fiches clients. Ne modifie rien.

Tout ce qui n'est pas explicitement autorisé à un rôle lui est refusé (même une page ajoutée plus tard).
Une invitation envoie un lien de 72 h : la personne choisit son mot de passe, puis configure sa double authentification.

## À tester après le déploiement (15 min)

1. **Santé** : Clients > Santé d'activité. Votre restaurant de démo doit apparaître. Réglez le seuil à 3 jours pour le pilote.
2. **Type de compte** : mettez chez-awa en « Démo » : le revenu récurrent du Pilotage baisse du prix de sa formule.
3. **Vue support** : ouvrez la vue support de chez-awa, parcourez les 5 onglets. Aucun bouton d'écriture.
4. **Rôle Support** : invitez votre propre 2e adresse email en « Support », ouvrez le lien dans une fenêtre privée, choisissez le mot de passe, configurez la 2FA. Vérifiez qu'elle voit les clients mais pas Finances ni Sauvegardes.
5. **Import** : connectez-vous comme gestionnaire, Boutique > Importer un menu, téléchargez le modèle Excel, ajoutez 3 plats, importez. Vérifiez l'aperçu avant de confirmer.
6. **Données personnelles** : faites une commande à emporter test, cherchez le numéro, exportez, puis effacez. La ligne doit afficher « Client supprimé ».

## Limites à connaître

* La vue support est un **diagnostic en lecture seule**, pas une connexion « en tant que » : elle ne reproduit pas l'écran exact du restaurateur, mais montre toutes les données utiles (menu, commandes, équipe, réglages, problèmes détectés).
* Les téléphones et emails des clients finaux sont masqués dans la vue support. Ils ne sont visibles que dans la page Données personnelles (administrateur, avec trace).
* L'effacement d'un client anonymise ses commandes à emporter ; il ne supprime pas un compte restaurateur (fermez le compte depuis la fiche client).
* Pas d'alertes par email dans cette version (le diagnostic les signale à l'écran).
