import pytest

import app.stores as stores
from app.config import Settings
from app.repository import MetadataRepository
from app.request_log_repository import RequestLogRepository
from app.schemas import DocumentMetadata
from app.stores import (
    NullRequestLogRepository,
    ResilientMetadataStore,
    StoreUnavailableError,
    create_metadata_store,
    create_request_log_store,
    describe_error,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _doc(name: str = "resume.pdf") -> DocumentMetadata:
    return DocumentMetadata(
        file_name=name,
        file_type="pdf",
        doc_category="resume",
        tags=["resume"],
        storage_path=f"uploads/{name}",
    )


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "development",
        "metadata_backend": "json",
        "mongodb_uri": "",
        "metadata_file": str(tmp_path / "metadata.json"),
        "request_log_file": str(tmp_path / "request_logs.json"),
        "metadata_store_retry_seconds": 30,
        "mongodb_database": "instaretriv_ai",
        "mongodb_collection": "documents",
        "mongodb_request_log_collection": "request_logs",
        "mongodb_timeout_ms": 5000,
        "request_log_max_entries": 2000,
    }
    values.update(overrides)
    return Settings(**values)


class _FlakyFactory:
    def __init__(self, repository, failures: int) -> None:
        self.repository = repository
        self.failures = failures
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise ConnectionError("cluster unreachable")
        return self.repository


def test_failing_factory_raises_store_unavailable_and_reports_status(tmp_path) -> None:
    factory = _FlakyFactory(MetadataRepository(str(tmp_path / "m.json")), failures=10)
    store = ResilientMetadataStore(factory, backend_name="mongo", retry_seconds=30, clock=FakeClock())

    with pytest.raises(StoreUnavailableError, match="cluster unreachable"):
        store.list_active()
    status = store.status()
    assert status == {"backend": "mongo", "available": False, "error": "cluster unreachable", "fallback": False}


def test_retry_interval_is_respected(tmp_path) -> None:
    clock = FakeClock()
    factory = _FlakyFactory(MetadataRepository(str(tmp_path / "m.json")), failures=10)
    store = ResilientMetadataStore(factory, backend_name="mongo", retry_seconds=30, clock=clock)

    for _ in range(3):
        with pytest.raises(StoreUnavailableError):
            store.list_all()
    store.status()
    assert factory.calls == 1

    clock.now += 29
    with pytest.raises(StoreUnavailableError):
        store.get_by_id("x")
    assert factory.calls == 1

    clock.now += 2
    with pytest.raises(StoreUnavailableError):
        store.get_by_id("x")
    assert factory.calls == 2


def test_retry_interval_with_monkeypatched_time(tmp_path, monkeypatch) -> None:
    current = {"now": 500.0}
    monkeypatch.setattr(stores.time, "monotonic", lambda: current["now"])
    factory = _FlakyFactory(MetadataRepository(str(tmp_path / "m.json")), failures=10)
    store = ResilientMetadataStore(factory, backend_name="mongo", retry_seconds=10)

    with pytest.raises(StoreUnavailableError):
        store.list_all()
    with pytest.raises(StoreUnavailableError):
        store.list_all()
    assert factory.calls == 1
    current["now"] += 10
    with pytest.raises(StoreUnavailableError):
        store.list_all()
    assert factory.calls == 2


def test_recovers_when_factory_starts_succeeding(tmp_path) -> None:
    clock = FakeClock()
    factory = _FlakyFactory(MetadataRepository(str(tmp_path / "m.json")), failures=1)
    store = ResilientMetadataStore(factory, backend_name="mongo", retry_seconds=30, clock=clock)

    with pytest.raises(StoreUnavailableError):
        store.list_all()
    assert store.status()["available"] is False

    clock.now += 31
    document = store.add(_doc())
    assert store.get_by_id(document.id) == document
    assert store.list_active() == [document]
    assert store.deactivate(document.id) is True
    store.ping()
    assert store.status() == {"backend": "mongo", "available": True, "error": None, "fallback": False}


class _BrokenRepository:
    def __init__(self) -> None:
        self.broken = True

    def list_all(self):
        if self.broken:
            raise TimeoutError("socket timed out")
        return []

    def get_by_id(self, document_id):
        raise ValueError("programming error")


def test_connection_error_during_call_marks_unavailable_then_recovers() -> None:
    repository = _BrokenRepository()
    clock = FakeClock()
    store = ResilientMetadataStore(lambda: repository, backend_name="mongo", clock=clock)

    with pytest.raises(StoreUnavailableError, match="socket timed out"):
        store.list_all()
    assert store.status()["available"] is False
    assert store.status()["error"] == "socket timed out"

    repository.broken = False
    clock.now += 31  # past the retry window: the circuit lets a probe call through
    assert store.list_all() == []
    assert store.status()["available"] is True


def test_non_connection_errors_propagate_unchanged() -> None:
    store = ResilientMetadataStore(lambda: _BrokenRepository(), backend_name="mongo", clock=FakeClock())
    with pytest.raises(ValueError):
        store.get_by_id("x")
    assert store.status()["available"] is True


def test_describe_error_redacts_uri_credentials_and_truncates() -> None:
    message = describe_error(RuntimeError("bad uri mongodb+srv://user:p%40ss@cluster0.example.net/db"))
    assert "p%40ss" not in message and "user:" not in message
    assert "mongodb+srv://***@cluster0.example.net" in message
    assert len(describe_error(RuntimeError("x" * 1000))) == 300


# --- create_metadata_store ---


def test_json_backend_store_has_status(tmp_path) -> None:
    store = create_metadata_store(_settings(tmp_path))
    assert store.status() == {"backend": "json", "available": True, "error": None, "fallback": False}
    document = store.add(_doc())
    assert store.list_active() == [document]


def test_mongo_with_empty_uri_in_production_is_unavailable(tmp_path) -> None:
    settings = _settings(tmp_path, app_env="production", metadata_backend="mongo", mongodb_uri="")
    store = create_metadata_store(settings)

    status = store.status()
    assert status == {"backend": "mongo", "available": False, "error": "MONGODB_URI is empty", "fallback": False}
    with pytest.raises(StoreUnavailableError):
        store.list_active()
    assert not (tmp_path / "metadata.json").exists()


def test_mongo_with_empty_uri_in_development_falls_back_to_json(tmp_path) -> None:
    settings = _settings(tmp_path, app_env="development", metadata_backend="mongo", mongodb_uri="  ")
    store = create_metadata_store(settings)

    status = store.status()
    assert status["backend"] == "json"
    assert status["available"] is True
    assert status["fallback"] is True
    assert "MONGODB_URI is empty" in status["error"]
    document = store.add(_doc())
    assert store.get_by_id(document.id) == document


def _failing_mongo(monkeypatch) -> list:
    calls: list = []

    def failing(*args, **kwargs):
        calls.append((args, kwargs))
        raise ConnectionError("ServerSelectionTimeoutError: cluster0.example.net:27017 timed out")

    monkeypatch.setattr(stores, "MongoMetadataRepository", failing)
    return calls


def test_mongo_init_failure_in_production_is_unavailable_without_fallback(tmp_path, monkeypatch) -> None:
    calls = _failing_mongo(monkeypatch)
    settings = _settings(tmp_path, app_env="production", metadata_backend="mongo", mongodb_uri="mongodb://example.invalid")

    store = create_metadata_store(settings)
    assert calls == []  # lazy in production: no network at import time

    status = store.status()
    assert status["backend"] == "mongo"
    assert status["available"] is False
    assert status["fallback"] is False
    assert "timed out" in status["error"]
    with pytest.raises(StoreUnavailableError):
        store.list_active()
    assert not (tmp_path / "metadata.json").exists()
    args, kwargs = calls[0]
    assert args[1:] == ("instaretriv_ai", "documents")
    assert kwargs == {"timeout_ms": 5000}


def test_mongo_init_failure_in_development_falls_back_to_json(tmp_path, monkeypatch, caplog) -> None:
    _failing_mongo(monkeypatch)
    settings = _settings(tmp_path, app_env="development", metadata_backend="mongo", mongodb_uri="mongodb://example.invalid")

    with caplog.at_level("WARNING", logger="instaretriv.stores"):
        store = create_metadata_store(settings)

    status = store.status()
    assert status["backend"] == "json"
    assert status["available"] is True
    assert status["fallback"] is True
    assert "MongoDB unavailable" in status["error"]
    assert any("development only" in record.getMessage() for record in caplog.records)
    document = store.add(_doc())
    assert store.list_all() == [document]


def test_mongo_success_uses_mongo_store(tmp_path, monkeypatch) -> None:
    repository = MetadataRepository(str(tmp_path / "stand-in.json"))
    monkeypatch.setattr(stores, "MongoMetadataRepository", lambda *args, **kwargs: repository)
    settings = _settings(tmp_path, metadata_backend="mongo", mongodb_uri="mongodb://example.invalid")

    store = create_metadata_store(settings)
    assert store.status() == {"backend": "mongo", "available": True, "error": None, "fallback": False}


# --- create_request_log_store ---


def test_request_log_store_json_by_default(tmp_path) -> None:
    logs = create_request_log_store(_settings(tmp_path, request_log_max_entries=5))
    assert isinstance(logs, RequestLogRepository)
    assert logs.backend_name == "json"
    assert logs.max_entries == 5


def test_request_log_store_falls_back_to_json_when_mongo_unreachable(tmp_path, monkeypatch) -> None:
    def failing(*args, **kwargs):
        raise ConnectionError("unreachable")

    monkeypatch.setattr(stores, "MongoRequestLogRepository", failing)
    settings = _settings(tmp_path, app_env="production", metadata_backend="mongo", mongodb_uri="mongodb://example.invalid")
    logs = create_request_log_store(settings)
    assert logs.backend_name == "json"


def test_request_log_store_uses_mongo_when_reachable(tmp_path, monkeypatch) -> None:
    class FakeMongoLogs:
        backend_name = "mongo"

        def __init__(self, *args, **kwargs) -> None:
            self.args = args

    monkeypatch.setattr(stores, "MongoRequestLogRepository", FakeMongoLogs)
    settings = _settings(tmp_path, metadata_backend="mongo", mongodb_uri="mongodb://example.invalid")
    logs = create_request_log_store(settings)
    assert logs.backend_name == "mongo"
    assert logs.args[2] == "request_logs"


def test_request_log_store_never_raises(tmp_path, monkeypatch) -> None:
    def failing(*args, **kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(stores, "RequestLogRepository", failing)
    logs = create_request_log_store(_settings(tmp_path))
    assert isinstance(logs, NullRequestLogRepository)
    logs.add({"type": "webhook"})
    assert logs.latest() == []
