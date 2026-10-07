"""
minisite/composer.py — assemble les propositions à partir de la bibliothèque
════════════════════════════════════════════════════════════════
compose_batch(facts, art, copies, seed, taken) → 6 plans (spec), un par direction.
  - structure selon le type d'événement, sections disponibles selon les données ;
  - ordre légèrement permuté, sections facultatives tirées au sort ;
  - variantes, fonds, alignements, polices, formes, ornements, harmonie ;
  - choix de l'IA (art) appliqués s'ils sont valides, sinon tirage guidé ;
  - empreinte de disposition unique : différente des 5 autres propositions ET
    de toutes les empreintes déjà générées (taken) — sinon on fait muter.
════════════════════════════════════════════════════════════════
"""
import hashlib
import json
import random

from . import catalog, colors
from . import copy as copywriting

PERMUTE = {'editorial': 0.25, 'immersive': 0.4, 'minimal': 0.2, 'festive': 0.45, 'luxe': 0.25, 'playful': 0.5}
EXTRA_RATE = {'editorial': 0.5, 'immersive': 0.4, 'minimal': 0.2, 'festive': 0.75, 'luxe': 0.55, 'playful': 0.7}


def available(kind, f):
    return {
        'countdown': f['upcoming'],
        'calendar': f['upcoming'],
        'location': bool(f['address']),
        'online': f['is_online'],
        'gallery': f['images'] >= 2,
        'dresscode': bool(f['dress_code']),
        'capacity': bool(f['max_guests']),
    }.get(kind, True)


def _valid_order(seq):
    pos = {k: i for i, k in enumerate(seq)}
    if 'faq' in pos and 'details' in pos and pos['faq'] < pos['details']:
        return False
    if 'calendar' in pos and pos['calendar'] == 0:
        return False
    for a, b in zip(seq, seq[1:]):
        if a == b or {a, b} == {'divider', 'divider'}:
            return False
    return True


def _order(f, direction, rng):
    structure = catalog.STRUCTURES.get(f['type'], catalog.STRUCTURES['autre'])
    seq = [k for k in structure['core'] if available(k, f)]
    for i in range(len(seq) - 1):
        if rng.random() < PERMUTE[direction]:
            trial = seq[:]
            trial[i], trial[i + 1] = trial[i + 1], trial[i]
            if _valid_order(trial):
                seq = trial
    extras = [k for k in structure['extras'] if available(k, f) and k not in seq]
    if direction == 'minimal':
        extras = [k for k in extras if k not in ('divider', 'capacity')]
    for k in extras:
        if rng.random() < EXTRA_RATE[direction]:
            for _ in range(6):
                trial = seq[:]
                trial.insert(rng.randrange(1, len(seq) + 1) if seq else 0, k)
                if _valid_order(trial):
                    seq = trial
                    break
    if direction == 'immersive' and 'gallery' in seq and seq.index('gallery') > 1:
        seq.remove('gallery')
        seq.insert(rng.choice((0, 1)), 'gallery')
    if 'cta' not in seq:
        seq.append('cta')
    return [catalog.FIXED_FIRST] + seq + [catalog.FIXED_LAST]


def _choose(rng, options, avoid=()):
    fresh = [o for o in options if o not in avoid]
    return rng.choice(fresh or list(options))


def _sections(order, direction, rng, art, used_heroes):
    d = catalog.DIRECTIONS[direction]
    out, prev_tone = [], 'plain'
    for kind in order:
        variants = catalog.SECTIONS[kind]['variants']
        if kind == 'hero':
            wanted = art.get('hero') if art.get('hero') in variants else None
            if wanted and wanted not in used_heroes:
                variant = wanted
            elif any(h not in used_heroes for h in d['hero']):
                variant = _choose(rng, d['hero'], used_heroes)
            else:                                        # accueils de la direction déjà pris : un autre, inédit
                variant = _choose(rng, variants, used_heroes)
        else:
            variant = rng.choice(variants)
        if kind in ('hero', 'footer'):
            tone = 'inverse' if kind == 'footer' and rng.random() < 0.5 else 'plain'
        else:
            tone = rng.choice(d['tones'])
            if tone == 'inverse' and prev_tone == 'inverse':
                tone = 'tinted'
        align = 'left' if direction in ('editorial', 'minimal') and rng.random() < 0.6 else rng.choice(catalog.ALIGNS)
        out.append({'kind': kind, 'variant': variant, 'tone': tone, 'align': align})
        prev_tone = tone
    return out


def _theme(f, direction, rng, art, used_fonts):
    d = catalog.DIRECTIONS[direction]

    def pick(key, choices, allowed):
        value = art.get(key)
        return value if value in allowed else rng.choice(choices)

    fonts = art.get('font_pair') if art.get('font_pair') in catalog.FONT_PAIRS and art.get('font_pair') not in used_fonts \
        else _choose(rng, d['fonts'], used_fonts)
    harmony = pick('harmony', d['harmony'], catalog.HARMONIES)
    base = f['primary'] or colors.AMBIANCE_BASE.get(f['ambiance'], colors.AMBIANCE_BASE[''])
    alternatives = [h for h in catalog.HARMONIES if h != harmony]
    rng.shuffle(alternatives)
    return {
        'fonts': fonts,
        'radius': pick('radius', d['radius'], catalog.RADII),
        'density': pick('density', d['density'], catalog.DENSITIES),
        'ornament': pick('ornament', d['ornament'], catalog.ORNAMENTS),
        'motion': rng.choice(d['motion']),
        'harmony': harmony,
        'colors': colors.palette(base, f['secondary'] or None, harmony),
        # Trois autres harmonies proposées dans l'éditeur (toutes lisibles)
        'alternatives': {h: colors.palette(base, f['secondary'] or None, h) for h in alternatives[:3]},
    }


def fingerprint(spec):
    t = spec['theme']
    shape = {
        'sections': [(s['kind'], s['variant'], s['tone'], s['align']) for s in spec['sections']],
        'theme': (t['fonts'], t['radius'], t['density'], t['ornament'], t['harmony']),
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()


def _mutate(spec, rng):
    """Change une variante, un fond ou un alignement (hors accueil) pour obtenir une disposition inédite."""
    candidates = [s for s in spec['sections'] if s['kind'] != 'hero'] or spec['sections']
    s = rng.choice(candidates)
    what = rng.random()
    if what < 0.5:
        s['variant'] = rng.choice(catalog.SECTIONS[s['kind']]['variants'])
    elif what < 0.8 and s['kind'] not in ('hero', 'footer'):
        s['tone'] = rng.choice(catalog.TONES)
    else:
        s['align'] = 'center' if s['align'] == 'left' else 'left'


def _copy_for(f, direction, rng, ai_copy, order):
    base = copywriting.fallback(f, direction, rng)
    for kind, fields in (ai_copy or {}).items():
        if kind.startswith('_'):
            continue
        base.setdefault(kind, {}).update(fields)
    if not f['dress_code']:
        base['dresscode']['note'] = ''
    out = {k: v for k, v in base.items() if k in order}
    label = out.get('cta', {}).get('label') or copywriting.cta_label(f)
    if 'faq' in out:
        out['faq'] = {**out['faq'], 'items': copywriting.faq(f, label)}
    return out


def compose(f, direction, rng, art=None, ai_copy=None, used_heroes=(), used_fonts=()):
    art = art or {}
    order = _order(f, direction, rng)
    spec = {
        'v': catalog.SPEC_VERSION,
        'direction': direction,
        'label': catalog.DIRECTIONS[direction]['label'],
        'mood': art.get('mood') or '',
        # Concept de la proposition (rédaction, sinon direction artistique) : conservé pour l'apprentissage
        'concept': (ai_copy or {}).get('_concept') or art.get('concept') or '',
        'theme': _theme(f, direction, rng, art, used_fonts),
        'sections': _sections(order, direction, rng, art, used_heroes),
    }
    spec['copy'] = _copy_for(f, direction, rng, ai_copy, order)
    return spec


def compose_batch(f, seed, art=None, copies=None, taken=frozenset()):
    """6 propositions aux dispositions toutes différentes (et jamais vues auparavant)."""
    rng = random.Random(seed)
    art, copies = art or {}, copies or {}
    batch, prints, heroes, fonts = [], set(), set(), set()
    for direction in catalog.DIRECTION_ORDER:
        spec = compose(f, direction, rng, art.get(direction), copies.get(direction), heroes, fonts)
        fp = fingerprint(spec)
        tries = 0
        while (fp in prints or fp in taken) and tries < 200:
            _mutate(spec, rng)
            fp = fingerprint(spec)
            tries += 1
        spec['fingerprint'] = fp
        prints.add(fp)
        heroes.add(spec['sections'][0]['variant'])
        fonts.add(spec['theme']['fonts'])
        batch.append(spec)
    return batch


# ── Corrections du directeur de création (validées une à une) ─────────────────
def apply_critique(spec, review, f, used_fonts=()):
    """Applique les actions valides ; ignore le reste. Renvoie la liste des actions appliquées."""
    from .copy import clean_text
    applied = []
    sections = spec['sections']
    by_kind = {s['kind']: s for s in sections}
    theme = spec['theme']
    for a in review.get('actions', []):
        t, kind, value = a.get('type'), a.get('kind'), a.get('value')
        ok = False
        if t == 'variant' and kind in by_kind and value in catalog.SECTIONS[kind]['variants']:
            by_kind[kind]['variant'] = value
            ok = True
        elif t == 'tone' and kind in by_kind and kind not in ('hero',) and value in catalog.TONES:
            by_kind[kind]['tone'] = value
            ok = True
        elif t == 'align' and kind in by_kind and value in catalog.ALIGNS:
            by_kind[kind]['align'] = value
            ok = True
        elif t == 'move' and kind in by_kind and a.get('before') in by_kind \
                and kind not in (catalog.FIXED_FIRST, catalog.FIXED_LAST) and a.get('before') != catalog.FIXED_FIRST:
            order = [s for s in sections if s['kind'] != kind]
            idx = next(i for i, s in enumerate(order) if s['kind'] == a['before'])
            order.insert(idx, by_kind[kind])
            if _valid_order([s['kind'] for s in order]):
                sections[:] = order
                ok = True
        elif t == 'harmony' and value in catalog.HARMONIES and value != theme['harmony']:
            base = f['primary'] or colors.AMBIANCE_BASE.get(f['ambiance'], colors.AMBIANCE_BASE[''])
            theme['alternatives'].pop(value, None)
            theme['alternatives'][theme['harmony']] = theme['colors']
            theme['colors'] = colors.palette(base, f['secondary'] or None, value)
            theme['harmony'] = value
            ok = True
        elif t == 'fonts' and value in catalog.FONT_PAIRS and value not in used_fonts:   # garder 6 typographies distinctes
            theme['fonts'] = value
            ok = True
        elif t in ('ornament', 'density', 'radius'):
            allowed = {'ornament': catalog.ORNAMENTS, 'density': catalog.DENSITIES, 'radius': catalog.RADII}[t]
            if value in allowed:
                theme[t] = value
                ok = True
        elif t == 'copy' and kind in spec['copy'] and kind != 'faq':
            limit = catalog.SECTIONS.get(kind, {}).get('copy', {}).get(a.get('field'))
            text = clean_text(value, limit, f) if limit else None
            if text:
                spec['copy'][kind][a['field']] = text
                ok = True
        if ok:
            applied.append(a)
    # Nouvelle palette : toujours lisible (contrôle du moteur de couleurs)
    if not colors.check(theme['colors']):
        theme['colors'] = colors.palette(f['primary'] or colors.AMBIANCE_BASE[''], None, 'monochrome')
        theme['harmony'] = 'monochrome'
    return applied


def finalize(batch, rng, taken=frozenset()):
    """Recalcule les empreintes après corrections et garantit encore l'unicité."""
    prints = set()
    for spec in batch:
        fp = fingerprint(spec)
        tries = 0
        while (fp in prints or fp in taken) and tries < 200:
            _mutate(spec, rng)
            fp = fingerprint(spec)
            tries += 1
        spec['fingerprint'] = fp
        prints.add(fp)
    return batch
