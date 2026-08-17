#!/usr/bin/env bash
#
# The completion condition for the sectorradar build.
#
#   make verify  ->  this script
#
# It runs the repo-only `make check` first, then every data-dependent phase
# gate from the build plan against the local data/radar.db.
#
# Two rules govern this file:
#
#   1. It is append-only in spirit. Checks are added, never removed or
#      weakened to go green. If a gate fails, fix the code.
#
#   2. A SKIP is not a pass. Gates whose phase has not landed yet print
#      "SKIP: phase N not reached" so the build can run this from day one,
#      but any SKIP at all makes the script exit non-zero. The build is only
#      complete when nothing is skipped and nothing fails.
#
# Whether a phase has been reached is decided by the presence of that phase's
# code, not by anything self-reported. A gate therefore flips from SKIP to a
# hard check the moment its module lands, with no edit to this file.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

DB="${SECTORRADAR_DB_PATH:-data/radar.db}"

SKIPS=0
FAILS=0

pass() { printf 'PASS: %s\n' "$1"; }
skip() { printf 'SKIP: %s\n' "$1"; SKIPS=$((SKIPS + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; FAILS=$((FAILS + 1)); }

# q <sql> -- run a scalar query, echo 0 if the database is missing.
q() {
  if [ ! -f "$DB" ]; then echo 0; return; fi
  sqlite3 "$DB" "$1" 2>/dev/null || echo 0
}

# expect_ge <actual> <minimum> <description>
expect_ge() {
  local actual="${1:-0}" minimum="$2" what="$3"
  if [ -z "$actual" ]; then actual=0; fi
  if [ "$actual" -ge "$minimum" ] 2>/dev/null; then
    pass "$what ($actual >= $minimum)"
  else
    fail "$what ($actual < $minimum)"
  fi
}

echo "=============================================="
echo " sectorradar verify"
echo "=============================================="
echo
echo "--- repo health (make check) ---"
if make check; then
  pass "make check"
else
  fail "make check"
fi

echo
echo "--- phase 1: schema, config, CLI ---"
if [ -f src/sectorradar/db.py ]; then
  if uv run sectorradar init >/dev/null 2>&1 && uv run sectorradar doctor >/dev/null 2>&1; then
    pass "sectorradar init && doctor"
  else
    fail "sectorradar init && doctor"
  fi
  tables=$(q "select count(*) from sqlite_master where type='table' and name in
    ('segment','company','membership','company_field','offering','tag',
     'candidate','discovery_run','page','snapshot','schema_version');")
  expect_ge "$tables" 11 "core tables present"
else
  skip "phase 1 not reached (no src/sectorradar/db.py)"
fi

echo
echo "--- phase 2: seeds, resolve, geocode ---"
if [ -f src/sectorradar/resolve.py ]; then
  expect_ge "$(q 'select count(*) from company where lat is not null and lon is not null;')" \
    20 "companies with coordinates"
else
  skip "phase 2 not reached (no src/sectorradar/resolve.py)"
fi

echo
echo "--- phase 3: fetch, extract, classify ---"
if [ -f src/sectorradar/extract.py ]; then
  expect_ge "$(q "select count(*) from offering where evidence_quote != '';")" \
    20 "offerings carrying an evidence quote"
  orphan=$(q "select count(*) from offering where evidence_url is null or evidence_url = '';")
  if [ "${orphan:-1}" = "0" ]; then
    pass "every offering has a source URL"
  else
    fail "$orphan offerings have no source URL"
  fi
else
  skip "phase 3 not reached (no src/sectorradar/extract.py)"
fi

echo
echo "--- phase 4: discovery and stats ---"
if [ -f src/sectorradar/stats.py ]; then
  expect_ge "$(q 'select count(*) from candidate;')" 100 "candidates discovered"
  expect_ge "$(q 'select count(*) from company;')" 150 "companies in the database"
  expect_ge "$(q "select count(*) from membership where tier in (1,2) and tier_rationale is not null and tier_rationale != '';")" \
    100 "tier 1-2 companies with a rationale"
  recall=$(uv run sectorradar stats --segment agentic-ai-ch --recall-only 2>/dev/null | tr -d ' %')
  if [ -n "$recall" ] && [ "${recall%.*}" -ge 80 ] 2>/dev/null; then
    pass "gold-set recall (${recall}% >= 80%)"
  else
    fail "gold-set recall (${recall:-unavailable} < 80%)"
  fi
else
  skip "phase 4 not reached (no src/sectorradar/stats.py)"
fi

echo
echo "--- phase 5: long tail, second segment ---"
if [ -f segments/genai-training-ch.yaml ]; then
  expect_ge "$(q "select count(*) from membership where segment_slug='genai-training-ch';")" \
    1 "second segment populated from YAML alone"
  expect_ge "$(q "select count(*) from snapshot;")" 1 "snapshots taken"
  if ls data/exports/*.geojson >/dev/null 2>&1; then
    pass "geojson export produced"
  else
    fail "no geojson export in data/exports/"
  fi
else
  skip "phase 5 not reached (no segments/genai-training-ch.yaml)"
fi

echo
echo "--- phase 6: hardening ---"
if [ -f docs/architecture.md ]; then
  # Coverage and mypy already ran inside `make check`; what is specific here is
  # that no type: ignore was used as a blunt instrument.
  bare=$(grep -rn 'type: *ignore' src/ 2>/dev/null | grep -vc 'type: *ignore\[' || true)
  if [ "${bare:-0}" = "0" ]; then
    pass "no bare type: ignore in src/"
  else
    fail "$bare bare 'type: ignore' without an error code in src/"
  fi
  for d in docs/architecture.md docs/adding-a-segment.md docs/operations.md; do
    if [ -s "$d" ]; then pass "$d written"; else fail "$d missing or empty"; fi
  done
else
  skip "phase 6 not reached (no docs/architecture.md)"
fi

echo
echo "--- phase 7: release readiness ---"
# Keyed on a phase 6 artefact, not on CHANGELOG.md: the changelog is written
# during scaffolding, so it would false-trigger this gate from day one. Phase 7
# is release paperwork layered on a finished phase 6.
if [ -f docs/operations.md ]; then
  pyproj=$(grep -m1 '^version = ' pyproject.toml | cut -d'"' -f2)
  initv=$(grep -m1 '^__version__ = ' src/sectorradar/__init__.py | cut -d'"' -f2)
  if [ "$pyproj" = "$initv" ] && [ -n "$pyproj" ]; then
    pass "version consistent ($pyproj)"
  else
    fail "version mismatch: pyproject=$pyproj __init__=$initv"
  fi
  if git rev-parse -q --verify refs/tags/v0.1.0 >/dev/null; then
    pass "v0.1.0 tagged"
  else
    fail "v0.1.0 not tagged"
  fi
  unticked=$(grep -c '^- \[ \] Phase' notes/PROGRESS.md 2>/dev/null || echo 0)
  if [ "${unticked:-1}" = "0" ]; then
    pass "every gate ticked in notes/PROGRESS.md"
  else
    fail "$unticked gate checkboxes still unticked in notes/PROGRESS.md"
  fi
else
  skip "phase 7 not reached (no [0.1.0] section in CHANGELOG.md)"
fi

echo
echo "=============================================="
if [ "$FAILS" -gt 0 ] || [ "$SKIPS" -gt 0 ]; then
  echo " VERIFY INCOMPLETE — ${FAILS} failed, ${SKIPS} skipped."
  echo " A SKIP is not a pass: the build is done only when"
  echo " every gate runs and every gate passes."
  echo "=============================================="
  exit 1
fi
echo " VERIFY PASS — every gate ran and every gate passed."
echo "=============================================="
exit 0
