"""What a site has done to be findable.

This is the one part of the dataset that reads the HTML rather than the prose.
Everything a company *says* is extracted by a language model and carries a
quote; everything measured here is in the markup, is deterministic, and costs
nothing to recompute. That difference matters: "does this site publish
Organization schema" has a right answer, and no model should be asked to guess
at it.

The tests below fix the two things easiest to get wrong — treating a missing
signal as a failing one, and scoring a page that told the crawler not to index
it as if it were competing at all.
"""

from __future__ import annotations

from sectorradar import seo

FULL_PAGE = """
<!doctype html>
<html lang="de">
<head>
  <title>KI-Agenten für Schweizer Unternehmen | Beispiel AG</title>
  <meta name="description" content="Wir entwickeln autonome KI-Agenten für
    Schweizer KMU. Von der Potenzialanalyse bis zum produktiven Betrieb,
    mit Workshops und Support." />
  <link rel="canonical" href="https://example.ch/" />
  <link rel="alternate" hreflang="fr" href="https://example.ch/fr/" />
  <link rel="alternate" hreflang="en" href="https://example.ch/en/" />
  <meta property="og:title" content="KI-Agenten" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Organization","name":"Beispiel AG"}
  </script>
</head>
<body>
  <h1>KI-Agenten für Schweizer Unternehmen</h1>
  <h2>Was wir bauen</h2>
  <p>Wir entwickeln autonome Agenten.</p>
  <img src="a.png" alt="Ein Diagramm" />
  <a href="/services">Leistungen</a>
  <a href="/referenzen">Referenzen</a>
</body>
</html>
"""

BARE_PAGE = "<html><head></head><body><p>Hallo</p></body></html>"


def _analyse(pages: dict[str, str]) -> seo.SeoProfile:
    return seo.analyse(pages)


def test_a_well_built_page_is_recognised_as_such() -> None:
    profile = _analyse({"https://example.ch/": FULL_PAGE})

    assert profile.has_title
    assert profile.has_description
    assert profile.has_canonical
    assert profile.has_hreflang
    assert profile.has_open_graph
    assert profile.has_viewport
    assert profile.single_h1
    assert "Organization" in profile.schema_types


def test_a_bare_page_scores_low_without_crashing() -> None:
    profile = _analyse({"https://example.ch/": BARE_PAGE})

    assert not profile.has_title
    assert not profile.has_description
    assert profile.schema_types == ()
    assert profile.score == 0 or profile.score < 20


def test_no_pages_at_all_is_unknown_not_zero() -> None:
    """A site we never crawled has not failed at SEO — we have not looked."""
    profile = _analyse({})

    assert profile.pages_analysed == 0
    assert profile.is_unknown


def test_a_crawled_site_is_never_unknown() -> None:
    profile = _analyse({"https://example.ch/": BARE_PAGE})

    assert not profile.is_unknown


def test_declared_languages_are_counted_from_hreflang() -> None:
    profile = _analyse({"https://example.ch/": FULL_PAGE})

    assert profile.languages_declared >= 2


def test_a_noindex_home_page_is_recorded_because_it_explains_invisibility() -> None:
    page = '<html><head><meta name="robots" content="noindex,nofollow"></head><body></body></html>'
    profile = _analyse({"https://example.ch/": page})

    assert profile.blocks_indexing


def test_alt_text_ratio_reflects_images_that_have_it() -> None:
    page = '<html><body><img src="a" alt="x"><img src="b"><img src="c" alt="y"></body></html>'
    profile = _analyse({"https://example.ch/": page})

    assert 0.6 < profile.image_alt_ratio < 0.7


def test_a_site_with_no_images_is_not_punished_for_alt_text() -> None:
    """Nothing to caption is not the same as failing to caption it."""
    profile = _analyse({"https://example.ch/": BARE_PAGE})

    assert profile.image_alt_ratio == 1.0


def test_two_h1_tags_is_not_a_single_h1() -> None:
    page = "<html><body><h1>One</h1><h1>Two</h1></body></html>"
    profile = _analyse({"https://example.ch/": page})

    assert not profile.single_h1


def test_the_score_stays_in_range_and_rewards_the_better_site() -> None:
    good = _analyse({"https://example.ch/": FULL_PAGE})
    bad = _analyse({"https://example.ch/": BARE_PAGE})

    assert 0 <= bad.score <= good.score <= 100
    assert good.score > bad.score


def test_findings_name_what_is_missing_rather_than_only_scoring_it() -> None:
    """A number tells you where you stand; a finding tells you what to do."""
    profile = _analyse({"https://example.ch/": BARE_PAGE})

    assert profile.findings
    assert any("description" in f.lower() for f in profile.findings)


def test_a_good_site_has_few_findings() -> None:
    profile = _analyse({"https://example.ch/": FULL_PAGE})

    assert len(profile.findings) < len(_analyse({"https://example.ch/": BARE_PAGE}).findings)


def test_word_count_ignores_markup() -> None:
    profile = _analyse({"https://example.ch/": FULL_PAGE})

    assert 0 < profile.median_word_count < 200


def test_malformed_structured_data_does_not_break_the_analysis() -> None:
    page = '<html><head><script type="application/ld+json">{not json</script></head></html>'
    profile = _analyse({"https://example.ch/": page})

    assert profile.schema_types == ()
