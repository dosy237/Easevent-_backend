"""
tickets/pdf.py
═══════════════════════════════════════════════════════════════
Ticket au format PDF (bouton « Télécharger » de M27).

Une page A6 portrait, aux couleurs d'Easevent : photo de l'événement,
titre, date, lieu, participant, prix, dress code, QR code signé et
numéro du ticket. Le QR encode le même identifiant signé que dans
l'application (aucune donnée personnelle).
═══════════════════════════════════════════════════════════════
"""
import io
import logging
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A6
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)

GREEN, ORANGE, TEXT, MUTED, BORDER, BG = (HexColor(c) for c in
                                          ('#1B6B4A', '#E76F51', '#1A1A1A', '#757575', '#E8E8E8', '#F7F7F7'))
MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août',
        'septembre', 'octobre', 'novembre', 'décembre']
JOURS = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche']


def _date(dt):
    from django.utils import timezone
    dt = timezone.localtime(dt)
    return f"{JOURS[dt.weekday()].capitalize()} {dt.day} {MOIS[dt.month - 1]} {dt.year} · {dt:%H}h{dt:%M}"


def _price(ticket):
    if ticket.price <= 0:
        return '0,00 € · Gratuit'
    symbol = '€' if ticket.currency == 'EUR' else ticket.currency
    return f"{ticket.price:.2f}".replace('.', ',') + f" {symbol} · Payé"


def _cover_image(cover):
    """Image de couverture : fichier statique/média local, sinon URL (délai court)."""
    if not cover:
        return None
    try:
        if cover.startswith('/static/'):
            path = finders.find(cover[len('/static/'):])
            return ImageReader(path) if path else None
        if cover.startswith(('http://', 'https://')):
            import requests
            resp = requests.get(cover, timeout=4)
            resp.raise_for_status()
            return ImageReader(io.BytesIO(resp.content))
        path = Path(settings.MEDIA_ROOT) / cover.replace(settings.MEDIA_URL.lstrip('/'), '', 1).lstrip('/')
        return ImageReader(str(path)) if path.exists() else None
    except Exception:
        logger.warning('Couverture indisponible pour le PDF : %s', cover)
        return None


def _draw_cover(c, img, x, y, w, h):
    """Dessine l'image en mode « cover » (recadrée, sans déformation)."""
    iw, ih = img.getSize()
    scale = max(w / iw, h / ih)
    dw, dh = iw * scale, ih * scale
    c.saveState()
    path = c.beginPath()
    path.rect(x, y, w, h)
    c.clipPath(path, stroke=0, fill=0)
    c.drawImage(img, x + (w - dw) / 2, y + (h - dh) / 2, dw, dh)
    c.restoreState()


def render_ticket_pdf(ticket):
    event = ticket.event
    buf = io.BytesIO()
    W, H = A6
    c = canvas.Canvas(buf, pagesize=A6)
    c.setTitle(f"Ticket {ticket.number} — {event.title}")
    c.setAuthor('Easevent')
    c.setSubject('Ticket Easevent')

    margin = 14
    c.setFillColor(BG)
    c.rect(0, 0, W, H, stroke=0, fill=1)

    # ── En-tête : photo + titre ─────────────────────────────
    head_h = 112
    card_x, card_w = margin, W - 2 * margin
    head_y = H - margin - head_h
    c.setFillColor(GREEN)
    c.roundRect(card_x, head_y, card_w, head_h, 10, stroke=0, fill=1)
    img = _cover_image(event.cover_image)
    if img:
        _draw_cover(c, img, card_x, head_y, card_w, head_h)
    c.setFillColor(HexColor('#000000'))
    c.setFillAlpha(0.5)
    c.rect(card_x, head_y, card_w, 54, stroke=0, fill=1)
    c.setFillAlpha(1)

    # Badges GÉNÉRÉ / PAYÉ|GRATUIT
    c.setFillColor(white)
    c.roundRect(card_x + 10, head_y + 38, 44, 11, 3, stroke=0, fill=1)
    c.setFillColor(GREEN)
    c.setFont('Helvetica-Bold', 6.5)
    c.drawCentredString(card_x + 32, head_y + 41.5, 'GÉNÉRÉ')
    c.setFillColor(white)
    c.setFont('Helvetica-Bold', 6.5)
    c.drawString(card_x + 60, head_y + 41.5, 'GRATUIT' if ticket.price <= 0 else 'PAYÉ')

    c.setFont('Helvetica-Bold', 12.5)
    title_lines = simpleSplit(event.title, 'Helvetica-Bold', 12.5, card_w - 20)[:2]
    for i, line in enumerate(title_lines):
        c.drawString(card_x + 10, head_y + 24 - i * 14 + (7 if len(title_lines) > 1 else 0), line)

    # ── Corps : lignes d'information ────────────────────────
    body_top = head_y - 6
    c.setFillColor(white)
    c.roundRect(card_x, margin, card_w, body_top - margin, 10, stroke=0, fill=1)

    rows = [
        ('Date', _date(event.start_date)),
        ('Lieu', 'En ligne' if event.is_online else (event.location_address or '—')),
        ('Participant', ticket.user.full_name),
        ('Prix', _price(ticket)),
    ]
    if ticket.dress_code:
        rows.append(('Dress code', ticket.dress_code))

    y = body_top - 16
    for label, value in rows:
        c.setFont('Helvetica', 7)
        c.setFillColor(MUTED)
        c.drawString(card_x + 12, y, label)
        c.setFont('Helvetica-Bold', 7.5)
        c.setFillColor(ORANGE if label == 'Dress code' else TEXT)
        lines = simpleSplit(value, 'Helvetica-Bold', 7.5, card_w - 90)[:2]
        for i, line in enumerate(lines):
            c.drawRightString(card_x + card_w - 12, y - i * 9, line)
        y -= 9 * max(1, len(lines)) + 5
        c.setStrokeColor(BORDER)
        c.setLineWidth(0.5)
        c.line(card_x + 12, y + 2, card_x + card_w - 12, y + 2)
        y -= 6

    # ── QR code ─────────────────────────────────────────────
    qr_size = 100
    widget = QrCodeWidget(ticket.qr_payload, barLevel='M')
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(qr_size, qr_size, transform=[qr_size / (x2 - x1), 0, 0, qr_size / (y2 - y1), 0, 0])
    drawing.add(widget)
    qr_y = margin + 30
    renderPDF.draw(drawing, c, (W - qr_size) / 2, qr_y)

    c.setFont('Helvetica', 6.5)
    c.setFillColor(MUTED)
    c.drawCentredString(W / 2, qr_y + qr_size + 4, "Présentez ce QR code à l'entrée")
    c.setFont('Helvetica-Bold', 8)
    c.setFillColor(TEXT)
    c.drawCentredString(W / 2, qr_y - 10, f"TICKET N° {ticket.number}")
    c.setFont('Helvetica', 5.8)
    c.setFillColor(MUTED)
    c.drawCentredString(W / 2, margin + 8,
                        f"Ticket personnel et non transférable — valable pour {ticket.user.full_name}. Easevent")

    c.showPage()
    c.save()
    return buf.getvalue()
