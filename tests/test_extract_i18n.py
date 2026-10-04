"""Multilingual section-heading detection (DE/FR/ES/NL/IT).

Non-English postings use h-tags structurally, but the old heading
regexes only matched English keywords, so requirements, duties and
benefits sections were never bucketed. These tests exercise the
multilingual heading taxonomy and the language-neutral "other" fallback.
"""

from jobscraper.extract import (
    extract_benefits,
    extract_requirements,
    parse_description_html,
    split_sections,
)


def _buckets(html: str):
    soup = parse_description_html(html)
    sections = split_sections(soup)
    req, nice, resp, other = extract_requirements(sections)
    benefits = extract_benefits(sections)
    return (
        [s.heading for s in req],
        [s.heading for s in nice],
        [s.heading for s in resp],
        [s.heading for s in other],
        [s.heading for s in benefits],
    )


GERMAN = """
<html><body>
<h1>Sicherheitsingenieur (m/w/d)</h1>
<h2>Deine Aufgaben</h2>
<p>Du betreibst die SIEM-Plattform und jagst Bedrohungen.</p>
<h2>Dein Profil</h2>
<p>Du hast Erfahrung mit Splunk und Python.</p>
<h2>Von Vorteil</h2>
<p>Kenntnisse in Kubernetes sind w&uuml;nschenswert.</p>
<h2>Wir bieten</h2>
<p>30 Tage Urlaub, flexible Arbeitszeiten, Jobrad.</p>
</body></html>
"""

FRENCH = """
<html><body>
<h1>Ing&eacute;nieur S&eacute;curit&eacute;</h1>
<h2>Vos missions</h2>
<p>Vous exploitez la plateforme SIEM.</p>
<h2>Profil recherch&eacute;</h2>
<p>Exp&eacute;rience avec Splunk et Python requise.</p>
<h2>Ce que nous offrons</h2>
<p>Mutuelle, 25 jours de cong&eacute;s, t&eacute;l&eacute;travail.</p>
</body></html>
"""

SPANISH = """
<html><body>
<h1>Ingeniero de seguridad</h1>
<h2>Funciones y responsabilidades</h2>
<p>Operar la plataforma SIEM.</p>
<h2>Requisitos</h2>
<p>Experiencia con Splunk y Python.</p>
<h2>Beneficios</h2>
<p>Seguro m&eacute;dico, horario flexible.</p>
</body></html>
"""

DUTCH = """
<html><body>
<h1>Beveiligingsingenieur</h1>
<h2>Jouw taken</h2>
<p>Je beheert het SIEM-platform.</p>
<h2>Jouw profiel</h2>
<p>Ervaring met Splunk en Python.</p>
<h2>Wat wij bieden</h2>
<p>27 vakantiedagen, thuiswerkmogelijkheid.</p>
</body></html>
"""

ITALIAN = """
<html><body>
<h1>Ingegnere della sicurezza</h1>
<h2>Le tue attivit&agrave;</h2>
<p>Gestisci la piattaforma SIEM.</p>
<h2>Requisiti</h2>
<p>Esperienza con Splunk e Python.</p>
<h2>Cosa offriamo</h2>
<p>Buoni pasto, smart working.</p>
</body></html>
"""


def test_german_headings():
    req, nice, resp, other, benefits = _buckets(GERMAN)
    assert req == ["Dein Profil"], req
    assert nice == ["Von Vorteil"], nice
    assert resp == ["Deine Aufgaben"], resp
    assert benefits == ["Wir bieten"], benefits


def test_french_headings():
    req, nice, resp, other, benefits = _buckets(FRENCH)
    assert resp == ["Vos missions"], resp
    assert len(req) == 1 and "Profil" in req[0], req
    assert benefits == ["Ce que nous offrons"], benefits


def test_spanish_headings():
    req, nice, resp, other, benefits = _buckets(SPANISH)
    assert req == ["Requisitos"], req
    assert resp == ["Funciones y responsabilidades"], resp
    assert benefits == ["Beneficios"], benefits


def test_dutch_headings():
    req, nice, resp, other, benefits = _buckets(DUTCH)
    assert req == ["Jouw profiel"], req
    assert resp == ["Jouw taken"], resp
    assert benefits == ["Wat wij bieden"], benefits


def test_italian_headings():
    req, nice, resp, other, benefits = _buckets(ITALIAN)
    assert req == ["Requisiti"], req
    assert resp and "attivit" in resp[0], resp
    assert benefits == ["Cosa offriamo"], benefits


def test_company_profile_not_misbucketed_as_requirements():
    """'Unternehmensprofil' (company profile) must not count as a
    requirements heading even though it contains 'Profil'."""
    html = """
    <html><body>
    <h1>Job</h1>
    <h2>Unternehmensprofil</h2>
    <p>Wir sind ein f&uuml;hrendes Unternehmen in Berlin.</p>
    <h2>Anforderungen</h2>
    <p>Erfahrung mit Splunk.</p>
    </body></html>
    """
    req, nice, resp, other, benefits = _buckets(html)
    assert all("Unternehmensprofil" not in h for h in req), req
    assert req == ["Anforderungen"], req


def test_other_fallback_multilingual():
    """A long unheaded German paragraph with experience hints lands in
    'other' even without a matching heading."""
    body = ("Sie bringen mehrj&auml;hrige Berufserfahrung im Bereich "
            "SIEM mit. Fundierte Kenntnisse in Splunk und ein "
            "abgeschlossenes Studium runden Ihr Profil ab. ") * 12
    html = f"<html><body><p>{body}</p></body></html>"
    req, nice, resp, other, benefits = _buckets(html)
    assert len(other) == 1, (req, nice, resp, other)
    assert not req and not resp


def test_english_still_works():
    html = """
    <html><body>
    <h1>Security Engineer</h1>
    <h2>Requirements</h2>
    <p>3+ years of experience with Splunk.</p>
    <h2>What we offer</h2>
    <p>Health insurance, PTO.</p>
    </body></html>
    """
    req, nice, resp, other, benefits = _buckets(html)
    assert req == ["Requirements"], req
    assert benefits == ["What we offer"], benefits
