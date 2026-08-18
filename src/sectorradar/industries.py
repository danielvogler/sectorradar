"""One closed vocabulary for client industries.

Industries arrive as free text from an LLM reading a dozen different websites
in three languages, and free text does not aggregate. Left alone it produced
`hospitality` and `Hospitality`, `real estate` and `real_estate` and
`Real Estate` and `Immobilien` — six rows for one sector, in a table whose
entire job is to show where the market is concentrated. Eighty-two distinct
values is not a richer picture than twenty-five; it is the same picture,
shredded.

Two rules make this work:

* **Everything canonicalises or is refused.** An unrecognised sector returns
  ``None`` and the value is dropped rather than passed through. Passing
  unknowns through is exactly how eighty-two happened, and a vocabulary with a
  hole in it is not a vocabulary.
* **Near-synonyms merge.** `manufacturing` and `industrial` are the same
  column in any report a human would actually read, so they are one value. The
  distinction that is lost was never carrying information.

This is the same treatment :mod:`sectorradar.swiss` gives cantons, for the
same reason and after the same bug.
"""

from __future__ import annotations

import re
from typing import Final

#: The vocabulary. Deliberately short: this is a list people scan, not scroll.
#: Adding one is cheap; the discipline is that a new value has to be a sector
#: somebody would filter by, not a phrase that appeared on one website.
INDUSTRIES: Final[tuple[str, ...]] = (
    "agriculture",
    "automotive",
    "aviation_defence",
    "chemicals",
    "construction",
    "education",
    "energy",
    "environment",
    "finance",
    "healthcare",
    "hospitality",
    "industrial",
    "insurance",
    "legal",
    "logistics",
    "media",
    "non_profit",
    "pharma",
    "professional_services",
    "public_sector",
    "real_estate",
    "retail",
    "sports_entertainment",
    "technology",
    "telecom",
)

#: Phrases that name the *absence* of a sector. They must never become one:
#: "cross-industry" as a row in an industry table is worse than no row.
_NOT_A_SECTOR: Final[frozenset[str]] = frozenset(
    {
        "cross industry",
        "crossindustry",
        "various",
        "diverse",
        "multiple",
        "general",
        "all",
        "other",
        "misc",
        "miscellaneous",
        "none",
        "n a",
        "unknown",
        "branchenuebergreifend",
        "branchenübergreifend",
        "verschiedene",
        "business",
        "enterprise",
        "kmu",
        "sme",
        "b2b",
        "b2c",
    }
)

#: Alias → canonical. Keys are matched after normalisation (casefolded,
#: punctuation to spaces, collapsed whitespace), so only one spelling of each
#: alias is needed here. German, French and Italian are present because Swiss
#: companies write their reference pages in all three.
_ALIASES: Final[dict[str, str]] = {
    # finance
    "banking": "finance",
    "bank": "finance",
    "banken": "finance",
    "banque": "finance",
    "fintech": "finance",
    "financial services": "finance",
    "finanzen": "finance",
    "finanzdienstleistungen": "finance",
    "finanzsektor": "finance",
    "asset management": "finance",
    "wealth management": "finance",
    "private banking": "finance",
    "crypto": "finance",
    "crypto blockchain": "finance",
    "blockchain": "finance",
    "trading": "finance",
    "accounting": "finance",
    "treuhand": "finance",
    "payments": "finance",
    "payment": "finance",
    "zahlungsverkehr": "finance",
    # insurance
    "versicherung": "insurance",
    "versicherungen": "insurance",
    "assurance": "insurance",
    "insurtech": "insurance",
    "reinsurance": "insurance",
    "rueckversicherung": "insurance",
    "pension": "insurance",
    "vorsorge": "insurance",
    # healthcare
    "health": "healthcare",
    "health care": "healthcare",
    "gesundheit": "healthcare",
    "gesundheitswesen": "healthcare",
    "sante": "healthcare",
    "santé": "healthcare",
    "medtech": "healthcare",
    "medical": "healthcare",
    "medizin": "healthcare",
    "medizintechnik": "healthcare",
    "hospital": "healthcare",
    "spital": "healthcare",
    "clinic": "healthcare",
    "biomedical": "healthcare",
    "life sciences": "healthcare",
    "healthtech": "healthcare",
    "digital health": "healthcare",
    "wellbeing": "healthcare",
    "health wellbeing": "healthcare",
    "wellness": "healthcare",
    "fitness": "healthcare",
    # pharma
    "pharmaceutical": "pharma",
    "pharmaceuticals": "pharma",
    "pharmazie": "pharma",
    "biotech": "pharma",
    "biotechnology": "pharma",
    "biotechnologie": "pharma",
    # public sector
    "public": "public_sector",
    "government": "public_sector",
    "govtech": "public_sector",
    "oeffentliche hand": "public_sector",
    "öffentliche hand": "public_sector",
    "oeffentliche verwaltung": "public_sector",
    "verwaltung": "public_sector",
    "behoerden": "public_sector",
    "behörden": "public_sector",
    "kanton": "public_sector",
    "gemeinde": "public_sector",
    "bund": "public_sector",
    "administration publique": "public_sector",
    "secteur public": "public_sector",
    "municipal": "public_sector",
    "defense": "public_sector",
    # education
    "bildung": "education",
    "schule": "education",
    "schulen": "education",
    "university": "education",
    "universitaet": "education",
    "universität": "education",
    "hochschule": "education",
    "academia": "education",
    "research": "education",
    "forschung": "education",
    "edtech": "education",
    "training": "education",
    "weiterbildung": "education",
    "formation": "education",
    "education formation": "education",
    # retail
    "e commerce": "retail",
    "ecommerce": "retail",
    "commerce": "retail",
    "handel": "retail",
    "detailhandel": "retail",
    "einzelhandel": "retail",
    "grosshandel": "retail",
    "wholesale": "retail",
    "consumer goods": "retail",
    "konsumgueter": "retail",
    "konsumgüter": "retail",
    "fmcg": "retail",
    "fashion": "retail",
    "luxury": "retail",
    "luxusgueter": "retail",
    "handel e commerce": "retail",
    # industrial
    "manufacturing": "industrial",
    "industry": "industrial",
    "industrie": "industrial",
    "produktion": "industrial",
    "fertigung": "industrial",
    "maschinenbau": "industrial",
    "machinery": "industrial",
    "engineering": "industrial",
    "electronics": "industrial",
    "elektronik": "industrial",
    "semiconductor": "industrial",
    "mining": "industrial",
    "bergbau": "industrial",
    "metals": "industrial",
    "textiles": "industrial",
    "watchmaking": "industrial",
    "uhrenindustrie": "industrial",
    "horlogerie": "industrial",
    "packaging": "industrial",
    "verpackung": "industrial",
    # automotive
    "auto": "automotive",
    "mobility": "automotive",
    "mobilitaet": "automotive",
    "mobilität": "automotive",
    "automobil": "automotive",
    "automobile": "automotive",
    # energy
    "utilities": "energy",
    "energie": "energy",
    "energieversorgung": "energy",
    "power": "energy",
    "electricity": "energy",
    "elektrizitaet": "energy",
    "oil gas": "energy",
    "renewables": "energy",
    "erneuerbare energien": "energy",
    "energy utilities": "energy",
    "energy and sustainability": "energy",
    "cleantech": "energy",
    # environment
    "sustainability": "environment",
    "nachhaltigkeit": "environment",
    "climate": "environment",
    "klima": "environment",
    "climate environment": "environment",
    "environmental": "environment",
    "umwelt": "environment",
    "water": "environment",
    "wasser": "environment",
    "waste": "environment",
    "recycling": "environment",
    # construction
    "bau": "construction",
    "baugewerbe": "construction",
    "bauwesen": "construction",
    "architecture": "construction",
    "architektur": "construction",
    "civil engineering": "construction",
    "infrastructure": "construction",
    "infrastruktur": "construction",
    "large scale infrastructures": "construction",
    "facility management": "construction",
    "construction tech": "construction",
    "contech": "construction",
    # real estate
    "immobilien": "real_estate",
    "immobilier": "real_estate",
    "property": "real_estate",
    "proptech": "real_estate",
    "housing": "real_estate",
    # logistics
    "transport": "logistics",
    "transportation": "logistics",
    "shipping": "logistics",
    "freight": "logistics",
    "supply chain": "logistics",
    "lieferkette": "logistics",
    "spedition": "logistics",
    "verkehr": "logistics",
    "rail": "logistics",
    "bahn": "logistics",
    "post": "logistics",
    "warehousing": "logistics",
    "lagerhaltung": "logistics",
    # telecom
    "telecommunications": "telecom",
    "telekommunikation": "telecom",
    "telco": "telecom",
    "mobile": "telecom",
    "network operator": "telecom",
    # media
    "medien": "media",
    "publishing": "media",
    "verlag": "media",
    "broadcasting": "media",
    "rundfunk": "media",
    "advertising": "media",
    "werbung": "media",
    "journalism": "media",
    "journalismus": "media",
    "presse": "media",
    "communications": "media",
    "kommunikation": "media",
    # legal
    "law": "legal",
    "recht": "legal",
    "rechtsberatung": "legal",
    "legaltech": "legal",
    "anwalt": "legal",
    "anwaltskanzlei": "legal",
    "compliance": "legal",
    "regtech": "legal",
    "juridique": "legal",
    # hospitality
    "gastronomie": "hospitality",
    "gastgewerbe": "hospitality",
    "hotel": "hospitality",
    "hotellerie": "hospitality",
    "tourism": "hospitality",
    "tourismus": "hospitality",
    "tourisme": "hospitality",
    "travel": "hospitality",
    "reisen": "hospitality",
    "restaurant": "hospitality",
    "food service": "hospitality",
    "events": "hospitality",
    # agriculture
    "landwirtschaft": "agriculture",
    "farming": "agriculture",
    "agritech": "agriculture",
    "agrar": "agriculture",
    "food": "agriculture",
    "food beverage": "agriculture",
    "lebensmittel": "agriculture",
    "nahrungsmittel": "agriculture",
    "beverage": "agriculture",
    "getraenke": "agriculture",
    "forestry": "agriculture",
    "forstwirtschaft": "agriculture",
    # non profit
    "nonprofit": "non_profit",
    "non profit": "non_profit",
    "not for profit": "non_profit",
    "ngo": "non_profit",
    "ngos": "non_profit",
    "charity": "non_profit",
    "charities": "non_profit",
    "foundation": "non_profit",
    "stiftung": "non_profit",
    "verein": "non_profit",
    "association": "non_profit",
    "verband": "non_profit",
    "social": "non_profit",
    "soziales": "non_profit",
    "humanitarian": "non_profit",
    "international organisation": "non_profit",
    # technology
    "tech": "technology",
    "it": "technology",
    "software": "technology",
    "saas": "technology",
    "information technology": "technology",
    "informatik": "technology",
    "ict": "technology",
    "cloud": "technology",
    "cybersecurity": "technology",
    "security": "technology",
    "sicherheit": "technology",
    "data": "technology",
    "telecom software": "technology",
    "startup": "technology",
    "startups": "technology",
    "gaming": "technology",
    "comparison portal": "technology",
    "vergleichsportal": "technology",
    "marketplace": "technology",
    "plattform": "technology",
    # professional services
    "consulting": "professional_services",
    "beratung": "professional_services",
    "unternehmensberatung": "professional_services",
    "conseil": "professional_services",
    "marketing": "professional_services",
    "sales": "professional_services",
    "vertrieb": "professional_services",
    "hr": "professional_services",
    "human resources": "professional_services",
    "personalwesen": "professional_services",
    "recruiting": "professional_services",
    "recruitment": "professional_services",
    "staffing": "professional_services",
    "agency": "professional_services",
    "agentur": "professional_services",
    "design": "professional_services",
    "audit": "professional_services",
    "professional services": "professional_services",
    "language services": "professional_services",
    "translation": "professional_services",
    "uebersetzung": "professional_services",
    "übersetzung": "professional_services",
    # chemicals
    "chemie": "chemicals",
    "chemical": "chemicals",
    "chemical industry": "chemicals",
    "materials": "chemicals",
    "werkstoffe": "chemicals",
    # aviation and defence
    "aviation": "aviation_defence",
    "aerospace": "aviation_defence",
    "luftfahrt": "aviation_defence",
    "airline": "aviation_defence",
    "space": "aviation_defence",
    "raumfahrt": "aviation_defence",
    "military": "aviation_defence",
    "verteidigung": "aviation_defence",
    # sport and entertainment
    "sport": "sports_entertainment",
    "sports": "sports_entertainment",
    "entertainment": "sports_entertainment",
    "unterhaltung": "sports_entertainment",
    "music": "sports_entertainment",
    "musik": "sports_entertainment",
    "film": "sports_entertainment",
    "culture": "sports_entertainment",
    "kultur": "sports_entertainment",
    "art": "sports_entertainment",
    "kunst": "sports_entertainment",
    "sports tech": "sports_entertainment",
    "sporttech": "sports_entertainment",
    "exhibition": "sports_entertainment",
    "museum": "sports_entertainment",
    "messe": "sports_entertainment",
}

_PUNCTUATION = re.compile(r"[^\w\s]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

#: Separators that mark a compound sector name. Hyphens are absent on purpose:
#: "E-Commerce" is one word, not two, and splitting it loses the sector.
_COMPOUND = re.compile(r"\s*(?:&|/|\||,|\+|\band\b|\bund\b|\bet\b|\bo\b)\s*", re.IGNORECASE)


def _normalise(value: str) -> str:
    """Casefold, strip punctuation, collapse whitespace.

    This is what makes `Real Estate`, `real-estate` and `real_estate` the same
    key, so each alias needs to be written once rather than in every casing
    somebody might use.
    """
    text = value.replace("_", " ").casefold()
    text = _PUNCTUATION.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def canonical_industry(value: str | None) -> str | None:
    """Map a free-text industry onto the vocabulary, or refuse it.

    Returns ``None`` for anything not recognised, including phrases like
    "cross-industry" that name no sector at all. Callers drop what comes back
    empty — a value that cannot be aggregated is not worth storing.
    """
    if not value:
        return None

    text = _normalise(value)
    if not text or text in _NOT_A_SECTOR:
        return None

    direct = text.replace(" ", "_")
    if direct in INDUSTRIES:
        return direct
    if text in _ALIASES:
        return _ALIASES[text]

    # A compound like "Handel & E-Commerce" or "Energy and Sustainability"
    # names a lead sector and a qualifier. Take the lead: it is what whoever
    # wrote the phrase put first, and guessing between the halves is worse than
    # being predictable about which one wins.
    #
    # Split the *original* string, not the normalised one — normalising strips
    # the very separators that mark the compound. And split only on explicit
    # separators, never on plain whitespace: "interdimensional freight" would
    # otherwise resolve to logistics on the strength of its second word, which
    # is how a vocabulary quietly stops meaning anything.
    parts = [p for p in _COMPOUND.split(value) if p.strip()]
    if len(parts) > 1:
        for part in parts:
            resolved = canonical_industry(part)
            if resolved is not None:
                return resolved

    return None
