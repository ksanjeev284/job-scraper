"""Content extraction: sections, skills, salary, signals, embedded data.

Turns raw posting HTML into structured pieces: headed sections, detected
skill keywords, salary figures, posting signals (sponsorship, language,
work mode), and the liveness verdict (open vs closed vs blocked).
"""

from __future__ import annotations

import html as html_mod
import json
import re

from bs4 import BeautifulSoup

from jobscraper.models import Section

MIN_CONTENT_CHARS = 300

REQ_HEADINGS = re.compile(
    r"(requirement|qualification|what you.?ll (need|bring|have)|must have|"
    r"need to have|should have|you have|you are|you.?re|your (profile|"
    r"background|skills|experience)|ideal candidate|who you are|about you|"
    r"key skills|technical skills|preferred|nice to have|bonus points|"
    r"desirable|essential|criteria|eligibility|what we.?re looking for|"
    r"we are looking for|"
    # German
    r"anforderung|anforderungsprofil|dein profil|ihr profil|"
    r"was du mitbringst|was sie mitbringen|das bringst du mit|"
    r"das erwartet dich|deine kenntnisse|ihre kenntnisse|wunschprofil|"
    r"gew[üu]nschte (qualifikation|f[äa]higkeiten)|"
    # French
    r"profil recherch[ée]|profil souhait[ée]|votre profil|"
    r"ce que nous recherchons|vos comp[ée]tences|"
    r"qualifications requises|"
    # Spanish
    r"requisitos|perfil del candidato|lo que buscamos|"
    r"habilidades requeridas|tu perfil|"
    # Dutch
    r"jouw profiel|uw profiel|functie.?eisen|wat breng je mee|"
    r"wat verwachten we|"
    # Italian
    r"requisiti|il tuo profilo|cosa cerchiamo)",
    re.I)

NICE_HEADINGS = re.compile(
    r"(nice to have|preferred|bonus|desirable|plus|"
    # German
    r"von vorteil|w[üu]nschenswert|pluspunkte|idealerweise|"
    # French
    r"serait un plus|atouts?|appr[ée]ci[ée]e?s?|"
    # Spanish
    r"se valorar[áa]|deseable|ser[áa] un plus|"
    # Dutch
    r"een pluspunt|wenselijk|"
    # Italian
    r"sar[àa] un plus|preferibile|titolo preferenziale)",
    re.I)

RESP_HEADINGS = re.compile(
    r"(your mission|the role|about the role|responsabilit|what you.?ll do|"
    r"key duties|day to day|what you will own|the opportunity|"
    # German
    r"deine aufgaben|ihre aufgaben|aufgabenbereich|deine mission|"
    r"ihre mission|deine verantwortung|ihre verantwortung|"
    r"t[äa]tigkeitsbereich|was dich erwartet|was sie erwartet|"
    # French
    r"vos missions|tes missions|missions|vos responsabilit[ée]s|"
    r"tes responsabilit[ée]s|ce que vous ferez|votre r[ôo]le|"
    # Spanish
    r"tus funciones|sus funciones|funciones y responsabilidades|"
    r"responsabilidades|tu misi[óo]n|"
    # Dutch
    r"jouw taken|uw taken|je werkzaamheden|verantwoordelijkheden|de functie|"
    # Italian
    r"le tue attivit[àa]|attivit[àa] principali|responsabilit[àa]|"
    r"la posizione)",
    re.I)

BENEFITS_HEADINGS = re.compile(
    r"(benefit|perks|what we offer|why (join|work)|life at|compensation"
    r"|rewards|what.?s in it for you|employee (benefits|perks)|"
    # German
    r"wir bieten|das bieten wir|deine vorteile|ihre vorteile|vorteile|"
    r"gute gr[üu]nde f[üu]r uns|leistungen|"
    # French
    r"ce que nous offrons|avantages|vos avantages|"
    r"pourquoi nous rejoindre|nos avantages|"
    # Spanish
    r"lo que ofrecemos|beneficios|ventajas|por qu[ée] nosotros|"
    r"te ofrecemos|"
    # Dutch
    r"wat wij bieden|voordelen|jouw voordelen|waarom bij ons|"
    # Italian
    r"cosa offriamo|benefici|vantaggi|perch[ée] noi)",
    re.I)

SKILL_VOCAB = [
    "Splunk", "Splunk ES", "Splunk SOAR", "SIEM", "SOC", "SOAR",
    "Python", "Shell", "Bash", "PowerShell", "SQL", "Regex",
    "MITRE ATT&CK", "threat hunting", "threat intelligence",
    "incident response", "detection engineering", "log analysis",
    "Tenable", "Nessus", "Qualys", "CrowdStrike", "SentinelOne",
    "Palo Alto", "Cortex XDR", "Cortex XSIAM", "Wazuh", "Elastic",
    "Microsoft Sentinel", "QRadar", "LogRhythm", "Sumo Logic",
    "AWS", "Azure", "GCP", "Kubernetes", "Docker", "Terraform",
    "Linux", "Windows", "Active Directory", "IAM", "Okta",
    "network security", "firewall", "IDS", "IPS", "EDR", "DLP",
    "vulnerability management", "penetration testing", "VAPT",
    "forensics", "malware analysis", "OSINT", "YARA", "Sigma",
    "Kafka", "Elasticsearch", "Git", "CI/CD", "Jira", "ServiceNow",
    "NIST", "ISO 27001", "PCI DSS", "GDPR", "HIPAA",
    "CISSP", "CISM", "CEH", "Security+", "GSEC", "GCIA", "GCIH",
    "Splunk Certified", "SC-200", "AZ-500",
]

# Hints that a long unheaded paragraph is probably a requirements block.
# Multilingual: English, German, French, Spanish, Dutch, Italian.
OTHER_HINTS = re.compile(
    r"(year|experience|skill|certif|degree|bachelor|master|"
    r"jahre?|erfahrung|kenntnisse|f[äa]higkeiten|abschluss|studiengang|"
    r"zertifikat|"
    r"exp[ée]rience|comp[ée]tences|dipl[ôo]me|"
    r"experiencia|conocimientos|t[íi]tulo|"
    r"ervaring|kennis|opleiding|"
    r"esperienza|competenze|laurea)",
    re.I)


EXP_RE = re.compile(
    r"(\d+)\s*(?:\+)?\s*(?:to|-)?\s*(?:\d+\s*)?(?:years?|yrs?)\s+(?:of\s+)?"
    r"(?:experience|exp)", re.I)

SALARY_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s?[\d,]{2,}(?:\.\d+)?\s?(?:lakh|LPA|Lpa)?"
    r"|[\d.]+\s*(?:lakh|LPA)"
    r"|€\s?[\d.,]{3,}\s?k?"
    r"|\$\s?[\d,]{3,}\s?k?"
    r"|£\s?[\d,]{3,}\s?k?", re.I)

# Pay-period markers (JobSpy-style enforce_annual_salary): an optional
# "/hr", "per month", "hourly", "p.a.", ... suffix adjacent to a figure.
_PERIOD_MARKER = (
    r"(?:/\s*(?:hour|hr\b|day|week|wk\b|month|mo\b|year|yr\b)"
    r"|per\s+(?:hour|day|week|month|year|annum)"
    r"|hourly|daily|weekly|monthly|yearly|annually|annual"
    r"|p\.a\.?)"
)
_PERIOD_AFTER = re.compile(r"\s*" + _PERIOD_MARKER, re.I)
_TRAILING_PERIOD = re.compile(r"\s*" + _PERIOD_MARKER + r"\s*$", re.I)

# Short figures (e.g. "$50/hr") only count as salary when a pay-period
# marker is attached; unmarked short figures stay below the threshold.
_SHORT_SALARY_RE = re.compile(
    r"(?:₹|Rs\.?|INR)\s?[\d,]{1,}(?:\.\d+)?\s?(?:lakh|LPA|Lpa)?"
    + _PERIOD_MARKER +
    r"|€\s?[\d.,]{2,}\s?k?" + _PERIOD_MARKER +
    r"|\$\s?[\d,]{2,}\s?k?" + _PERIOD_MARKER +
    r"|£\s?[\d,]{2,}\s?k?" + _PERIOD_MARKER, re.I)

SPONSOR_RE = re.compile(
    r"(visa sponsorship|sponsori\w*|work permit|blue card|relocation"
    r"|authorized to work|right to work)", re.I)
GERMAN_RE = re.compile(
    r"(german|deutsch)[^.]{0,60}(requir|fluent|mandatory|must|necessar|"
    r"business proficient)|deutschkenntnisse", re.I)
REMOTE_RE = re.compile(r"\b(remote|hybrid|on[\s-]?site|work from home|wfh)\b",
                       re.I)

# Page text that means the posting is dead/closed.
DEAD_SIGNALS = re.compile(
    r"(job (posting |vacancy )?((has been|is) )?(closed|removed|expired|"
    r"no longer available|filled)|position (has been |is )?(closed|filled)|"
    r"no longer accepting applications|this job (is no longer|is not) "
    r"available|the job you.?re looking for (is expired|doesn.?t exist)|"
    r"page not found|404.{0,20}(job|posting)|opportunity has expired|"
    r"vacancy (closed|expired))", re.I)

# Phrases that mean we hit a bot wall. Only trusted on short pages: real
# job descriptions mention "captcha" or "security check" legitimately.
BLOCK_SIGNALS = re.compile(
    r"(verify you are human|are you a robot|(?<!gre)captcha|access denied|"
    r"request blocked|unusual traffic|enable javascript to run this app|"
    r"attention required!|incapsula|perimeterx|"
    r"cloudflare.{0,40}ray id)", re.I)


def soup_text(node) -> str:
    """Collapse a BeautifulSoup node to clean single-spaced text."""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def parse_description_html(html_str: str) -> BeautifulSoup:
    """Parse a description blob; some boards HTML-escape it (sometimes twice)."""
    soup = BeautifulSoup(html_mod.unescape(html_str or ""), "lxml")
    body_txt = soup.get_text(" ", strip=True)
    if not soup.find(["p", "li", "ul", "h1", "h2", "h3"]) \
            and re.search(r"<(p|li|ul|div|br|strong)[ >]", body_txt):
        soup = BeautifulSoup(html_mod.unescape(body_txt), "lxml")
    return soup


def split_sections(soup: BeautifulSoup) -> list[Section]:
    """Split a posting into headed sections."""
    sections: list[Section] = []
    heading, chunks = "(intro)", []

    def add_chunk(txt: str) -> None:
        joined = "\n".join(chunks)
        if txt and txt not in joined:  # skip nested-duplicate text
            chunks.append(txt)

    body = soup.body or soup
    for el in body.find_all(["h1", "h2", "h3", "h4", "h5", "strong", "b",
                             "p", "li", "div"]):
        name = el.name
        # container divs repeat their children's text; skip them
        if name == "div" and el.find(["p", "li", "h1", "h2", "h3", "h4",
                                      "h5"]):
            continue
        txt = soup_text(el)
        if not txt:
            continue
        is_heading = (
            name in ("h1", "h2", "h3", "h4", "h5") and len(txt) < 200
        ) or (name in ("strong", "b") and len(txt) < 120
              and el.parent is not None and el.parent.name == "p")
        if is_heading:
            if chunks or heading != "(intro)":
                sections.append(Section(heading, "\n".join(chunks)))
            heading, chunks = txt, []
        elif len(txt) <= 2000:
            add_chunk(txt)
    if chunks or heading != "(intro)":
        sections.append(Section(heading, "\n".join(chunks)))
    # de-dupe identical sections
    dedup, seen = [], set()
    for sec in sections:
        key = (sec.heading, sec.text[:200])
        if key not in seen:
            seen.add(key)
            dedup.append(sec)
    return dedup


def extract_requirements(
        sections: list[Section],
) -> tuple[list[Section], list[Section], list[Section], list[Section]]:
    """Bucket sections into (requirements, nice-to-have, responsibilities,
    other-possibly-relevant)."""
    req, nice, resp, other = [], [], [], []
    for sec in sections:
        if not sec.text.strip():
            continue
        # Nice-to-have is checked first: non-English "nice" headings (e.g.
        # "Von Vorteil") do not necessarily match the requirements regex.
        if NICE_HEADINGS.search(sec.heading):
            nice.append(sec)
        elif REQ_HEADINGS.search(sec.heading):
            req.append(sec)
        elif RESP_HEADINGS.search(sec.heading):
            resp.append(sec)
        elif len(sec.text) > 400 and OTHER_HINTS.search(sec.text):
            other.append(sec)
    return req, nice, resp, other


def extract_benefits(sections: list[Section]) -> list[Section]:
    """Pull out benefits/perks sections (health, PTO, bonus, equity…)."""
    return [sec for sec in sections
            if sec.text.strip() and BENEFITS_HEADINGS.search(sec.heading)]


def find_skills(text: str, extra_skills: tuple[str, ...] = ()) -> list[str]:
    """Detect skill-vocabulary hits in text (word-boundary matched).

    ``extra_skills`` extends the built-in vocabulary, e.g. from a
    candidate profile's ``custom_skills`` list.
    """
    vocab = list(SKILL_VOCAB) + [s for s in extra_skills
                                 if s not in SKILL_VOCAB]
    return [s for s in vocab
            if re.search(r"(?<![A-Za-z])" + re.escape(s) + r"(?![A-Za-z])",
                         text, re.I)]


def find_experience(text: str) -> list[int]:
    """Extract mentioned years-of-experience figures, sorted."""
    return sorted({int(m.group(1)) for m in EXP_RE.finditer(text)})


def extract_salary(text: str) -> list[str]:
    """Extract salary figures (INR/LPA, EUR, USD, GBP).

    An adjacent pay-period marker ("/hr", "per month", ...) is kept on the
    hit so :func:`normalize_salary` can annualize it. Short figures only
    count when a pay-period marker is attached ("$50/hr").
    """
    hits: set[str] = set()
    seen: set[str] = set()  # whitespace-normalized, for cross-regex dedupe

    def _add(hit: str) -> None:
        key = re.sub(r"\s+", "", hit)
        if key not in seen:
            seen.add(key)
            hits.add(hit)

    for m in SALARY_RE.finditer(text):
        hit = m.group(0).strip()
        tail = _PERIOD_AFTER.match(text, m.end())
        if tail:
            raw_tail = tail.group(0).strip()
            sep = "" if raw_tail.startswith("/") else " "
            hit = f"{hit}{sep}{raw_tail}".strip()
        _add(hit)
    for m in _SHORT_SALARY_RE.finditer(text):
        _add(m.group(0).strip())
    return sorted(hits)[:5]


def detect_signals(text: str) -> dict:
    """Detect sponsorship, language and work-mode signals."""
    return {
        "sponsorship_mentioned": bool(SPONSOR_RE.search(text)),
        "german_required": bool(GERMAN_RE.search(text)),
        "work_mode": sorted({m.group(1).lower()
                             for m in REMOTE_RE.finditer(text)})[:3],
    }


def looks_blocked(html_text: str) -> bool:
    """True when the HTML looks like a bot wall rather than a posting."""
    soup = BeautifulSoup(html_text, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    visible = soup_text(soup.body or soup)
    return len(visible) < 2000 and bool(BLOCK_SIGNALS.search(visible))


def check_liveness(text: str, status_ok: bool = True
                   ) -> tuple[bool | None, str]:
    """Classify a fetched page: (is_live, reason)."""
    if not status_ok:
        return False, "fetch failed / HTTP error"
    text = text or ""
    # block signals only count on short pages; real postings mention
    # "captcha"/"security check" in their own text legitimately
    if len(text) < 2000 and BLOCK_SIGNALS.search(text):
        return None, "page shows a bot challenge or block, not the posting"
    if DEAD_SIGNALS.search(text):
        return False, "page says the posting is closed/expired/removed"
    return True, "posting looks open"


def extract_balanced_json(txt: str, brace_start: int) -> str | None:
    """Return the {...} JSON substring starting at brace_start (balanced)."""
    depth, i, in_str, esc = 0, brace_start, False, False
    while i < len(txt):
        c = txt[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return txt[brace_start:i + 1]
        i += 1
    return None


def _walk_for_job_dict(obj) -> dict | None:
    """Find a sub-dict that looks like a posting (has descriptionHtml)."""
    if isinstance(obj, dict):
        if isinstance(obj.get("descriptionHtml"), str) \
                and obj.get("descriptionHtml"):
            return obj
        for value in obj.values():
            hit = _walk_for_job_dict(value)
            if hit:
                return hit
    elif isinstance(obj, list):
        for value in obj:
            hit = _walk_for_job_dict(value)
            if hit:
                return hit
    return None


def _normalize_embedded(hit: dict, company: str | None,
                        source: str) -> dict:
    loc = hit.get("location") or hit.get("locationName") or {}
    if isinstance(loc, dict):
        loc = loc.get("name")
    return {
        "title": hit.get("title") or hit.get("text"),
        "company": company,
        "location": loc,
        "employment_type": hit.get("employmentType"),
        "department": hit.get("departmentName"),
        "description_html": hit.get("descriptionHtml", ""),
        "posted": hit.get("publishedAt") or hit.get("updated_at"),
        "source": source,
    }


def parse_embedded_job_json(soup: BeautifulSoup) -> dict | None:
    """Pull job data from JS-embedded page state.

    Handles Ashby-style ``window.__appData``, Next.js ``__NEXT_DATA__``,
    and any script tag whose JSON contains a ``descriptionHtml`` field.
    """
    next_tag = soup.find("script", id="__NEXT_DATA__")
    if next_tag and next_tag.string:
        try:
            hit = _walk_for_job_dict(json.loads(next_tag.string))
            if hit:
                return _normalize_embedded(hit, None, "embedded-next-data")
        except Exception:
            pass
    for script in soup.find_all("script"):
        txt = script.string or ""
        if "descriptionHtml" not in txt:
            continue
        match = re.search(r"window\.__appData\s*=\s*\{", txt)
        if match:
            try:
                blob = extract_balanced_json(txt, match.end() - 1)
                app = json.loads(blob) if blob else {}
                posting = (app.get("posting")
                           if isinstance(app, dict) else None) or {}
                org = (app.get("organization")
                       if isinstance(app, dict) else None) or {}
                if posting.get("descriptionHtml"):
                    return {
                        "title": posting.get("title"),
                        "company": org.get("name"),
                        "location": posting.get("locationName"),
                        "employment_type": posting.get("employmentType"),
                        "department": posting.get("departmentName"),
                        "description_html": posting.get("descriptionHtml",
                                                        ""),
                        "compensation": posting.get(
                            "compensationTierSummary"),
                        "posted": posting.get("publishedAt"),
                        "source": "embedded-json",
                    }
            except Exception:
                pass
        try:
            hit = _walk_for_job_dict(json.loads(txt))
            if hit:
                return _normalize_embedded(hit, None, "embedded-json")
        except Exception:
            pass
    return None


def parse_json_ld(soup: BeautifulSoup) -> dict:
    """Pull schema.org JobPosting structured data if present."""
    out: dict = {}
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict) or item.get("@type") != "JobPosting":
                continue
            org = item.get("hiringOrganization") or {}
            loc = item.get("jobLocation")
            if isinstance(loc, list):
                loc = loc[0] if loc else {}
            addr = (loc or {}).get("address") or {}
            return {
                "title": item.get("title"),
                "company": org.get("name")
                if isinstance(org, dict) else org,
                "location": addr.get("addressLocality")
                if isinstance(addr, dict) else None,
                "employment_type": item.get("employmentType"),
                "description_html": item.get("description", ""),
                "posted": item.get("datePosted"),
                "source": "json-ld",
            }
    return out


# (currency, range-pattern, single-pattern, multiplier to annual)
SALARY_SPECS = [
    ("INR",
     re.compile(r"([\d.]+)\s*[-\u2013]\s*([\d.]+)\s*(?:lakh|LPA)", re.I),
     re.compile(r"([\d.]+)\s*(?:lakh|LPA)", re.I),
     100000),
    ("INR",
     re.compile(r"\u20b9\s?([\d,]+)\s*[-\u2013]\s*\u20b9?\s?([\d,]+)", re.I),
     re.compile(r"\u20b9\s?([\d,]+(?:\.\d+)?)", re.I),
     1),
    ("EUR",
     re.compile(r"\u20ac\s?([\d.,]+)\s*k?\s*[-\u2013]\s*([\d.,]+)\s*k?", re.I),
     re.compile(r"\u20ac\s?([\d.,]+)", re.I),
     1),
    ("USD",
     re.compile(r"\$\s?([\d,]+)\s*k?\s*[-\u2013]\s*([\d,]+)\s*k?", re.I),
     re.compile(r"\$\s?([\d,]+)", re.I),
     1),
    ("GBP",
     re.compile(r"\u00a3\s?([\d,]+)\s*k?\s*[-\u2013]\s*([\d,]+)\s*k?", re.I),
     re.compile(r"\u00a3\s?([\d,]+)", re.I),
     1),
]


def _num(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _annualize(value: float, hit: str, mult: float) -> int:
    factor = _period_factor(hit)
    # strip the pay-period marker before checking for a trailing "k"
    core = _TRAILING_PERIOD.sub("", hit)
    if re.search(r"k\s*$", core.strip(), re.I):
        value *= 1000
    return int(value * factor * mult)


# Annual multipliers per pay period (JobSpy enforce_annual_salary style):
# 40h weeks for hourly, 5d x 52w for daily. Longer words first so
# "daily" is not misread as a failed "day".
_PERIOD_FACTORS = (
    ("hourly", 2080), ("hour", 2080), ("hr", 2080),
    ("daily", 260), ("day", 260),
    ("weekly", 52), ("week", 52), ("wk", 52),
    ("monthly", 12), ("month", 12), ("mo", 12),
)


def _period_factor(hit: str) -> int:
    """Annual multiplier for a pay-period marker; 1 when absent or annual
    ("per year", "per annum", "LPA")."""
    m = _TRAILING_PERIOD.search(hit)
    marker = m.group(0).lower() if m else ""
    for keyword, factor in _PERIOD_FACTORS:
        if re.search(r"(?<![a-z])" + keyword + r"(?![a-z])", marker):
            return factor
    return 1


def normalize_salary(hits: list[str]) -> list[dict]:
    """Turn raw salary strings into structured annual figures.

    Returns [{raw, currency, min_annual, max_annual}]. A trailing 'k'
    multiplies by 1000; ranges like '50k-70k' or '18-22 LPA' split into
    min/max. Pay-period markers annualize the figure ("$50/hr" -> 104000
    USD, "€500/day" -> 130000 EUR, "$2,000/wk" -> 104000 USD,
    "₹80,000 per month" -> 960000 INR); unmarked figures keep the
    existing annual assumption. Unparseable hits are skipped.
    """
    out = []
    for hit in hits:
        for currency, range_re, single_re, mult in SALARY_SPECS:
            rng = range_re.search(hit)
            if rng:
                lo, hi = _num(rng.group(1)), _num(rng.group(2))
                if lo is not None and hi is not None:
                    out.append({
                        "raw": hit, "currency": currency,
                        "min_annual": _annualize(min(lo, hi), hit, mult),
                        "max_annual": _annualize(max(lo, hi), hit, mult),
                    })
                    break
            single = single_re.search(hit)
            if single:
                val = _num(single.group(1))
                if val is None:
                    continue
                annual = _annualize(val, hit, mult)
                out.append({"raw": hit, "currency": currency,
                            "min_annual": annual, "max_annual": annual})
                break
    return out
