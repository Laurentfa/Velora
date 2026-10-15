# Velora

Client mail de bureau avec IA (Claude), 100 % Python (NiceGUI). Mac et Windows.
Boîtes : Exchange hébergé (EWS) + IMAP/SMTP. Synchro locale (SQLite), résumés, tri, brouillons de réponse.

## Utiliser
- **Appli installée** : ouvrir « Velora » (Mac : `dist/Velora.app`, à glisser dans Applications).
  Réglages (clé Claude, mots de passe, IA par boîte) : bouton ⚙ en bas à gauche.
- **Depuis les sources** : `.venv/bin/python main.py`
- **Depuis le téléphone** : `.venv/bin/python main.py --server`, puis ouvrir `http://<ip-du-Mac>:8765`
  (via Tailscale) et saisir le jeton d'accès (⚙ Paramètres).

## Boîtes mail
⚙ Paramètres → « Boîtes mail » : ajouter, modifier ou retirer une boîte Exchange (EWS) ou IMAP/SMTP (préréglages Gmail, iCloud,
IONOS, OVH, Infomaniak). « Tester la connexion » vérifie la réception et l'envoi avant d'enregistrer. Les réglages sont dans
`accounts.json` (sans secret) ; les mots de passe dans le `.env` (`PW_<clé>`). Retirer une boîte efface ses copies locales
et ses réglages, jamais les mails sur le serveur. Chaque boîte a un interrupteur (activée / désactivée) : désactivée,
elle est suspendue (aucune connexion), masquée partout (listes, compteurs, brief, recherche, envoi) et ses copies locales sont
conservées, donc la réactiver est immédiat. Une boîte en panne n'empêche pas la synchro des autres.

## Synchronisation
- **Incrémentale** : Exchange par `SyncFolderItems` (seuls les changements sont transférés), IONOS par comparaison UID/état.
  Exchange est interrogé toutes les 20 s, IONOS toutes les 45 s (`SYNC_INTERVAL_EWS`, `SYNC_INTERVAL_IMAP`).
- **Réconciliation** : suppressions, déplacements et lectures faits dans Outlook sont répercutés dans l'appli.
- **Dossiers** : la boîte de réception est toujours suivie ; les autres se choisissent dans ⚙ Paramètres.
  Premier chargement d'un dossier : les 150 derniers messages (`SYNC_LIMIT`). L'IA n'analyse que la boîte de réception.
- **Copie et pièces jointes** : lignes À / Cc affichées, pièces jointes listées (nom, taille) et téléchargeables ;
  sur le Mac elles sont enregistrées dans Téléchargements puis montrées dans le Finder, jamais ouvertes automatiquement.
  « Répondre à tous » reprend À et Cc (sans votre adresse). L'IA ne reçoit que les noms des pièces jointes, jamais leur contenu.
- **Mise en forme (éditeur riche)** : police, taille, gras/italique/souligné/barré, couleur et surlignage, alignement, listes, retraits,
  citations, liens, séparateur, tableaux, emojis, exposant/indice, effacer le format, plein écran ; images dans le corps du message
  (réduites, envoyées comme images incorporées `cid:`) ; signature mise en forme (⚙ Paramètres). Les réponses citent le message
  d'origine (De / Envoyé / À / Objet) dans l'éditeur. Envoi en HTML + version texte de secours, pour Exchange et IMAP/SMTP.
- **Épingler** : l'icône punaise de la barre d'actions épingle une conversation en haut de la boîte de réception. L'épingle est le drapeau
  du message sur le serveur (Exchange : drapeau Outlook, IMAP : `\\Flagged`), dans les deux sens ; les drapeaux posés ailleurs apparaissent épinglés.
- **Modèles de réponse** : menu « Modèles » de l'éditeur (3 modèles de départ, créer / modifier / supprimer, « enregistrer mon message
  comme modèle »). Variables : `{{prenom}}` `{{nom}}` `{{date}}` `{{objet}}` (remplies selon le message auquel on répond).
- **Brouillons automatiques** : enregistrés toutes les 5 s (texte, destinataires, objet, pièces jointes, images), vue « Brouillons »,
  « Annuler » propose de garder ou supprimer. Locaux à l'appli (non synchronisés avec le dossier Brouillons du serveur).
- **Améliorer avec l'IA** : corriger l'orthographe et la grammaire (avec liste des corrections à valider), rendre plus concis,
  plus formel, plus chaleureux, traduire en anglais. Seul le texte écrit est traité (ni signature, ni citation, ni images).
- **Écrire** : « Nouveau message » (choix de la boîte d'envoi, Cc, pièces jointes jusqu'à 25 Mo, rédaction IA facultative) ;
  les réponses peuvent aussi porter des pièces jointes et citer le message d'origine. Aucun envoi sans confirmation.
- **Conversations** : les mails d'un même fil sont regroupés (Exchange : identifiant de conversation natif ; IONOS : en-têtes
  Message-ID / References). Les « envoyés » sont suivis d'office pour que les fils soient complets. Liste : un élément par fil avec le
  nombre de messages ; lecture : messages empilés, le dernier et les non lus dépliés, citations masquées, résumé IA du fil.
  Archiver / supprimer / déplacer s'appliquent à toute la conversation. Désactivable dans ⚙ Paramètres (`CONVERSATIONS=0`).
- **Gestes sur la liste** : glisser vers la droite = lu / non lu ; vers la gauche = boutons Archiver / Supprimer (un grand glissement
  archive). Tactile, souris, et balayage à deux doigts du trackpad Mac. Supprimer exige un appui sur le bouton rouge (corbeille du serveur).
- **Glisser-déposer** (souris) : une ligne glissée sur un dossier de la barre latérale déplace le mail, ou toute sa conversation ;
  pendant le glissement un panneau propose aussi Archive, Corbeille et « Autre dossier… ». À la souris, glisser = déplacer ;
  le balayage lu/archiver/supprimer est réservé au tactile et au trackpad.
- **Mails HTML** : le HTML d'origine est nettoyé (nh3 : plus de scripts, formulaires, cadres, liens javascript:) puis affiché dans un
  cadre isolé (sandbox + politique CSP : aucun chargement externe). Images distantes bloquées tant qu'on ne clique pas « Afficher les
  images » (pixels espions) ; images incrustées (signatures) résolues sur le serveur ; liens : adresse réelle montrée + confirmation.
  Bouton « Texte brut » pour revenir au texte. Pièces jointes et IA utilisent toujours le texte, jamais le HTML.
- **Colonnes redimensionnables** : glisser les deux bordures (barre latérale | liste | lecture) ; double-clic sur une bordure =
  largeur d'origine. Les largeurs sont mémorisées. Limites : la zone de lecture garde toujours au moins 380 px.
- **Actions** : archiver, déplacer, supprimer (corbeille du serveur), marquer non lu, répercutés sur le serveur.
- **Notifications serveur** (Exchange streaming / IMAP IDLE) : code présent mais désactivé (`PUSH_NOTIFICATIONS=1` pour l'essayer) :
  testés sur ces deux hébergeurs, aucun événement n'est livré.

## Sécurité et confort (version récente)
- **Alerte hameçonnage** : bandeau rouge et badge « Suspect » quand un message se fait passer pour une marque (nom ou objet) depuis une autre adresse,
  contient des liens trompeurs, ou un ton de menace. Règles locales, sans IA ; moins de méfiance pour vos correspondants connus.
- **Hameçonnage, contrôles ajoutés** : résultats SPF / DKIM / DMARC lus dans les en-têtes de votre serveur (quand il les fournit), usurpation d'un collègue
  repérée avec Exchange (adresse interne mais message venu de l'extérieur), « Répondre à » vers un autre domaine, pièces jointes à risque (programmes,
  double extension, macros, pages web, archives avec mot de passe ; confirmation avant de les enregistrer), et avis de l'IA à la demande (icône bouclier
  du message) : l'IA explique, ne décide jamais, et ne peut pas effacer une alerte grave.
- **Annuler l'envoi / envoi programmé** : « Envoyer » ouvre un choix (maintenant, dans 1 h, demain 8 h, lundi 8 h, date précise) ; envoi immédiat =
  délai d'annulation réglable (5 à 30 s) avec bouton « Annuler l'envoi ». Vue « En attente d'envoi ». « Me rappeler si je n'ai pas de réponse »
  crée une relance (vue « À relancer »).
- **Reporter un mail** (⏲) : masqué jusqu'à l'heure choisie, puis non lu et en tête (« À revoir »). Vue « Reportés ». **Tâches** (☑) : liste simple,
  créable depuis un mail (titre et échéance proposés par l'IA à la demande).
- **Raccourcis clavier** (`?` pour la liste) : j/k, r, a, e, #, p, u, s, t, c, /.
- **Se désabonner** d'une liste de diffusion (en-tête List-Unsubscribe, « un clic » RFC 8058 sans ouvrir de page ; jamais proposé sur un message suspect).
- **Signature par boîte** (Modifier la boîte), qui suit le choix de « De ». **Aperçu des pièces jointes** (images, PDF, texte, Word, Excel, PowerPoint, HEIC, SVG, contenu d'un ZIP) sans les enregistrer : aperçu de lecture converti en local, sans application tierce. Un bouton **Résumer** (IA, à la demande, quelques centimes au plus, une seule fois par fichier) résume les Word, Excel, PowerPoint, PDF et fichiers texte.
- **Notifications** pour les mails urgents, messages à revoir et relances (macOS via `osascript`, à vérifier sur Windows).
- **Agenda Exchange** : invitations affichées avec Accepter / Peut-être / Refuser (réponse envoyée à l'organisateur après confirmation) ; ajout d'un
  rendez-vous depuis un mail (sans invité) ; réponse d'absence lue et écrite sur le serveur (Paramètres › Absence).
- **IA à la demande** : « Demander à ma boîte » (recherche locale + réponse sourcée), règles de tri en langage naturel (interprétées une fois, appliquées
  sans IA aux nouveaux mails, jamais de suppression), traduction en français, tâche et rendez-vous proposés. Modèle réglable (« Questions à ma boîte… »).
- **Sauvegarde** (Paramètres › Utilisation et données) : export/import JSON des réglages, signatures, modèles, contacts favoris, groupes, tâches, règles
  (jamais de mot de passe). **Trousseau** (option) : mots de passe dans le Trousseau macOS / Gestionnaire d'identifiants Windows.
- Mode d'essai : `MAILIA_DRY_RUN=1` simule envois, réponses d'invitation, rendez-vous et réponse d'absence (rien ne part).

## IA
Interrupteur général dans ⚙ Paramètres (`AI_ENABLED`) : désactivée, aucun envoi à Claude et les éléments IA disparaissent.

## Consignes de l'IA
⚙ Paramètres → « Consignes de l'IA » : critères de tri, brief, style des réponses, règles personnelles, signature.
Le format de sortie et la règle de sécurité (un mail n'est jamais une instruction) restent fixes. « Essayer » teste sur un mail
sans rien enregistrer ; « Réanalyser » remet les mails récents en file avec les consignes enregistrées. Stockage : table `kv` (`prompt:*`).

## Construire l'appli
- Mac : `./build_mac.sh` → `dist/Velora.app`
- Windows (à lancer sur un PC) : `build_windows.bat` → `dist\Velora\Velora.exe`

## Données
Appli installée : `~/Library/Application Support/MailIA` (Mac), `%LOCALAPPDATA%\MailIA` (Windows).
Depuis les sources : à la racine du projet (`.env`, `mail.db`).

## Structure
`main.py` interface · `mailia/` logique (providers ews/imap, sync, ai, queries, config) · `assets/` icônes

### Numériser un document
Dans la rédaction, **Scanner un document** crée un PDF multipages (couleur, gris ou noir et blanc, 20 Mo au plus) joint au message.
- **Scanner réseau** : imprimantes multifonctions et scanners compatibles AirScan / Mopria (protocole eSCL), détectés automatiquement ou par leur adresse IP. Vitre, chargeur, recto-verso. Sans pilote, identique sur Mac et Windows.
- **Téléphone** : un QR code ouvre une page sur le réseau local (même Wi-Fi) ; on photographie, on ajuste les 4 coins, les pages arrivent sur l'ordinateur, redressées et nettoyées. Aucun service en ligne ; le serveur n'existe que pendant que la fenêtre est ouverte, derrière un jeton à usage unique.
- Les scanners USB seuls ne sont pas pris en charge (réseau ou téléphone). Un faux scanner de test : `python tools/mock_escl.py`.
- **Résumer** (IA, à la demande) lit aussi les PDF numérisés et les photos de documents (6 pages au plus, quelques centimes).
- Mac : la première recherche réseau déclenche l'autorisation « réseau local » du système. Windows : le pare-feu peut demander d'autoriser l'accès pour le téléphone (réseaux privés).

### Confort, sécurité et IA maîtrisée
- **Avant l'envoi** : avertissement si vous parlez d'une pièce jointe sans fichier, objet vide, destinataire jamais contacté, plus de 10 destinataires (local, gratuit).
- **Signature** : les images distantes (logo hébergé en http://) sont téléchargées une fois, gardées sur le disque et intégrées à chaque envoi.
- **Glisser-déposer** de fichiers sur la rédaction, **collage** d'images (captures d'écran) dans le texte.
- **Recherche avancée** : `de:`, `à:`, `objet:`, `pj:oui`, `non-lu`, `depuis:`/`avant:` (date, 7j, 2s, 3m), `cat:`, `dossier:`, exclusion avec `-`. Recherches enregistrables dans la colonne de gauche.
- **Nettoyer ma boîte** : expéditeurs les plus bavards, part lue, tout archiver (jamais de suppression) ou se désabonner.
- **Expéditeurs prioritaires ★** : toujours urgents, avec notification. **Apprentissage** : vos corrections de classement servent d'exemples à l'IA, et deux corrections identiques sur un même domaine classent tout le domaine. **Résumé quotidien** (facultatif) en notification.
- **IA maîtrisée** : interrupteur général, boîtes et expéditeurs/domaines exclus (jamais envoyés à l'IA), suivi du coût par fonction et par jour, budget mensuel avec alerte à 80 % et blocage facultatif.
- **Chiffrement de la base** (SQLCipher, AES-256, clé dans le trousseau du système, clé de récupération à conserver) et **verrouillage** par code (Touch ID sur Mac, verrouillage après inactivité). Windows Hello n'est pas pris en charge.
- **Première utilisation** : assistant (adresse → réglages détectés automatiquement → connexion → IA facultative).
- **Connexion OAuth** (Microsoft 365 / Outlook.com, Google) : nécessite un identifiant d'application que vous créez gratuitement chez l'éditeur, à placer dans le fichier de configuration : `OAUTH_MICROSOFT_CLIENT_ID` (application « client public » avec redirection `http://localhost`) ; `OAUTH_GOOGLE_CLIENT_ID` et `OAUTH_GOOGLE_CLIENT_SECRET` (application « ordinateur », portée `https://mail.google.com/`). Sans identifiant, Gmail passe par un mot de passe d'application. Un faux fournisseur de test : `python tools/mock_oauth.py`. L'API Microsoft Graph et EWS en OAuth ne sont pas pris en charge.
- **Langues** : français et anglais (Paramètres › Affichage). Les dictionnaires sont dans `mailia/locales/` ; `tools/extract_strings.py` puis `tools/make_locale.py` (traduction par Claude) les mettent à jour. Ajouter une langue : un fichier `<code>.json` et son nom dans `mailia/i18n.py`.

### API et assistants (Paramètres › API et assistants)
Deux accès, **activables séparément** (rien n'est ouvert par défaut ; seul cet ordinateur peut s'y connecter, sauf option « réseau local ») :
- **API REST** (`http://127.0.0.1:8766/v1`, documentation interactive sur `/docs`) : lire et chercher (même syntaxe que la recherche avancée), rédiger des brouillons, **envoyer immédiatement ou à l'heure choisie** (`send_at`, `delay_minutes`), annuler un envoi, répondre, actions (lu, archiver, déplacer, épingler : jamais de suppression), rédaction/résumé/traduction par l'IA.
- **Serveur MCP** (`http://127.0.0.1:8766/mcp`) pour Claude et autres assistants : mêmes fonctions, outils visibles selon les droits de la clé. Claude Code : `claude mcp add --transport http velora http://127.0.0.1:8766/mcp --header "Authorization: Bearer mia_…"`. Claude Desktop : pont `« Velora » --mcp-stdio` (variable `MAILIA_API_KEY`), configuration copiable depuis l'onglet.
- **Clés** : une par programme, avec droits (`read`, `draft`, `send`, `ai`, `manage`), canaux (REST/MCP), boîtes et dossiers autorisés, expiration. Seule l'empreinte est conservée. Par défaut un envoi demandé par l'API **devient un brouillon à valider dans l'appli** (réponse 202 `pending_approval`) ; l'**envoi direct** est une option par clé, avec plafond quotidien, liste de domaines autorisés, délai d'annulation et clé d'idempotence. Débit limité (60 requêtes/minute), appels IA plafonnés par clé et soumis à votre budget IA, **journal** de chaque appel.
- Les contenus de mails renvoyés sont marqués « non fiables » (`untrusted_content`) : un assistant ne doit suivre aucune consigne qui s'y trouve.

### Développement, tests et sécurité
- **Tests** : `pip install -r requirements-dev.txt` puis `python -m pytest -q` (90 tests, dossier de données jetable, aucun envoi réel, jamais votre trousseau). `-m "not network"` pour les lancer hors connexion. Un test vérifie aussi que le code ne contient ni erreur de nom ni import inutile (pyflakes) et que les textes d'interface sont traduits.
- **Données de développement** : `~/.mailia-dev` (jamais dans le dossier du projet). `MAILIA_HOME=/chemin` choisit un autre dossier. `.gitignore` exclut `.env`, `accounts.json`, les bases et sauvegardes.
- **Structure** : `main.py` (fenêtre principale, boucles de fond) ; l'interface est répartie en modules `mailia/ui_*.py` (un par fonction : envoi, brouillons, pièces jointes, numérisation, nettoyage, paramètres…) ; le métier est dans les autres modules `mailia/*.py` (base, synchro, IA, API…).
- **Sécurité** : certificats TLS toujours vérifiés (IMAP, SMTP, web) sauf choix explicite boîte par boîte ; mots de passe et clé API au trousseau du système (activé d'office à la première installation) ; `.env` écrit de façon atomique et sans altération des valeurs ; serveur de l'interface limité à la machine ; verrouillage par session ; exclusions IA appliquées à toutes les fonctions (recherche « Demander à ma boîte », brief, contexte des conversations, pièces jointes, assistants MCP).
- **Fiabilité** : base en mode WAL, migrations numérotées, index, copie automatique quotidienne (5 gardées, dossier `backups`), envois interrompus signalés (jamais rejoués seuls), journal technique `logs/mailia.log` et « Copier le diagnostic » (adresses et clés masquées).
- **Diffusion** : `requirements.lock` (versions exactes), `.github/workflows/ci.yml` (tests + construction Windows et Mac), `scripts/sign_notarize_mac.sh` et `scripts/sign_windows.bat` (signature et notarisation : non testés, il faut un compte Apple Developer et un certificat de signature).


## Diffuser Velora gratuitement (GitHub)

Rien n'est payant : les versions sont hébergées sur GitHub, construites par GitHub Actions (Mac et Windows) et les mises à jour sont signées
avec votre propre clé (`tools/sign_release.py`), pas avec un certificat Apple ou Microsoft.

**Une seule fois**
1. Créer un dépôt GitHub *public* (il peut ne contenir que le code nécessaire à la construction) et y envoyer le projet.
2. Dans le dépôt : Settings › Secrets and variables › Actions › New repository secret, nom `UPDATE_PRIVATE_KEY`, valeur = contenu du fichier
   `~/velora-update-private.key` (créé par `python tools/sign_release.py --new-key`). Sauvegardez ce fichier ailleurs : sans lui, plus de mise à jour possible.

**À chaque version**
```bash
git tag v1.1.0 && git push --tags
```
GitHub construit, signe et publie : `Velora-1.1.0-mac.dmg` et `Velora-1.1.0-setup.exe` (première installation, à donner aux nouveaux
utilisateurs avec `INSTALLER.md`), `…-mac.zip` et `…-setup.exe` + `.sig` (utilisés par la mise à jour automatique). Les Velora déjà installées
proposent la nouvelle version dans la journée.

Essai en local (Mac) : `./build_mac.sh && scripts/package_mac.sh 1.1.0` crée `dist/release/`.
