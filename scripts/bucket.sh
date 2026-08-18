#!/usr/bin/env bash
#
# Create or tear down the Cloud Storage bucket the dataset is published to.
#
# Doing this by hand is how a bucket ends up in us-central1 with public access
# and no versioning, so it is a script. Everything it needs comes from .env:
# SECTORRADAR_GCP_PROJECT (or GOOGLE_CLOUD_PROJECT), SECTORRADAR_GCS_BUCKET,
# and SECTORRADAR_GCS_LOCATION.
#
# The project is passed explicitly on every command. Relying on the ambient
# `gcloud config` default is how commands get run against the wrong project.
#
# Usage:
#   scripts/bucket.sh create
#   scripts/bucket.sh grant colleague@example.com
#   scripts/bucket.sh revoke colleague@example.com
#   scripts/bucket.sh status
#   scripts/bucket.sh destroy          # asks before deleting anything

set -euo pipefail

RED=$'\033[31m'; BOLD=$'\033[1m'; DIM=$'\033[2m'; OFF=$'\033[0m'

die() { echo "${RED}error:${OFF} $*" >&2; exit 1; }

[ -f .env ] || die "no .env — copy .env.example and fill it in"
# shellcheck disable=SC1091
set -a; source .env; set +a

# Either name, matching config.py: gcloud already sets GOOGLE_CLOUD_PROJECT on
# most machines, and requiring only the prefixed one made `make bucket` refuse
# for an account whose pipeline ran perfectly.
PROJECT="${SECTORRADAR_GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}"
BUCKET="${SECTORRADAR_GCS_BUCKET:-}"
LOCATION="${SECTORRADAR_GCS_LOCATION:-europe-west6}"

[ -n "$PROJECT" ] || die "set SECTORRADAR_GCP_PROJECT (or GOOGLE_CLOUD_PROJECT) in .env"
[ -n "$BUCKET" ]  || die "SECTORRADAR_GCS_BUCKET is unset in .env"
command -v gcloud >/dev/null || die "gcloud is not installed"

URL="gs://${BUCKET}"

exists() { gcloud storage buckets describe "$URL" --project="$PROJECT" >/dev/null 2>&1; }

create() {
  if exists; then
    echo "${URL} already exists in ${PROJECT} — nothing to do."
    status
    return
  fi

  echo "Creating ${BOLD}${URL}${OFF} in ${PROJECT} (${LOCATION})"
  # Uniform access because per-object ACLs are unreviewable, and public access
  # prevention because this bucket must never be world-readable: the data comes
  # from public pages, but an aggregate of a market with your own position
  # marked in it is not something to leave open.
  gcloud storage buckets create "$URL" \
    --project="$PROJECT" \
    --location="$LOCATION" \
    --uniform-bucket-level-access \
    --public-access-prevention

  # Versioning, so a bad publish is recoverable rather than final.
  gcloud storage buckets update "$URL" --project="$PROJECT" --versioning

  # Keep at most a few old versions of each object. Without this, versioning
  # quietly accumulates every publish forever.
  local rules; rules="$(mktemp)"
  cat > "$rules" <<'JSON'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"numNewerVersions": 5, "isLive": false}
    },
    {
      "action": {"type": "Delete"},
      "condition": {"daysSinceNoncurrentTime": 90, "isLive": false}
    }
  ]
}
JSON
  gcloud storage buckets update "$URL" --project="$PROJECT" --lifecycle-file="$rules"
  rm -f "$rules"

  echo
  echo "Done. Publish with:  make publish EXECUTE=1"
  echo "Grant a colleague:   scripts/bucket.sh grant them@example.com"
}

grant() {
  local who="${1:-}"
  [ -n "$who" ] || die "usage: scripts/bucket.sh grant someone@example.com"
  exists || die "${URL} does not exist — run: scripts/bucket.sh create"

  # objectViewer is read-only. Nobody but whoever runs the crawl needs write
  # access to this bucket, and nobody should be given it.
  gcloud storage buckets add-iam-policy-binding "$URL" \
    --project="$PROJECT" \
    --member="user:${who}" \
    --role="roles/storage.objectViewer" >/dev/null

  echo "${who} can now read ${URL}."
  echo "They need SECTORRADAR_GCS_BUCKET and SECTORRADAR_GCP_PROJECT in their"
  echo ".env, then: gcloud auth application-default login && make app"
}

revoke() {
  local who="${1:-}"
  [ -n "$who" ] || die "usage: scripts/bucket.sh revoke someone@example.com"
  gcloud storage buckets remove-iam-policy-binding "$URL" \
    --project="$PROJECT" \
    --member="user:${who}" \
    --role="roles/storage.objectViewer" >/dev/null
  echo "${who} can no longer read ${URL}."
}

status() {
  if ! exists; then
    echo "${URL} does not exist in ${PROJECT}."
    echo "Create it with: scripts/bucket.sh create"
    return
  fi
  gcloud storage buckets describe "$URL" --project="$PROJECT" \
    --format="table[box](name, location, uniform_bucket_level_access_enabled:label=UNIFORM, versioning_enabled:label=VERSIONED, public_access_prevention:label=PUBLIC_BLOCKED)"
  echo
  echo "${DIM}readers:${OFF}"
  # `--filter` is not accepted by this subcommand, so the role is selected in
  # the format expression instead. Found by running it.
  gcloud storage buckets get-iam-policy "$URL" --project="$PROJECT" \
    --format="value(bindings.filter(\"role:roles/storage.objectViewer\").extract(members).flatten())" \
    2>/dev/null | tr ';' '\n' | sed '/^$/d; s/^/  /' || true
  echo
  echo "${DIM}published objects:${OFF}"
  gcloud storage ls -r "${URL}/**" --project="$PROJECT" 2>/dev/null | sed 's/^/  /' | head -20 || true
}

destroy() {
  exists || { echo "${URL} does not exist — nothing to destroy."; return; }

  local count size
  count="$(gcloud storage ls -r "${URL}/**" --project="$PROJECT" 2>/dev/null | grep -c . || true)"
  size="$(gcloud storage du -s "$URL" --project="$PROJECT" 2>/dev/null | awk '{print $1}' || echo '?')"

  # Say exactly what disappears, then require the bucket's own name typed back.
  # A y/N prompt is answered reflexively; typing the name is not.
  echo "${RED}${BOLD}This deletes the bucket and everything in it.${OFF}"
  echo
  echo "  bucket   : ${URL}"
  echo "  project  : ${PROJECT}"
  echo "  location : ${LOCATION}"
  echo "  objects  : ${count} (${size} bytes, all versions)"
  echo
  echo "Anyone you granted access to loses it. The published dataset is"
  echo "reproducible from your local database with 'make publish', so this is"
  echo "recoverable — but nothing else in the bucket is."
  echo
  printf "Type the bucket name to confirm: "
  read -r reply
  [ "$reply" = "$BUCKET" ] || die "got '${reply}', expected '${BUCKET}' — nothing was deleted"

  gcloud storage rm -r "$URL" --project="$PROJECT"
  echo "${URL} is gone."
}

case "${1:-}" in
  create)  create ;;
  grant)   grant "${2:-}" ;;
  revoke)  revoke "${2:-}" ;;
  status)  status ;;
  destroy) destroy ;;
  *)
    echo "usage: scripts/bucket.sh {create|grant EMAIL|revoke EMAIL|status|destroy}"
    exit 2
    ;;
esac
