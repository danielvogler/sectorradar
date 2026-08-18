"""Publishing to object storage, up to the point where it talks to Google.

Everything worth testing here happens before any network call. The plan is
computed from the filesystem alone: it needs no credentials, makes no request,
and cannot half-succeed — which is exactly why the dry run is the default and
why it is worth having as a separate function rather than a flag threaded
through the upload.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sectorradar import publish


@pytest.fixture
def exported(tmp_path: Path) -> Path:
    path = tmp_path / "seg.web.json"
    path.write_text(json.dumps({"companies": []}), encoding="utf-8")
    return path


@pytest.fixture
def built(tmp_path: Path) -> Path:
    site = tmp_path / "dist"
    (site / "assets").mkdir(parents=True)
    (site / "index.html").write_text("<html></html>", encoding="utf-8")
    (site / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (site / "assets" / "app.css").write_text("body{}", encoding="utf-8")
    return site


def test_the_data_document_lands_under_the_data_prefix(exported: Path) -> None:
    plan = publish.plan(bucket="b", project="p", data_file=exported)

    assert [u.blob_name for u in plan.uploads] == ["data/seg.web.json"]


def test_the_site_keeps_its_directory_structure(exported: Path, built: Path) -> None:
    plan = publish.plan(bucket="b", project="p", data_file=exported, site_dir=built)
    names = {u.blob_name for u in plan.uploads}

    assert "site/index.html" in names
    assert "site/assets/app.js" in names


def test_json_is_served_as_json_not_as_a_download() -> None:
    """`application/octet-stream` makes a browser download the file instead."""
    assert publish.content_type_for(Path("x.json")).startswith("application/json")
    assert publish.content_type_for(Path("x.html")).startswith("text/html")
    assert publish.content_type_for(Path("x.js")).startswith("text/javascript")


def test_an_unknown_extension_still_gets_a_type() -> None:
    assert publish.content_type_for(Path("x.unheardof")) == "application/octet-stream"


def test_a_missing_export_is_a_clear_error_not_a_traceback(tmp_path: Path) -> None:
    with pytest.raises(publish.PublishError, match="make data"):
        publish.plan(bucket="b", project="p", data_file=tmp_path / "absent.json")


def test_a_missing_site_directory_says_which_command_builds_it(exported: Path) -> None:
    with pytest.raises(publish.PublishError, match="make web"):
        publish.plan(bucket="b", project="p", data_file=exported, site_dir=Path("/nope/dist"))


def test_publishing_nothing_is_refused_rather_than_silently_succeeding() -> None:
    with pytest.raises(publish.PublishError, match="nothing to publish"):
        publish.plan(bucket="b", project="p")


def test_the_plan_describes_itself_well_enough_to_approve(exported: Path, built: Path) -> None:
    """The dry run's whole job is to be read before somebody types --execute."""
    described = publish.plan(
        bucket="my-bucket", project="my-proj", data_file=exported, site_dir=built
    ).describe()

    assert "my-bucket" in described
    assert "my-proj" in described
    assert "data/seg.web.json" in described


def test_total_bytes_counts_every_file(exported: Path, built: Path) -> None:
    plan = publish.plan(bucket="b", project="p", data_file=exported, site_dir=built)

    assert plan.total_bytes == sum(u.source.stat().st_size for u in plan.uploads)


def test_uploading_without_the_cloud_library_explains_the_extra(
    monkeypatch: pytest.MonkeyPatch, exported: Path
) -> None:
    """The gcp extra is optional, so its absence must be an instruction."""
    import builtins

    real_import = builtins.__import__

    def missing(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("google.cloud"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", missing)

    with pytest.raises(publish.PublishError, match="extra"):
        publish.execute(publish.plan(bucket="b", project="p", data_file=exported))


# --- what a reader can discover without a segment file -----------------------


class _Blob:
    def __init__(self, name: str) -> None:
        self.name = name


class _Client:
    """Enough of a storage client to exercise the listing logic."""

    def __init__(self, names: list[str]) -> None:
        self._names = names
        self.asked_prefix: str | None = None

    def list_blobs(self, bucket: str, prefix: str = "") -> list[_Blob]:
        self.asked_prefix = prefix
        return [_Blob(n) for n in self._names if n.startswith(prefix)]


def test_a_reader_can_discover_what_is_published(monkeypatch: pytest.MonkeyPatch) -> None:
    """They will not have the segment YAML, and asking them to name a slug they
    have no way to see makes the whole arrangement circular."""
    client = _Client(
        ["data/ai-assurance-ch.web.json", "data/pilates-zurich.web.json", "site/index.html"]
    )
    monkeypatch.setattr(publish, "_client", lambda project: client)

    assert publish.available(bucket="b", project="p") == ["ai-assurance-ch", "pilates-zurich"]


def test_listing_ignores_everything_that_is_not_a_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(["data/notes.txt", "data/x.web.json", "site/assets/app.js"])
    monkeypatch.setattr(publish, "_client", lambda project: client)

    assert publish.available(bucket="b", project="p") == ["x"]


def test_an_empty_bucket_lists_nothing_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(publish, "_client", lambda project: _Client([]))

    assert publish.available(bucket="b", project="p") == []


# --- uploading and downloading, against a stand-in for Google ----------------


class _FakeBlob:
    def __init__(self, name: str, store: dict[str, str], present: bool = False) -> None:
        self.name = name
        self._store = store
        self._present = present
        self.cache_control: str | None = None
        self.content_type: str | None = None

    def exists(self) -> bool:
        return self._present

    def upload_from_filename(self, path: str, content_type: str | None = None) -> None:
        self._store[self.name] = path
        self.content_type = content_type

    def download_to_filename(self, path: str) -> None:
        Path(path).write_text("{}", encoding="utf-8")


class _FakeBucket:
    def __init__(self, exists: bool, present: set[str], store: dict[str, str]) -> None:
        self._exists = exists
        self._present = present
        self._store = store
        self.blobs: dict[str, _FakeBlob] = {}

    def exists(self) -> bool:
        return self._exists

    def blob(self, name: str) -> _FakeBlob:
        blob = _FakeBlob(name, self._store, present=name in self._present)
        self.blobs[name] = blob
        return blob


class _FakeStorage:
    def __init__(self, bucket: _FakeBucket, names: list[str] | None = None) -> None:
        self._bucket = bucket
        self._names = names or []

    def bucket(self, name: str) -> _FakeBucket:
        return self._bucket

    def list_blobs(self, bucket: str, prefix: str = "") -> list[_Blob]:
        return [_Blob(n) for n in self._names if n.startswith(prefix)]


def test_the_data_document_is_uploaded_with_caching_off(
    monkeypatch: pytest.MonkeyPatch, exported: Path, built: Path
) -> None:
    """The data is compiled into the page, so a cached copy is stale *data*.

    That is the one failure publishing introduces which local use does not
    have, and it is worth a header rather than a warning in a README.
    """
    bucket = _FakeBucket(exists=True, present=set(), store={})
    monkeypatch.setattr(publish, "_client", lambda project: _FakeStorage(bucket))

    written = publish.execute(
        publish.plan(bucket="b", project="p", data_file=exported, site_dir=built)
    )

    assert written == 4
    assert bucket.blobs["data/seg.web.json"].cache_control == "no-cache"
    assert "max-age" in (bucket.blobs["site/index.html"].cache_control or "")


def test_uploading_to_a_bucket_that_does_not_exist_says_to_create_it(
    monkeypatch: pytest.MonkeyPatch, exported: Path
) -> None:
    bucket = _FakeBucket(exists=False, present=set(), store={})
    monkeypatch.setattr(publish, "_client", lambda project: _FakeStorage(bucket))

    with pytest.raises(publish.PublishError, match="does not exist"):
        publish.execute(publish.plan(bucket="b", project="p", data_file=exported))


def test_pulling_writes_the_document_where_the_page_looks_for_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bucket = _FakeBucket(exists=True, present={"data/seg.web.json"}, store={})
    monkeypatch.setattr(publish, "_client", lambda project: _FakeStorage(bucket))

    path = publish.pull(bucket="b", project="p", slug="seg", destination=tmp_path / "data")

    assert path.name == "seg.web.json"
    assert path.read_text(encoding="utf-8") == "{}"


def test_pulling_something_absent_names_what_is_actually_there(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A reader guessing a slug should be told the answer, not just refused."""
    bucket = _FakeBucket(exists=True, present=set(), store={})
    storage = _FakeStorage(bucket, names=["data/pilates-zurich.web.json"])
    monkeypatch.setattr(publish, "_client", lambda project: storage)

    with pytest.raises(publish.PublishError, match="pilates-zurich"):
        publish.pull(bucket="b", project="p", slug="wrong", destination=tmp_path)


def test_each_segment_gets_its_own_published_page(exported: Path, built: Path) -> None:
    """One `site/` was one slot: publishing a second market replaced the first
    market's page, while both datasets sat in `data/` looking correct."""
    plan = publish.plan(
        bucket="b", project="p", data_file=exported, site_dir=built, slug="pilates-zurich"
    )
    names = {u.blob_name for u in plan.uploads}

    assert "site/pilates-zurich/index.html" in names
    assert "site/index.html" not in names


def test_publishing_without_a_slug_still_writes_a_page(exported: Path, built: Path) -> None:
    """The slug is optional, so a caller that omits it is not left with nothing."""
    plan = publish.plan(bucket="b", project="p", data_file=exported, site_dir=built)

    assert "site/index.html" in {u.blob_name for u in plan.uploads}
