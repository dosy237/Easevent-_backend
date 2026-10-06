# Points à reprendre plus tard

Liste tenue à jour au fil du projet : sujets volontairement mis de côté.

## 1. Changement d'hébergement (prévu)

L'hébergement actuel (easevent.nitypulse.com, déploiement Docker via GitHub Actions)
va changer de compte. Au moment de la bascule :

- [ ] Nouveau serveur : variables du `.env` (SECRET_KEY, base de données, Redis, SendGrid,
      Cloudinary, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `PLATFORM_FEE_PERCENT`).
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
