"""
minisite/colors.py — palettes harmonieuses et toujours lisibles
════════════════════════════════════════════════════════════════
À partir des couleurs de l'organisateur (ou de l'ambiance), on calcule les
couleurs du mini-site selon une harmonie (monochrome, complémentaire…).
Le contraste est vérifié selon WCAG 2.2 et corrigé si besoin :
  texte / fond ≥ 7, texte secondaire / fond ≥ 4.5,
  texte des boutons / bouton ≥ 4.5, bouton / fond ≥ 3.
L'IA ne choisit qu'une harmonie : elle ne peut pas produire un site illisible.
════════════════════════════════════════════════════════════════
"""
import colorsys
import re

HEX = re.compile(r'^#?([0-9a-fA-F]{6})$')

# Couleur de départ selon l'ambiance quand l'organisateur n'en a pas choisi
AMBIANCE_BASE = {
    'elegant': '#8C6D46', 'festif': '#D9480F', 'minimaliste': '#2F3A3A',
    'colore': '#7B2FF7', 'professionnel': '#1D4E89', 'autre': '#1B6B4A', '': '#1B6B4A',
}
TOKENS = ('bg', 'surface', 'text', 'muted', 'primary', 'onPrimary', 'accent', 'onAccent', 'line',
          'inverseBg', 'inverseText', 'inverseMuted', 'tint')


def parse(value, fallback='#1B6B4A'):
    m = HEX.match(str(value or '').strip())
    return f'#{m.group(1).upper()}' if m else fallback


def _rgb(hex_):
    h = hex_.lstrip('#')
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _hex(rgb):
    return '#' + ''.join(f'{round(max(0, min(1, c)) * 255):02X}' for c in rgb)


def hsl(hex_):
    r, g, b = _rgb(hex_)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, s, l


def from_hsl(h, s, l):
    return _hex(colorsys.hls_to_rgb((h % 360) / 360, max(0, min(1, l)), max(0, min(1, s))))


def luminance(hex_):
    def ch(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in _rgb(hex_))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ensure(fg, bg, ratio):
    """Assombrit ou éclaircit fg (même teinte) jusqu'au contraste demandé."""
    if contrast(fg, bg) >= ratio:
        return fg
    h, s, l = hsl(fg)
    darker = luminance(bg) > 0.18
    for _ in range(60):
        l = l - 0.02 if darker else l + 0.02
        fg = from_hsl(h, s, l)
        if contrast(fg, bg) >= ratio or l <= 0 or l >= 1:
            break
    if contrast(fg, bg) < ratio:
        fg = '#111111' if darker else '#FFFFFF'
    return fg


def on(color):
    """Texte posé sur une couleur : quasi noir ou quasi blanc, le plus lisible."""
    dark, light = '#141414', '#FFFFFF'
    return dark if contrast(dark, color) >= contrast(light, color) else light


def button(color, bg):
    """Couleur de bouton dont le texte (blanc sur fond clair, foncé sur fond sombre) atteint 4.5."""
    light_bg = luminance(bg) > 0.18
    label = '#FFFFFF' if light_bg else '#141414'
    if contrast(on(color), color) >= 4.5:
        return color, on(color)
    h, s, l = hsl(color)
    for _ in range(60):
        l = l - 0.02 if light_bg else l + 0.02
        color = from_hsl(h, s, l)
        if contrast(label, color) >= 4.5:
            return color, label
    return ('#141414', '#FFFFFF') if light_bg else ('#F2F2F2', '#141414')


def _accent_hue(h, harmony, secondary):
    if secondary:
        return hsl(secondary)[0]
    return {'complementary': h + 180, 'triadic': h + 120, 'analogous': h + 32, 'vivid': h + 150,
            'pastel': h + 40, 'gold': 42, 'dark': h + 25, 'neutral': h, 'monochrome': h}.get(harmony, h + 30)


def palette(primary, secondary=None, harmony='monochrome'):
    primary = parse(primary)
    secondary = parse(secondary, None) if secondary else None
    h, s, l = hsl(primary)
    s = max(s, 0.18)
    ah = _accent_hue(h, harmony, secondary if harmony not in ('gold',) else None)

    if harmony in ('dark', 'gold'):
        bg = from_hsl(h, min(s, 0.35), 0.09)
        surface = from_hsl(h, min(s, 0.3), 0.14)
        text = from_hsl(h, 0.15, 0.95)
        muted = from_hsl(h, 0.12, 0.72)
        prim = from_hsl(h, max(s, 0.45), max(l, 0.62))
        acc = from_hsl(ah, 0.62 if harmony == 'gold' else max(s, 0.5), 0.62)
        line = from_hsl(h, 0.2, 0.24)
        tint = from_hsl(h, min(s, 0.35), 0.17)
        inv_bg = from_hsl(h, 0.2, 0.95)
    elif harmony == 'pastel':
        bg = from_hsl(h, 0.6, 0.96)
        surface = '#FFFFFF'
        text = from_hsl(h, 0.35, 0.16)
        muted = from_hsl(h, 0.15, 0.38)
        prim = from_hsl(h, max(s, 0.45), 0.5)
        acc = from_hsl(ah, 0.55, 0.62)
        line = from_hsl(h, 0.35, 0.86)
        tint = from_hsl(h, 0.55, 0.93)
        inv_bg = from_hsl(h, 0.35, 0.18)
    elif harmony == 'neutral':
        bg = '#FAFAF8'
        surface = '#FFFFFF'
        text = '#161616'
        muted = '#5A5A5A'
        prim = primary
        acc = from_hsl(h, s * 0.6, 0.45)
        line = '#E6E4E0'
        tint = '#F1F0EC'
        inv_bg = '#161616'
    elif harmony == 'vivid':
        bg = from_hsl(h, 0.45, 0.97)
        surface = '#FFFFFF'
        text = from_hsl(h, 0.4, 0.12)
        muted = from_hsl(h, 0.2, 0.35)
        prim = from_hsl(h, max(s, 0.7), min(max(l, 0.42), 0.55))
        acc = from_hsl(ah, 0.85, 0.55)
        line = from_hsl(h, 0.3, 0.88)
        tint = from_hsl(h, 0.65, 0.94)
        inv_bg = from_hsl(h, 0.55, 0.16)
    else:  # monochrome, analogous, complementary, triadic
        bg = from_hsl(h, min(s, 0.4), 0.97)
        surface = '#FFFFFF'
        text = from_hsl(h, min(s, 0.45), 0.13)
        muted = from_hsl(h, min(s, 0.2), 0.38)
        prim = primary
        acc = from_hsl(ah, max(s, 0.45), 0.5) if harmony != 'monochrome' else from_hsl(h, s, min(l + 0.18, 0.7))
        line = from_hsl(h, min(s, 0.3), 0.88)
        tint = from_hsl(h, min(s, 0.45), 0.93)
        inv_bg = from_hsl(h, min(s, 0.5), 0.14)

    text = ensure(ensure(ensure(text, bg, 7), tint, 4.5), surface, 7)
    muted = ensure(ensure(ensure(muted, bg, 4.5), tint, 4.5), surface, 4.5)
    prim = ensure(prim, bg, 3)
    acc = ensure(acc, bg, 3)
    inv_text = ensure('#FFFFFF' if luminance(inv_bg) < 0.4 else '#141414', inv_bg, 7)
    prim, on_prim = button(prim, bg)
    acc, on_acc = button(acc, bg)
    return {
        'bg': bg, 'surface': surface, 'text': text, 'muted': muted,
        'primary': prim, 'onPrimary': on_prim, 'accent': acc, 'onAccent': on_acc,
        'line': line, 'tint': tint,
        'inverseBg': inv_bg, 'inverseText': inv_text, 'inverseMuted': ensure(muted if luminance(inv_bg) > 0.4 else '#C9C9C9', inv_bg, 4.5),
    }


def check(colors):
    """Vrai si la palette respecte les contrastes minimaux (utilisé aussi pour les retouches)."""
    try:
        return (contrast(colors['text'], colors['bg']) >= 4.5 and contrast(colors['muted'], colors['bg']) >= 4.5
                and contrast(colors['onPrimary'], colors['primary']) >= 4.5
                and contrast(colors['onAccent'], colors['accent']) >= 4.5
                and contrast(colors['primary'], colors['bg']) >= 3
                and contrast(colors['inverseText'], colors['inverseBg']) >= 4.5
                and contrast(colors['text'], colors['surface']) >= 4.5
                and contrast(colors['text'], colors['tint']) >= 4.5
                and contrast(colors['muted'], colors['tint']) >= 4.5
                and contrast(colors['muted'], colors['surface']) >= 4.5)
    except (KeyError, ValueError, TypeError):
        return False
