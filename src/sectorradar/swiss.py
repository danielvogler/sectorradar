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
from collections.abc import Iterable
from typing import Final

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


#: English, French, German and Italian names for Swiss cities, folded to the
#: spelling swisstopo and the Swiss postal service actually use.
#:
#: The same problem as the cantons, and it bit in the same way: eight companies
#: recorded as being in "Geneva" were excluded from the segment as foreign,
#: because the geocoder answers to "Genève" and a plain substring check does
#: not connect the two.
CITY_EXONYMS: dict[str, str] = {
    "geneva": "Genève",
    "genf": "Genève",
    "ginevra": "Genève",
    "zurich": "Zürich",
    "zuerich": "Zürich",
    "zurigo": "Zürich",
    "lucerne": "Luzern",
    "lucerna": "Luzern",
    "berne": "Bern",
    "berna": "Bern",
    "basle": "Basel",
    "bale": "Basel",
    "basilea": "Basel",
    "st gallen": "St. Gallen",
    "st. gallen": "St. Gallen",
    "sankt gallen": "St. Gallen",
    "saint gall": "St. Gallen",
    "san gallo": "St. Gallen",
    "neuchatel": "Neuchâtel",
    "neuenburg": "Neuchâtel",
    "biel": "Biel/Bienne",
    "bienne": "Biel/Bienne",
    "sion": "Sion",
    "sitten": "Sion",
    "coire": "Chur",
    "coira": "Chur",
    "fribourg": "Fribourg",
    "freiburg": "Fribourg",
    "soleure": "Solothurn",
    "schaffhouse": "Schaffhausen",
    "thoune": "Thun",
    "morat": "Murten",
    "locarno": "Locarno",
    "lugano": "Lugano",
}


def canonical_city(value: str | None) -> str | None:
    """The spelling Swiss services answer to, or the input unchanged.

    Unlike :func:`canton_code` this does not refuse unknown values — most Swiss
    towns are not in the table above and are already spelled correctly. It only
    fixes the handful of places that have widely-used foreign-language names.
    """
    if not value or not value.strip():
        return None
    cleaned = " ".join(value.strip().split())
    return CITY_EXONYMS.get(_fold(cleaned), cleaned)


# --------------------------------------------------------------------------
# Languages
# --------------------------------------------------------------------------

#: The four national languages plus English, which is the working language of
#: a large part of this particular market. Anything else a site offers is real
#: but not a distinction anybody here filters on.
LANGUAGES: Final[tuple[str, ...]] = ("de", "fr", "it", "rm", "en")

_LANGUAGE_ALIASES: Final[dict[str, str]] = {
    "de": "de",
    "deutsch": "de",
    "german": "de",
    "allemand": "de",
    "tedesco": "de",
    "ger": "de",
    "fr": "fr",
    "französisch": "fr",
    "franzoesisch": "fr",
    "french": "fr",
    "francais": "fr",
    "français": "fr",
    "francese": "fr",
    "it": "it",
    "italienisch": "it",
    "italian": "it",
    "italien": "it",
    "italiano": "it",
    "rm": "rm",
    "romansh": "rm",
    "rumantsch": "rm",
    "rätoromanisch": "rm",
    "en": "en",
    "englisch": "en",
    "english": "en",
    "anglais": "en",
    "inglese": "en",
    "eng": "en",
}


def canonical_languages(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalise however a site's languages were written into sorted codes.

    The stored values were `de`, `de,en`, `en,de`, `english,german` and
    `german,english` — five spellings of at most two facts. Order carried no
    meaning and neither did the spelling, but both produced separate rows in
    every count, exactly as the cantons did before :func:`canton_code`.

    Sorted into the national order so `de,en` and `en,de` are one answer.
    """
    if value is None:
        return ()
    parts = value.split(",") if isinstance(value, str) else list(value)

    found: set[str] = set()
    for part in parts:
        key = part.strip().casefold().replace("-", "").split("_")[0]
        code = _LANGUAGE_ALIASES.get(key)
        if code:
            found.add(code)
    return tuple(code for code in LANGUAGES if code in found)
