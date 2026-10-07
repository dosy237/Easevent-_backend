"""
events/wording.py — « Invitation » ou « Billet » selon l'événement
════════════════════════════════════════════════════════════════
On ne parle pas de « ticket » pour un mariage : un événement privé ou une
célébration (mariage, anniversaire, soirée, gala) donne une INVITATION ; un
événement public ouvert (concert, festival, conférence…) donne un BILLET.
Les formes sont prêtes à l'emploi, accords compris (« générée » / « généré »).
L'application reçoit le même dictionnaire (champ « pass_word »).
════════════════════════════════════════════════════════════════
"""
CELEBRATIONS = {'mariage', 'anniversaire', 'soiree', 'gala'}

INVITATION = {
    'kind': 'invitation', 'one': 'invitation', 'One': 'Invitation', 'many': 'invitations', 'Many': 'Invitations',
    'a': 'une invitation', 'the': "l'invitation", 'of': "de l'invitation", 'my': 'mon invitation', 'your': 'votre invitation',
    'e': 'e',                               # accord : « générée », « annulée », « prête »
}
BILLET = {
    'kind': 'billet', 'one': 'billet', 'One': 'Billet', 'many': 'billets', 'Many': 'Billets',
    'a': 'un billet', 'the': 'le billet', 'of': 'du billet', 'my': 'mon billet', 'your': 'votre billet',
    'e': '',
}


def pass_word(event):
    if event is None:
        return BILLET
    if event.visibility == 'private' or event.event_type in CELEBRATIONS:
        return INVITATION
    return BILLET


def de(name):
    """« de Paul », « d’Aïcha » : élision devant une voyelle ou un h."""
    name = (name or '').strip()
    return f'd’{name}' if name[:1].lower() in 'aeiouyhàâäéèêëîïôöùûüœæ' else f'de {name}'
