# Saisir les clés de service (sans risque d'erreur)

Les clés (Stripe, Notch Pay, Twilio, SendGrid, Cloudinary, Google Maps, IA…) sont
**chiffrées dans la base** du serveur. Elles se saisissent soit dans Django admin, soit
en une seule commande. Chaque clé est **vérifiée** avant l'enregistrement, puis **testée en
direct** auprès de son service. Ce test lit seulement : aucun paiement, SMS ou email n'est envoyé.

## Avant tout : le serveur doit exister

Ces étapes se font **après le déploiement**, une fois le serveur et le domaine en place (voir
`A_REPRENDRE.md`). Tant que rien n'est déployé, il n'y a pas de Django admin en ligne.

## Étape 1 — Créer votre compte administrateur (une seule fois)

Sur le serveur, dans le dossier du projet :

```bash
docker compose exec web python manage.py createsuperuser
```

Saisissez votre email, votre prénom, votre nom et un mot de passe solide.

## Étape 2 (méthode conseillée) — Toutes les clés en une commande

1. Sur le serveur, créez un fichier temporaire, par exemple `/root/cles.txt`.
2. Mettez une clé par ligne, au format `NOM=valeur` (les guillemets et les espaces en trop sont retirés) :

   ```
   STRIPE_SECRET_KEY=sk_live_...
   STRIPE_WEBHOOK_SECRET=whsec_...
   NOTCHPAY_PUBLIC_KEY=pk....
   NOTCHPAY_HASH_KEY=...
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_MESSAGING_SERVICE_SID=MG...
   SENDGRID_API_KEY=SG....
   CLOUDINARY_CLOUD_NAME=...
   CLOUDINARY_API_KEY=...
   CLOUDINARY_API_SECRET=...
   GOOGLE_MAPS_API_KEY=AIza...
   GEMINI_API_KEY=...
   GROQ_API_KEY=gsk_...
   OPENROUTER_API_KEY=sk-or-...
   ```

3. Lancez l'import :

   ```bash
   docker compose exec web python manage.py keys import /root/cles.txt
   ```

   - **Fichier effacé** : à la fin, le fichier est écrasé puis supprimé.
   - **Résultat par clé** : enregistrée, avec le résultat du test (OK ou ÉCHEC), ou refusée avec la raison. Exemple de refus : « clé publique Stripe collée à la place de la clé secrète ».

## Étape 2 (autre méthode) — Dans Django admin

1. Ouvrez `https://VOTRE-DOMAINE/admin/` et connectez-vous avec le compte de l'étape 1.
2. Allez dans **Administration › Clés de service (chiffrées) › Ajouter**.
3. Choisissez le service, collez la clé, puis cliquez sur **Enregistrer**.
4. Le résultat du test en direct s'affiche en haut de la page, en vert ou en rouge.
5. Pour retester plus tard, cochez les clés puis choisissez l'action **« Tester les clés sélectionnées »**.

## Vérifier à tout moment

```bash
docker compose exec web python manage.py keys status   # où se trouve chaque clé (base / environnement / manquante)
docker compose exec web python manage.py keys check    # test en direct de toutes les clés
docker compose exec web python manage.py keys set STRIPE_SECRET_KEY   # saisie masquée d'une seule clé
```

## Garde-fous

- **Clés jamais réaffichées** : on ne voit que les 4 derniers caractères. Chaque modification est tracée.
- **Confusions bloquées**, pour les formats stables :
  - Stripe : `sk_` / `whsec_` ;
  - Twilio : `AC…` / `MG…` ;
  - SendGrid : `SG.`.
- **Format inhabituel pour un autre service** : un avertissement s'affiche, et le test en direct tranche. Google utilise par exemple aujourd'hui des clés en « AQ. ».
- **Clés de test** : une clé Stripe ou Notch Pay de **test** déclenche un avertissement (pas de vrais paiements).
- **Clé de chiffrement** : la clé qui chiffre les autres (`KEYS_ENCRYPTION_KEY`, ou à défaut `SECRET_KEY`) reste dans le fichier `.env` du serveur. Ne la changez pas sans ressaisir les clés.

## Emails : SendGrid, puis Gmail en secours

Les emails (code de vérification, mot de passe oublié, invitations) partent **par SendGrid**.
Si SendGrid échoue, ou s'il n'est pas configuré, ils partent **par Gmail** (SMTP), à condition
que le secours soit configuré.

### Configurer le secours Gmail (5 minutes)

1. Sur le compte Google qui enverra les emails (par exemple `dosyca35@gmail.com`), activez la
   **validation en deux étapes** : myaccount.google.com › Sécurité.
2. Créez un **mot de passe d'application** : myaccount.google.com › Sécurité › Mots de passe des
   applications. Nommez-le « Easevent ». Google affiche 16 lettres.
3. Dans le `.env` du serveur, ajoutez :

   ```
   EMAIL_HOST_USER=dosyca35@gmail.com
   EMAIL_HOST_PASSWORD=abcd efgh ijkl mnop
   ```

   Les espaces du mot de passe sont retirés automatiquement. Le mot de passe peut aussi être
   saisi, chiffré, dans l'administration (Clés de service › « Gmail — mot de passe d'application »).
   Le bouton « Tester » se connecte à Gmail sans envoyer d'email.
4. Recréez les conteneurs pour qu'ils relisent le `.env` :

   ```bash
   docker compose up -d
   ```

5. Vérifiez :

   ```bash
   docker compose exec web python manage.py diagnostic --email vous@exemple.fr
   ```

   La ligne « Email de test » dit **par quel service** l'email est parti, ou la cause exacte de l'échec.

Options :
- `EMAIL_PROVIDER=smtp` : Gmail directement, SendGrid ignoré.
- `EMAIL_HOST`, `EMAIL_PORT` (587), `EMAIL_USE_TLS` (True) : pour un autre fournisseur SMTP.
- Gmail limite l'envoi à environ 500 emails par jour : suffisant pour le lancement.

### Pourquoi un email envoyé par SendGrid avec une adresse @gmail.com se perd

Gmail publie une règle (DMARC) qui dit : « un email signé @gmail.com doit venir des serveurs de
Google ». Envoyé par SendGrid, il échoue à ce contrôle et finit souvent dans les **indésirables**,
ou n'est pas délivré. Solutions : le secours Gmail ci-dessus, ou, à terme, un domaine à vous
(par exemple `noreply@nitypulse.com`) authentifié dans SendGrid (Settings › Sender Authentication ›
Domain Authentication).

### Lire les causes affichées par `diagnostic`

| Message | Cause | Solution |
|---|---|---|
| clé SendGrid invalide | clé fausse ou sans le droit « Mail Send » | recréer une clé « Full Access » ou « Mail Send » |
| expéditeur non vérifié | `DEFAULT_FROM_EMAIL` non vérifié dans SendGrid | Settings › Sender Authentication › Single Sender |
| Gmail refuse | mot de passe normal au lieu d'un mot de passe d'application | étapes 1 et 2 ci-dessus |
| service injoignable | réseau, DNS, ou port 587 bloqué par l'hébergeur | demander à l'hébergeur d'ouvrir le port 587 sortant |
