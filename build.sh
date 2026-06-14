#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# backend/build.sh — Script de build Easevent (backend Django)
# Exécuté par Render à chaque déploiement.
# ═══════════════════════════════════════════════════════════════════════
set -euo pipefail

log() { echo ""; echo "━━━  $*  ━━━"; }

# ── 1. Python deps ──────────────────────────────────────────────────────
log "Installing Python dependencies"
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet

# ── 2. Static files ─────────────────────────────────────────────────────
log "Collecting static files (WhiteNoise)"
python manage.py collectstatic --no-input

# ── 3. Migrations ───────────────────────────────────────────────────────
log "Running database migrations"
python manage.py migrate --no-input

# ── 4. Sanity check ─────────────────────────────────────────────────────
log "Django system check"
python manage.py check --deploy

log "Build complete ✓"
