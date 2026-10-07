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
