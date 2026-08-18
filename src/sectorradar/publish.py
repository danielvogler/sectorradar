"""Publish the dataset to Google Cloud Storage, and pull it back down.

The shape of the problem: one person runs the crawl, several people want to
look at the result, and nobody wants to run a server. That is an object-store
problem, not a database problem — see ``docs/operations.md`` for why this is
GCS rather than Cloud SQL or BigQuery.

What gets published is the same JSON document the local page already reads,
plus the built site beside it. A colleague either opens the hosted page or runs
:func:`pull` and serves it locally; either way they are reading bytes the
pipeline wrote, and there is no service in between to run, patch or pay for.

Three rules this module does not bend:

* **Application Default Credentials only.** Never a downloaded service-account
  key. A ``*.json`` key in a repo or a home directory is the most common way a
  project like this leaks, and there is nothing here that needs one.
* **Dry run is the default.** Uploading to shared storage is outward-facing and
  awkward to take back, so the default run prints exactly what it would send
  and writes nothing. ``--execute`` is a deliberate act.
* **Never public.** No ``allUsers``, no public bucket, no signed URL handed
  out. Access is IAM, granted per person, which is exactly what "give a
  colleague access by email" means on GCP.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from sectorradar.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from collections.abc import Iterable

log = get_logger(__name__)

#: Zurich. Data about Swiss companies has no business sitting in us-central1
#: because that is what the console offered first.
DEFAULT_LOCATION: Final = "europe-west6"

#: Where things land in the bucket. `data/` is what `pull` reads; `site/` is
#: the built page. Keeping them apart means a colleague can take the data
#: without the site, which is the whole point of exporting a document rather
#: than only rendering one.
DATA_PREFIX: Final = "data"
SITE_PREFIX: Final = "site"

#: Content types the guesser gets wrong or does not know. Serving JSON as
#: application/octet-stream makes a browser download it instead of showing it.
_CONTENT_TYPES: Final[dict[str, str]] = {
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}


class PublishError(RuntimeError):
    """Something about the destination or the credentials is wrong."""


@dataclass
class Upload:
    """One file, and where it is going."""

    source: Path
    blob_name: str
    content_type: str
    size: int


@dataclass
class PublishPlan:
    """What a publish would do. Printed on a dry run, executed otherwise."""

    bucket: str
    project: str
    uploads: list[Upload] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(u.size for u in self.uploads)

    def describe(self) -> str:
        lines = [
            f"project : {self.project}",
            f"bucket  : gs://{self.bucket}",
            f"files   : {len(self.uploads)} ({self.total_bytes / 1024:.0f} KiB)",
            "",
        ]
        lines += [f"  {u.blob_name}  ({u.size / 1024:.1f} KiB)" for u in self.uploads]
        return "\n".join(lines)


def content_type_for(path: Path) -> str:
    """Best content type for a file, preferring the table above to the guesser."""
    known = _CONTENT_TYPES.get(path.suffix.lower())
    if known:
        return known
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _files_under(root: Path) -> Iterable[Path]:
    return (p for p in sorted(root.rglob("*")) if p.is_file())


def plan(
    *,
    bucket: str,
    project: str,
    data_file: Path | None = None,
    site_dir: Path | None = None,
    slug: str | None = None,
) -> PublishPlan:
    """Work out what would be uploaded, without talking to GCP at all.

    Separated from the upload so the dry run is genuinely free: it needs no
    credentials, makes no network call, and cannot half-succeed.
    """
    result = PublishPlan(bucket=bucket, project=project)

    if data_file is not None:
        if not data_file.exists():
            msg = f"no exported document at {data_file} — run `make data` first"
            raise PublishError(msg)
        result.uploads.append(
            Upload(
                source=data_file,
                blob_name=f"{DATA_PREFIX}/{data_file.name}",
                content_type=content_type_for(data_file),
                size=data_file.stat().st_size,
            )
        )

    if site_dir is not None:
        if not site_dir.is_dir():
            msg = f"no built site at {site_dir} — run `make web` first"
            raise PublishError(msg)
        # One page per segment. A single `site/` was a single slot: publishing
        # a second market silently replaced the first market's page, while both
        # datasets sat in `data/` looking fine. The build is per-segment — its
        # title, its numbers, its embedded document — so its home has to be too.
        site_prefix = f"{SITE_PREFIX}/{slug}" if slug else SITE_PREFIX
        for path in _files_under(site_dir):
            relative = path.relative_to(site_dir).as_posix()
            result.uploads.append(
                Upload(
                    source=path,
                    blob_name=f"{site_prefix}/{relative}",
                    content_type=content_type_for(path),
                    size=path.stat().st_size,
                )
            )

    if not result.uploads:
        msg = "nothing to publish: pass a data file, a site directory, or both"
        raise PublishError(msg)
    return result


def _client(project: str) -> Any:
    """A GCS client on Application Default Credentials.

    Imported here rather than at module scope so that planning, and the whole
    test suite, work without the cloud libraries installed.
    """
    try:
        from google.cloud import storage
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        msg = (
            "publishing needs the gcp extra: `uv sync --extra gcp`. "
            "It is optional because the tool is useful with no cloud account at all."
        )
        raise PublishError(msg) from exc

    try:
        return storage.Client(project=project)
    except Exception as exc:  # pragma: no cover - depends on local credentials
        msg = (
            f"could not authenticate to project {project}. "
            "Run `gcloud auth application-default login` — this tool never uses "
            "a downloaded service-account key."
        )
        raise PublishError(msg) from exc


def execute(publish_plan: PublishPlan) -> int:
    """Upload everything in the plan. Returns the number of files written."""
    client = _client(publish_plan.project)
    bucket = client.bucket(publish_plan.bucket)

    if not bucket.exists():
        msg = (
            f"bucket gs://{publish_plan.bucket} does not exist. "
            "Create it with uniform access and versioning first — "
            "see docs/operations.md."
        )
        raise PublishError(msg)

    written = 0
    for item in publish_plan.uploads:
        blob = bucket.blob(item.blob_name)
        # The data document must never be served stale — somebody looking at
        # last week's numbers and believing they are this week's is the one
        # failure mode publishing introduces that local use does not have.
        is_data = item.blob_name.startswith(DATA_PREFIX)
        blob.cache_control = "no-cache" if is_data else "public, max-age=300"
        blob.upload_from_filename(str(item.source), content_type=item.content_type)
        written += 1
        log.info("publish.uploaded", blob=item.blob_name, bytes=item.size)

    log.info("publish.done", bucket=publish_plan.bucket, files=written)
    return written


def available(*, bucket: str, project: str) -> list[str]:
    """Which segments have been published to this bucket.

    The bucket is the only thing a reader has. They will not have the segment
    YAML — a market somebody keeps private is exactly the kind most worth
    sharing the *result* of — so asking them to name a slug they have no way to
    discover makes the whole arrangement circular.
    """
    client = _client(project)
    prefix = f"{DATA_PREFIX}/"
    return sorted(
        blob.name[len(prefix) : -len(".web.json")]
        for blob in client.list_blobs(bucket, prefix=prefix)
        if blob.name.endswith(".web.json")
    )


def pull(*, bucket: str, project: str, slug: str, destination: Path) -> Path:
    """Download the exported document so a colleague can serve the page locally.

    The counterpart to publishing: one person crawls, everybody else reads.
    Nobody needs the database, the API keys, the segment definition, or a
    Python environment beyond this command. The exported document carries the
    market's whole definition — name, inclusion rule, tiers, vocabulary,
    queries — which is what lets a reader interpret figures for a market they
    have no config file for.
    """
    client = _client(project)
    blob = client.bucket(bucket).blob(f"{DATA_PREFIX}/{slug}.web.json")
    if not blob.exists():
        try:
            published = available(bucket=bucket, project=project)
        except Exception:
            published = []
        hint = f" Published there: {', '.join(published)}." if published else ""
        msg = f"no published data for '{slug}' in gs://{bucket}.{hint}"
        raise PublishError(msg)

    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{slug}.web.json"
    blob.download_to_filename(str(target))
    log.info("publish.pulled", blob=blob.name, path=str(target))
    return target
