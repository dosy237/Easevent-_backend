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
- [ ] Webhook (événements des comptes connectés inclus) : `checkout.session.completed`,
      `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`,
      `checkout.session.expired`, `account.updated`, `charge.refunded`.
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

## 8. Plus tard : notifications push (application fermée)

- [ ] Projet Firebase + `google-services.json` (Android) et compte Expo (EAS) pour les jetons push.
      Les tâches Celery existantes enverront alors aussi les push.

## 9. Messagerie en temps réel (plus tard, facultatif)

La messagerie (M15 / M16) se met à jour toutes les 4 s quand une conversation est
ouverte (seuls les nouveaux messages sont téléchargés). Pour du temps réel strict,
Django Channels + Redis (déjà dans requirements.txt) pourront remplacer ce mécanisme
sans changer l'API.

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
