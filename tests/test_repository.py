import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.repository import MetadataRepository
from app.repository_mongo import MongoMetadataRepository
from app.schemas import DocumentMetadata


def _doc(name: str = "resume.pdf", **overrides) -> DocumentMetadata:
    values = {
        "file_name": name,
        "file_type": name.rsplit(".", 1)[-1],
        "doc_category": "resume",
        "tags": ["resume", "latest"],
        "storage_path": f"uploads/{name}",
    }
    values.update(overrides)
    return DocumentMetadata(**values)


def test_repository_add_and_deactivate(tmp_path) -> None:
    metadata_file = tmp_path / "metadata.json"
    repository = MetadataRepository(str(metadata_file))

    document = DocumentMetadata(
        file_name="resume.pdf",
        file_type="pdf",
        doc_category="resume",
        tags=["resume", "latest"],
        storage_path="uploads/resume.pdf",
    )

    repository.add(document)
    assert len(repository.list_active()) == 1

    archived = repository.deactivate(document.id)
    assert archived is True
    assert len(repository.list_active()) == 0
    assert len(repository.list_all()) == 1


def test_deactivate_is_idempotent_and_unknown_returns_false(tmp_path) -> None:
    repository = MetadataRepository(str(tmp_path / "metadata.json"))
    document = repository.add(_doc())

    assert repository.deactivate(document.id) is True
    assert repository.deactivate(document.id) is True
    assert repository.deactivate("does-not-exist") is False
    stored = repository.get_by_id(document.id)
    assert stored is not None and stored.is_active is False


def test_get_by_id_returns_none_for_unknown(tmp_path) -> None:
    repository = MetadataRepository(str(tmp_path / "metadata.json"))
    document = repository.add(_doc())

    assert repository.get_by_id(document.id) == document
    assert repository.get_by_id("missing") is None


def test_add_with_existing_id_replaces_record(tmp_path) -> None:
    repository = MetadataRepository(str(tmp_path / "metadata.json"))
    document = repository.add(_doc())
    repository.add(document.model_copy(update={"doc_category": "cv"}))

    documents = repository.list_all()
    assert len(documents) == 1
    assert documents[0].doc_category == "cv"


def test_list_orders_newest_first(tmp_path) -> None:
    repository = MetadataRepository(str(tmp_path / "metadata.json"))
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old = repository.add(_doc("old.pdf", uploaded_at=base))
    newest = repository.add(_doc("newest.pdf", uploaded_at=base + timedelta(days=2)))
    middle = repository.add(_doc("middle.pdf", uploaded_at=base + timedelta(days=1)))

    assert [doc.id for doc in repository.list_all()] == [newest.id, middle.id, old.id]
    repository.deactivate(middle.id)
    assert [doc.id for doc in repository.list_active()] == [newest.id, old.id]


def test_atomic_write_leaves_no_temp_files(tmp_path) -> None:
    metadata_file = tmp_path / "metadata.json"
    repository = MetadataRepository(str(metadata_file))
    for index in range(5):
        repository.add(_doc(f"file{index}.pdf"))

    assert sorted(p.name for p in tmp_path.iterdir()) == ["metadata.json"]
    assert len(json.loads(metadata_file.read_text(encoding="utf-8"))) == 5


def test_failed_write_keeps_previous_file_and_cleans_temp(tmp_path, monkeypatch) -> None:
    metadata_file = tmp_path / "metadata.json"
    repository = MetadataRepository(str(metadata_file))
    first = repository.add(_doc("first.pdf"))
    before = metadata_file.read_text(encoding="utf-8")

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("app.repository.os.replace", broken_replace)
    with pytest.raises(OSError):
        repository.add(_doc("second.pdf"))
    monkeypatch.undo()

    assert metadata_file.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["metadata.json"]
    assert [doc.id for doc in repository.list_all()] == [first.id]


def test_concurrent_adds_keep_every_record(tmp_path) -> None:
    metadata_file = tmp_path / "metadata.json"
    repository = MetadataRepository(str(metadata_file))
    # A second instance on the same file must share the same lock.
    other = MetadataRepository(str(metadata_file))
    errors: list[BaseException] = []
    barrier = threading.Barrier(20)

    def worker(index: int) -> None:
        try:
            barrier.wait()
            target = repository if index % 2 else other
            target.add(_doc(f"file{index}.pdf"))
        except BaseException as exc:  # pragma: no cover - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(repository.list_all()) == 20
    assert len({doc.id for doc in repository.list_all()}) == 20


def test_corrupt_json_is_quarantined_and_store_stays_usable(tmp_path, caplog) -> None:
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_text("[{not json", encoding="utf-8")
    repository = MetadataRepository(str(metadata_file))

    with caplog.at_level("ERROR", logger="instaretriv.repository"):
        assert repository.list_all() == []

    quarantined = list(tmp_path.glob("metadata.json.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "[{not json"
    assert any("corrupt" in record.getMessage() for record in caplog.records)

    document = repository.add(_doc())
    assert repository.get_by_id(document.id) == document


def test_non_list_json_is_quarantined(tmp_path) -> None:
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_text('{"id": "x"}', encoding="utf-8")
    repository = MetadataRepository(str(metadata_file))

    assert repository.list_all() == []
    assert len(list(tmp_path.glob("metadata.json.corrupt-*"))) == 1


@pytest.mark.parametrize("content", ["", "   \n", "﻿"])
def test_empty_file_is_treated_as_empty_list(tmp_path, content) -> None:
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_text(content, encoding="utf-8")
    repository = MetadataRepository(str(metadata_file))

    assert repository.list_all() == []
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_invalid_records_are_skipped_but_preserved(tmp_path) -> None:
    metadata_file = tmp_path / "metadata.json"
    metadata_file.write_text(json.dumps([{"id": "broken", "file_name": "x"}]), encoding="utf-8")
    repository = MetadataRepository(str(metadata_file))

    assert repository.list_all() == []
    repository.add(_doc())
    raw = json.loads(metadata_file.read_text(encoding="utf-8"))
    assert {item["id"] for item in raw} >= {"broken"}
    assert len(repository.list_all()) == 1


def test_legacy_record_without_is_active_counts_as_active(tmp_path) -> None:
    metadata_file = tmp_path / "metadata.json"
    record = _doc().model_dump(mode="json")
    record.pop("is_active")
    metadata_file.write_text(json.dumps([record]), encoding="utf-8")
    repository = MetadataRepository(str(metadata_file))

    assert len(repository.list_active()) == 1
    assert repository.deactivate(record["id"]) is True
    assert repository.list_active() == []


def test_ping_and_status(tmp_path) -> None:
    repository = MetadataRepository(str(tmp_path / "metadata.json"))
    repository.ping()
    assert repository.status() == {"backend": "json", "available": True, "error": None, "fallback": False}


# --- MongoMetadataRepository with a fake pymongo client (mongomock is not installed) ---


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.indexes: list[tuple] = []

    @staticmethod
    def _matches(doc: dict, query: dict) -> bool:
        for key, expected in query.items():
            if isinstance(expected, dict) and "$ne" in expected:
                if doc.get(key) == expected["$ne"]:
                    return False
            elif doc.get(key) != expected:
                return False
        return True

    def create_index(self, keys, **kwargs) -> str:
        self.indexes.append((keys, kwargs))
        return "idx"

    def replace_one(self, query, payload, upsert=False):
        self.docs = [doc for doc in self.docs if not self._matches(doc, query)]
        self.docs.append({"_id": object(), **payload})

    def find(self, query, projection=None):
        return [{k: v for k, v in doc.items() if k != "_id"} for doc in self.docs if self._matches(doc, query)]

    def find_one(self, query, projection=None):
        found = self.find(query)
        return found[0] if found else None

    def update_one(self, query, update):
        for doc in self.docs:
            if self._matches(doc, query):
                doc.update(update["$set"])
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, uri, **kwargs) -> None:
        self.uri = uri
        self.kwargs = kwargs
        self.collection = _FakeCollection()
        self.closed = False
        self.pings = 0
        self.admin = SimpleNamespace(command=self._command)
        _FakeClient.instances.append(self)

    def _command(self, name: str) -> dict:
        self.pings += 1
        return {"ok": 1}

    def __getitem__(self, name):
        return {"documents": self.collection, "other": self.collection}

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_mongo(monkeypatch):
    import pymongo

    _FakeClient.instances = []
    monkeypatch.setattr(pymongo, "MongoClient", _FakeClient)
    return _FakeClient


def test_mongo_repository_crud_with_fake_client(fake_mongo) -> None:
    repository = MongoMetadataRepository("mongodb://example.invalid", "db", "documents", timeout_ms=1234)
    client = fake_mongo.instances[0]
    assert client.kwargs["serverSelectionTimeoutMS"] == 1234
    assert client.kwargs["connectTimeoutMS"] == 1234
    assert client.collection.indexes[0] == ("id", {"unique": True})

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old = repository.add(_doc("old.pdf", uploaded_at=base))
    new = repository.add(_doc("new.pdf", uploaded_at=base + timedelta(hours=1)))
    assert [doc.id for doc in repository.list_all()] == [new.id, old.id]
    assert repository.get_by_id(old.id) == old
    assert repository.get_by_id("missing") is None

    assert repository.deactivate(old.id) is True
    assert repository.deactivate(old.id) is True
    assert repository.deactivate("missing") is False
    assert [doc.id for doc in repository.list_active()] == [new.id]

    repository.ping()
    assert client.pings == 1


def test_mongo_repository_init_failure_closes_client(monkeypatch, fake_mongo) -> None:
    def failing_create_index(self, keys, **kwargs):
        raise TimeoutError("server selection timed out")

    monkeypatch.setattr(_FakeCollection, "create_index", failing_create_index)
    with pytest.raises(TimeoutError):
        MongoMetadataRepository("mongodb://example.invalid", "db", "documents")
    assert fake_mongo.instances[0].closed is True
