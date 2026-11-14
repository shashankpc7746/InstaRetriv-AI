import json
import shutil

import pytest

from app.request_log_repository import MongoRequestLogRepository, RequestLogRepository


def test_add_sets_timestamp_without_mutating_payload(tmp_path) -> None:
    logs = RequestLogRepository(str(tmp_path / "logs.json"))
    payload = {"type": "webhook", "query": "resume"}
    logs.add(payload)

    assert "timestamp" not in payload
    entry = logs.latest(limit=1)[0]
    assert entry["type"] == "webhook"
    assert "T" in entry["timestamp"]


def test_latest_returns_oldest_to_newest_of_last_n(tmp_path) -> None:
    logs = RequestLogRepository(str(tmp_path / "logs.json"))
    for index in range(5):
        logs.add({"type": "webhook", "n": index})

    assert [entry["n"] for entry in logs.latest(limit=3)] == [2, 3, 4]
    assert [entry["n"] for entry in logs.latest(limit=50)] == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("limit", [0, -1, -100])
def test_non_positive_limit_returns_empty(tmp_path, limit) -> None:
    logs = RequestLogRepository(str(tmp_path / "logs.json"))
    logs.add({"type": "webhook"})

    assert logs.latest(limit=limit) == []
    assert logs.latest_by_type("webhook", limit=limit) == []


def test_latest_by_type_filters(tmp_path) -> None:
    logs = RequestLogRepository(str(tmp_path / "logs.json"))
    for index in range(4):
        logs.add({"type": "webhook" if index % 2 == 0 else "upload", "n": index})

    assert [entry["n"] for entry in logs.latest_by_type("webhook")] == [0, 2]
    assert [entry["n"] for entry in logs.latest_by_type("upload", limit=1)] == [3]
    assert logs.latest_by_type("unknown") == []


def test_entries_are_trimmed_to_max_entries(tmp_path) -> None:
    log_file = tmp_path / "logs.json"
    logs = RequestLogRepository(str(log_file), max_entries=3)
    for index in range(7):
        logs.add({"type": "webhook", "n": index})

    stored = json.loads(log_file.read_text(encoding="utf-8"))
    assert [entry["n"] for entry in stored] == [4, 5, 6]


def test_add_never_raises_when_path_becomes_unwritable(tmp_path, caplog) -> None:
    log_file = tmp_path / "logs.json"
    logs = RequestLogRepository(str(log_file))
    logs.add({"type": "webhook"})

    log_file.unlink()
    log_file.mkdir()  # the path is now a directory: reads and replaces fail
    with caplog.at_level("ERROR", logger="instaretriv.request_logs"):
        logs.add({"type": "webhook"})
    assert any("Failed to write request log" in record.getMessage() for record in caplog.records)
    assert logs.latest() == []

    shutil.rmtree(log_file)
    logs.add({"type": "webhook", "n": 1})
    assert [entry.get("n") for entry in logs.latest()] == [1]


def test_add_never_raises_on_unserialisable_payload(tmp_path) -> None:
    logs = RequestLogRepository(str(tmp_path / "logs.json"))
    logs.add({"type": "webhook", "value": object()})  # serialised via str()
    logs.add(None)  # type: ignore[arg-type]
    assert len(logs.latest()) == 1


def test_corrupt_log_file_is_quarantined(tmp_path) -> None:
    log_file = tmp_path / "logs.json"
    log_file.write_text("not json at all", encoding="utf-8")
    logs = RequestLogRepository(str(log_file))

    assert logs.latest() == []
    assert len(list(tmp_path.glob("logs.json.corrupt-*"))) == 1
    logs.add({"type": "webhook"})
    assert len(logs.latest()) == 1


def test_empty_log_file_is_treated_as_empty_list(tmp_path) -> None:
    log_file = tmp_path / "logs.json"
    log_file.write_text("", encoding="utf-8")
    logs = RequestLogRepository(str(log_file))

    assert logs.latest() == []
    logs.add({"type": "webhook"})
    assert len(logs.latest()) == 1


# --- MongoRequestLogRepository with a fake pymongo client ---


class _FakeCursor:
    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def sort(self, keys: list[tuple[str, int]]) -> "_FakeCursor":
        for key, direction in reversed(keys):
            self.items = sorted(self.items, key=lambda item: item[key], reverse=direction < 0)
        return self

    def limit(self, count: int) -> "_FakeCursor":
        self.items = self.items[:count]
        return self

    def __iter__(self):
        return iter(self.items)


class _FakeLogCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.indexes: list[tuple] = []
        self.fail_inserts = False

    def create_index(self, keys, **kwargs) -> str:
        self.indexes.append((keys, kwargs))
        return "idx"

    def insert_one(self, entry: dict) -> None:
        if self.fail_inserts:
            raise ConnectionError("cluster gone")
        self.docs.append({"_id": len(self.docs), **entry})

    def find(self, query: dict, projection: dict) -> _FakeCursor:
        matched = [doc for doc in self.docs if all(doc.get(k) == v for k, v in query.items())]
        return _FakeCursor(matched)


class _FakeLogClient:
    def __init__(self, uri, **kwargs) -> None:
        self.collection = _FakeLogCollection()
        self.kwargs = kwargs

    def __getitem__(self, name):
        return {"request_logs": self.collection}

    def close(self) -> None:
        pass


def test_mongo_request_logs_with_fake_client(monkeypatch) -> None:
    import pymongo

    monkeypatch.setattr(pymongo, "MongoClient", _FakeLogClient)
    logs = MongoRequestLogRepository("mongodb://example.invalid", "db", "request_logs", retention_days=7)
    collection = logs._collection

    ttl_indexes = [kwargs for keys, kwargs in collection.indexes if keys == "created_at"]
    assert ttl_indexes == [{"expireAfterSeconds": 7 * 86400}]

    for index in range(4):
        logs.add({"type": "webhook" if index % 2 == 0 else "upload", "n": index})
    for doc in collection.docs:
        assert "created_at" in doc and "timestamp" in doc

    # Fake find ignores projection; real Mongo excludes created_at. Order is oldest -> newest.
    assert [entry["n"] for entry in logs.latest(limit=3)] == [1, 2, 3]
    assert [entry["n"] for entry in logs.latest_by_type("webhook")] == [0, 2]
    assert logs.latest(limit=0) == []

    collection.fail_inserts = True
    logs.add({"type": "webhook"})  # must not raise


def test_mongo_request_logs_read_failure_returns_empty(monkeypatch) -> None:
    import pymongo

    monkeypatch.setattr(pymongo, "MongoClient", _FakeLogClient)
    logs = MongoRequestLogRepository("mongodb://example.invalid", "db", "request_logs")
    logs._collection.find = lambda *args, **kwargs: (_ for _ in ()).throw(ConnectionError("down"))  # type: ignore[method-assign]
    assert logs.latest() == []
    assert logs.latest_by_type("webhook") == []
