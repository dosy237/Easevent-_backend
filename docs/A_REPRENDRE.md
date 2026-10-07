# Points à reprendre plus tard

Liste tenue à jour au fil du projet : sujets volontairement mis de côté.

## 1. Changement d'hébergement (prévu)

L'hébergement actuel (easevent.nitypulse.com, déploiement Docker via GitHub Actions)
va changer de compte. Au moment de la bascule :

- [ ] Nouveau serveur : variables du `.env` (SECRET_KEY, base de données, Redis, SendGrid,
      Cloudinary, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `PLATFORM_FEE_PERCENT`,
      `TWILIO_*`).
- [ ] **Garder la même `SECRET_KEY`** (ou `PHONE_ENCRYPTION_KEY` si elle est définie) :
      les numéros des invités sont chiffrés avec elle. Une nouvelle clé les rend illisibles.
- [ ] `PUBLIC_BASE_URL` et `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` avec le nouveau domaine.
- [ ] Secrets et cible du workflow GitHub Actions de déploiement.
- [ ] Stripe : mettre à jour l'URL du webhook (`https://<domaine>/api/stripe/webhook/`)
      et reprendre le nouveau secret `whsec_…`.
- [ ] Mobile Money (facultatif) : compte marchand Notch Pay, puis `NOTCHPAY_PUBLIC_KEY`,
      `NOTCHPAY_HASH_KEY` et l'URL du webhook `https://<domaine>/api/payments/mobile-money/webhook/`
      dans le tableau de bord Notch Pay. Sans ces variables, le choix Mobile Money n'apparaît pas.
- [ ] Premier compte administrateur : `python manage.py createsuperuser` (accès à l'onglet
      Administration de l'application ; il peut ensuite nommer d'autres administrateurs).
- [ ] Application mobile : `EXPO_PUBLIC_API_URL` puis reconstruire l'APK
      (les images viennent du backend : elles suivront automatiquement).
- [ ] Liens des e-mails (vérification, mot de passe oublié) : vérifier qu'ils pointent
      vers le nouveau domaine.
- [ ] Sauvegarde / migration des données et des médias.

## 2. Adresse d'envoi des e-mails

Expéditeur conservé pour l'instant : `dosyca35@gmail.com` (déjà validé sur SendGrid).
À remplacer plus tard par une adresse Eranis (eranistechnology@gmail.com ou domaine
dédié), après validation de l'expéditeur sur SendGrid.

## 3. Configuration Stripe (tableau de bord)

- [ ] Activer Connect (comptes Express).
- [ ] Webhook `https://easevent.nitypulse.com/api/stripe/webhook/` (événements des comptes
      connectés inclus) : `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
      `checkout.session.async_payment_failed`, `checkout.session.expired`, `account.updated`,
      `charge.refunded`, **abonnements** : `customer.subscription.created`,
      `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`.
- [ ] Abonnements : rien à créer dans Stripe, les produits « Easevent Standard » / « Easevent Pro »
      et leurs prix (9,99 €/mois, 99,90 €/an ; 24,99 €/mois, 249,90 €/an) sont créés au premier achat.
      Pour d'autres prix : créer les prix dans Stripe et renseigner `STRIPE_PRICE_STANDARD_MONTHLY`,
      `STRIPE_PRICE_STANDARD_ANNUAL`, `STRIPE_PRICE_PRO_MONTHLY`, `STRIPE_PRICE_PRO_ANNUAL`.
- [ ] Portail client (factures, carte, résiliation) : Stripe › Paramètres › Billing ›
      Portail client › « Activer » (une fois, en test puis en production).
- Remboursements automatiques : annulation d'un événement ou invitation retirée → remboursement
  intégral du participant (virement à l'organisateur et commission repris).
- [ ] Activer le prélèvement SEPA.
- Commission Easevent : 3 % (`PLATFORM_FEE_PERCENT`).

## 4. SMS des invitations (Twilio)

Les invitations par SMS sont créées même sans Twilio, mais marquées « SMS non envoyé ».
Pour les activer, ajouter au `.env` du serveur :

- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER` (numéro expéditeur) **ou** `TWILIO_MESSAGING_SERVICE_SID`

## 5. Limites d'invités par plan

Gratuit 50, Standard 500, Pro illimité (cahier des charges). Réglage : `PLAN_GUEST_LIMITS`
dans `easevent/settings.py`.

## 6. Celery (tâches de fond et planificateur)

Déjà dans `docker-compose.yml` : `celery` (worker) et `celery-beat` (planificateur).
Ils démarrent avec `docker compose up -d --build` (déploiement automatique).

- [ ] Dans le `.env` du serveur : `REDIS_URL=redis://redis:6379/0`
      (dans Docker, `localhost` désigne le conteneur lui-même, pas Redis).
- [ ] Après déploiement, vérifier : `docker compose ps` → `web`, `celery`, `celery-beat`, `redis` « Up ».
- [ ] Journaux : `docker compose logs -f celery celery-beat`.

Tâches :
| Tâche | Quand | Rôle |
|---|---|---|
| `invitations.tasks.send_invitations` | à chaque envoi / relance | emails M30 + SMS en arrière-plan |
| `invitations.tasks.retry_stuck_deliveries` | toutes les 10 min | rattrape un envoi resté « en cours » |
| `notifications.tasks.send_event_reminders` | toutes les heures | rappels J-7 / J-1 / jour J + email la veille |
| `notifications.tasks.send_daily_summaries` | 20:00 | bilan des confirmations (organisateurs) |
| `notifications.tasks.nightly_cleanup` | 03:30 | tickets / invitations expirés, notifications > 90 jours |

Si Redis ne répond pas, les invitations partent directement (plus lent, jamais perdu).
Si le worker est arrêté, la liste des invités (M13) envoie les invitations bloquées
depuis plus de 10 minutes, et les rappels / bilans sont calculés à l'ouverture de l'app.

## 7. Adresse publique du serveur

- [ ] `PUBLIC_BASE_URL=https://easevent.nitypulse.com` (en **https**) : utilisée dans les liens
      des emails (invitations, rappels), des PDF et des retours Stripe, y compris depuis Celery.

## 8. Notifications push (application fermée) — prêt, à activer

Le serveur envoie les push via le service Expo (qui relaie vers Firebase / Apple). À faire une fois :
- [ ] Compte Expo + `npx eas-cli login`, puis dans le dossier de l'application : `npx eas-cli init`
      (ajoute `extra.eas.projectId` à `app.json` : sans lui, l'app ne demande pas de jeton push).
- [ ] Android : projet Firebase (console.firebase.google.com) › Paramètres › Comptes de service ›
      « Générer une clé privée » (JSON), puis `npx eas-cli credentials` › Android › Push
      Notifications (FCM V1) › importer ce fichier JSON.
- [ ] iOS (plus tard) : `eas credentials` crée la clé APNs automatiquement.
- [ ] Facultatif : `EXPO_ACCESS_TOKEN` dans le `.env` si « Enhanced security for push » est activé
      sur expo.dev.
- Réglage : `PUSH_ENABLED=True` (par défaut). Chaque utilisateur choisit dans Notifications ›
      Préférences (téléphone, messages, réponses des invités, rappels, bilan du jour).

## 9. Messagerie instantanée (WebSocket) — à déployer

Service `realtime` (daphne, Django Channels + Redis) ajouté dans `docker-compose.yml`, port
local 8010. Messages, « en train d'écrire », accusés de lecture et badges arrivent en direct.
- [ ] `docker compose up -d --build` démarre aussi `realtime` (vérifier `docker compose ps`).
- [ ] nginx du serveur : reprendre le bloc `location /ws/` de `nginx.conf` (en-têtes Upgrade),
      ainsi que `client_max_body_size 15m` (photos de la messagerie) et l'en-tête
      `Access-Control-Allow-Origin` sur `/static/` (logo de la version web), puis `nginx -s reload`.
- [ ] Si le HTTPS est géré par certbot sur ce nginx : le bloc 443 doit contenir les mêmes
      `location` (certbot les recopie si on relance `certbot --nginx`).
Sans ce service, l'application continue de fonctionner : elle interroge le serveur toutes les 4 s.

## 10. Ouverture de l'app depuis les liens d'invitation et stores

Identifiant de l'application fixé dans `app.json` : **`com.eranis.easevent`** (Android et iOS).
- [ ] Confirmer cet identifiant AVANT la première publication (il ne peut plus changer ensuite).
      Si un APK a déjà été construit avec un autre identifiant, le dire : on alignera.

Variables du `.env` du serveur :
- [ ] `ANDROID_STORE_URL` = page Play Store (ex. `https://play.google.com/store/apps/details?id=com.eranis.easevent`).
      Le jeton d'invitation y est ajouté automatiquement (`referrer`) : après installation, l'app
      ouvre directement l'invitation.
- [ ] `IOS_STORE_URL` = page App Store (quand l'app iOS sera publiée).
- [ ] `APP_DOWNLOAD_URL` = lien direct de l'APK tant que l'app n'est pas sur le Play Store (facultatif).
- [ ] `ANDROID_CERT_SHA256` = empreinte SHA-256 du certificat de signature (EAS : `eas credentials`,
      ou Play Console › Intégrité de l'application) → les liens https://easevent.nitypulse.com/i/…
      s'ouvrent directement dans l'app, sans passer par le navigateur.
- [ ] `APPLE_TEAM_ID` (compte Apple Developer) → même chose sur iPhone.

Retrouver une invitation après installation :
- Android : automatique (referrer du Play Store).
- Tous : le numéro vérifié par SMS (Profil › Téléphone, ou bannière dans Mes tickets) et l'email
  vérifié rattachent automatiquement les invitations reçues. Nécessite Twilio (section 4).


## 11. Générer l'APK / l'AAB

Fichiers prêts : `eas.json` (profils `preview` = APK à installer, `production` = AAB pour le
Play Store), `app.json` (nom « Easevent », icône, icône Android adaptative + monochrome,
écran de démarrage, permissions limitées : contacts, appareil photo, notifications).
- [ ] `npm ci` puis `npx eas-cli build -p android --profile preview` → lien de l'APK.
- [ ] Play Store : `npx eas-cli build -p android --profile production` puis
      `npx eas-cli submit -p android`.
- L'adresse du serveur est fixée dans `eas.json` (`EXPO_PUBLIC_API_URL`) : à changer en cas de
  nouvel hébergement (toujours en https).
- Vérifications locales déjà faites : dépendances alignées sur Expo SDK 54, génération du projet
  Android (`expo prebuild`), compilation JavaScript Android (Hermes) sans erreur.

## 12. Connexion Google / Apple (masquée pour l'instant)

Les boutons n'apparaissent pas tant que `EXPO_PUBLIC_OAUTH_ENABLED` n'est pas à `true`.
- [ ] Fournir : identifiants OAuth Google (clients Android, iOS et Web) et, pour iOS,
      « Sign in with Apple » (Apple Developer). La connexion sera alors branchée.

## 13. Textes juridiques

- [ ] Faire relire les Conditions d'utilisation (écran CGU) et la politique de confidentialité
      par un juriste : société, médiateur de la consommation, âge minimum, conditions de
      remboursement des abonnements.

## 14. Mini-site IA (app `minisite`)

**Principe** : l'IA compose, elle ne code pas. Un mini-site est un plan JSON (sections × variantes × thème) dessiné par l'application avec ses propres composants. Aucun code produit par l'IA n'est exécuté. Le mini-site n'existe **que dans l'application**, sans adresse web publique ; il suit les mêmes règles d'accès que l'événement.

- **Bibliothèque** : `minisite/catalog.py`, reflétée dans `easevent_frontend/components/minisite/catalog.js`.
  - 15 types de sections, 51 variantes ;
  - 12 paires de polices, 9 harmonies de couleurs, 9 ornements, 4 formes, 3 densités ;
  - 3 fonds par section.
- **Lisibilité** : `minisite/colors.py` impose les contrastes WCAG, quelle que soit la couleur choisie (testé sur plus de 5 000 palettes au hasard).
- **6 propositions par génération**, une par direction artistique (éditorial, photo immersive, minimal, festif, luxe, ludique). Chaque disposition a une empreinte unique en base : jamais deux fois la même, ni entre les 6, ni avec les mini-sites déjà générés.
- **Faits** : la date, le lieu, le prix, la tenue et les questions fréquentes viennent de l'événement. L'IA n'écrit que les textes d'ambiance, et un texte contenant une heure ou un prix inventés est rejeté.
- **Quotas de génération par événement** : Gratuit 3, Standard 15, Pro illimité (`MINISITE_GENERATION_LIMITS`). Une génération échouée n'est pas décomptée.

### Modèles d'IA (gratuits, facultatifs)
Sans clé, le générateur de secours produit quand même les 6 propositions avec des textes rédigés. Chaque clé ajoutée améliore le résultat.

| Variable | Où la créer | Rôle |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | Rédaction (1er choix) |
| `GROQ_API_KEY` | https://console.groq.com/keys | Relecture (1er choix), rédaction en repli |
| `OPENROUTER_API_KEY` | https://openrouter.ai/settings/keys | Repli pour tous les rôles |
| `MISTRAL_API_KEY` | https://console.mistral.ai/api-keys | Direction artistique **seulement**, sans aucune donnée saisie |

Réglages facultatifs :
- `MINISITE_GEMINI_MODEL`, `MINISITE_GROQ_MODEL`, `MINISITE_OPENROUTER_MODEL`, `MINISITE_MISTRAL_MODEL` : changer de modèle sans toucher au code ;
- `MINISITE_AI_TIMEOUT` (25 s) ;
- `MINISITE_ASYNC` (True : génération par Celery).

### Journal d'apprentissage (futur modèle Easevent)
- **Emplacement** : `MINISITE_DATASET_DIR`, par défaut `data/minisite/AAAA-MM.jsonl`, exclu de Git. À monter sur un volume persistant en production.
- **Contenu** : une ligne par génération, choix, retouche ou régénération, avec les réponses de chaque modèle, sa durée et son succès.
- **Données personnelles** : emails, numéros et liens sont retirés ; l'organisateur est pseudonymisé.
- **Usage** : quand il y aura assez de données, on évaluera les modèles et on entraînera le nôtre.
- **Arrêt** : `MINISITE_DATASET_ENABLED=False`.
