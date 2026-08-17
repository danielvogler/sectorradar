"""Swiss geography, normalised.

A language model asked "which canton is this company in?" will answer with
whatever the website says, and Swiss websites say it in four languages and two
registers: "ZH", "Zürich", "Zurich", "Zuerich", "canton de Genève", "Ticino",
"Tessin". Left alone the canton column becomes unfilterable — the same place
appears three times in a dropdown and a filter on one misses the others.

It will also confidently answer with somewhere that is not a Swiss canton at
all. Real values observed in a single run: ``Alberta``, ``Hesse``,
``Switzerland``. Those are not spelling variants to be folded; they are wrong,
and the honest thing is to drop them rather than store a plausible-looking lie.

So this module does two jobs: fold every real spelling to the official
two-letter code, and refuse everything else.
"""

from __future__ import annotations

import unicodedata

#: The 26 cantons, code to canonical German/French/Italian name.
CANTONS: dict[str, str] = {
    "AG": "Aargau",
    "AI": "Appenzell Innerrhoden",
    "AR": "Appenzell Ausserrhoden",
    "BE": "Bern",
    "BL": "Basel-Landschaft",
    "BS": "Basel-Stadt",
    "FR": "Fribourg",
    "GE": "Genève",
    "GL": "Glarus",
    "GR": "Graubünden",
    "JU": "Jura",
    "LU": "Luzern",
    "NE": "Neuchâtel",
    "NW": "Nidwalden",
    "OW": "Obwalden",
    "SG": "St. Gallen",
    "SH": "Schaffhausen",
    "SO": "Solothurn",
    "SZ": "Schwyz",
    "TG": "Thurgau",
    "TI": "Ticino",
    "UR": "Uri",
    "VD": "Vaud",
    "VS": "Valais",
    "ZG": "Zug",
    "ZH": "Zürich",
}

#: Every spelling worth accepting, folded to ASCII lowercase, mapped to a code.
#: Covers the four national languages plus the English exonyms that Swiss firms
#: use on their own English-language pages.
_ALIASES: dict[str, str] = {
    # Zürich
    "zurich": "ZH",
    "zuerich": "ZH",
    "zurigo": "ZH",
    # Bern
    "bern": "BE",
    "berne": "BE",
    "berna": "BE",
    # Luzern
    "luzern": "LU",
    "lucerne": "LU",
    "lucerna": "LU",
    # Genève
    "geneve": "GE",
    "geneva": "GE",
    "genf": "GE",
    "ginevra": "GE",
    # Vaud
    "vaud": "VD",
    "waadt": "VD",
    # Valais
    "valais": "VS",
    "wallis": "VS",
    "vallese": "VS",
    # Ticino
    "ticino": "TI",
    "tessin": "TI",
    # Graubünden
    "graubunden": "GR",
    "grisons": "GR",
    "grigioni": "GR",
    "grischun": "GR",
    # Fribourg
    "fribourg": "FR",
    "freiburg": "FR",
    "friburgo": "FR",
    # Neuchâtel
    "neuchatel": "NE",
    "neuenburg": "NE",
    # Basel
    "basel": "BS",
    "basel-stadt": "BS",
    "bale": "BS",
    "basle": "BS",
    "basel-landschaft": "BL",
    "baselland": "BL",
    "basel-land": "BL",
    # St. Gallen
    "st gallen": "SG",
    "st. gallen": "SG",
    "sankt gallen": "SG",
    "saint gall": "SG",
    "san gallo": "SG",
    "stgallen": "SG",
    # Appenzell
    "appenzell innerrhoden": "AI",
    "appenzell ausserrhoden": "AR",
    # The rest, where the name is the code's expansion
    "aargau": "AG",
    "argovie": "AG",
    "argovia": "AG",
    "glarus": "GL",
    "glaris": "GL",
    "jura": "JU",
    "nidwalden": "NW",
    "obwalden": "OW",
    "schaffhausen": "SH",
    "schaffhouse": "SH",
    "schwyz": "SZ",
    "svitto": "SZ",
    "solothurn": "SO",
    "soleure": "SO",
    "thurgau": "TG",
    "thurgovie": "TG",
    "turgovia": "TG",
    "uri": "UR",
    "zug": "ZG",
    "zoug": "ZG",
    "zugo": "ZG",
}


def _fold(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace and common prefixes."""
    stripped = "".join(
        c
        for c in unicodedata.normalize("NFKD", text.replace("ü", "u").replace("ö", "o"))
        if not unicodedata.combining(c)
    )
    cleaned = " ".join(stripped.lower().split())
    for prefix in ("canton de ", "canton du ", "canton of ", "kanton ", "cantone di ", "cantone "):
        cleaned = cleaned.removeprefix(prefix)
    return cleaned.strip(" .,")


def canton_code(value: str | None) -> str | None:
    """Fold any spelling of a Swiss canton to its two-letter code.

    Returns ``None`` for anything that is not one — including plausible-looking
    answers like "Switzerland" or a foreign region. A wrong canton is worse than a
    missing one: it is unfilterable *and* it looks correct.
    """
    if not value or not value.strip():
        return None

    raw = value.strip()
    if len(raw) == 2 and raw.upper() in CANTONS:
        return raw.upper()

    return _ALIASES.get(_fold(raw))


def canton_name(code: str | None) -> str | None:
    """The canonical name for a code, for display and geocoding."""
    return CANTONS.get(code.upper()) if code and len(code) == 2 else None


def is_canton(value: str | None) -> bool:
    return canton_code(value) is not None
