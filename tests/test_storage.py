import asyncio
import io
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from fastapi import UploadFile

from app.services import storage as storage_module
from app.services.storage import (
    CloudinaryStorageService,
    LocalStorageService,
    StorageUnavailableError,
    StoredFileNotFoundError,
    UploadRejectedError,
    format_cloudinary_path,
    guess_content_type,
    is_remote_storage_path,
    parse_cloudinary_path,
    sanitize_suffix,
    stream_url,
)


def _upload(data: bytes, filename: str | None = "file.pdf") -> UploadFile:
    return UploadFile(file=io.BytesIO(data), filename=filename)


def _run(coro):
    return asyncio.run(coro)


def _cloudinary() -> CloudinaryStorageService:
    return CloudinaryStorageService("demo-cloud", "123456", "fake-secret", max_bytes=1024)


# --- helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("https://res.cloudinary.com/x/raw/upload/a.pdf", True),
        ("HTTP://example.com/a", True),
        ("cloudinary://raw/authenticated/instaretriv/a.pdf", True),
        ("uploads/a.pdf", False),
        ("C:\\uploads\\a.pdf", False),
        ("", False),
    ],
)
def test_is_remote_storage_path(path: str, expected: bool) -> None:
    assert is_remote_storage_path(path) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("pdf", "application/pdf"),
        (".PDF", "application/pdf"),
        ("resume.pdf", "application/pdf"),
        ("photo.JPG", "image/jpeg"),
        ("jpeg", "image/jpeg"),
        ("png", "image/png"),
        ("x.webp", "image/webp"),
        ("doc", "application/msword"),
        ("cv.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("archive.zip", "application/octet-stream"),
        ("", "application/octet-stream"),
    ],
)
def test_guess_content_type(value: str, expected: str) -> None:
    assert guess_content_type(value) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("../../evil.PDF", ".pdf"),
        ("..\\..\\evil.Docx", ".docx"),
        ("report.pdf", ".pdf"),
        ("noext", ""),
        ("", ""),
        (None, ""),
        ("weird.p$f", ""),
        ("x.pdf/../..", ""),
        ("a.verylongextension", ""),
        ("trailingdot.", ""),
        ("evil.pdf\x00.exe", ".exe"),  # only the real last suffix counts; main's allowlist rejects it
    ],
)
def test_sanitize_suffix(name, expected: str) -> None:
    assert sanitize_suffix(name) == expected


# --- LocalStorageService ---------------------------------------------------------------


def test_local_save_writes_file_with_sanitized_suffix(tmp_path: Path) -> None:
    service = LocalStorageService(str(tmp_path / "uploads"))
    stored = _run(service.save(_upload(b"%PDF-1.4 data", "../../evil.PDF")))

    stored_path = Path(stored)
    assert stored_path.parent == tmp_path / "uploads"
    assert stored_path.suffix == ".pdf"
    assert ".." not in stored_path.name
    assert stored_path.read_bytes() == b"%PDF-1.4 data"
    # no temp files left behind
    assert [p.name for p in (tmp_path / "uploads").iterdir()] == [stored_path.name]


def test_local_save_strips_weird_suffix(tmp_path: Path) -> None:
    service = LocalStorageService(str(tmp_path))
    stored = _run(service.save(_upload(b"data", "file.p$f")))
    assert Path(stored).suffix == ""


def test_local_save_rejects_empty_file(tmp_path: Path) -> None:
    service = LocalStorageService(str(tmp_path))
    with pytest.raises(UploadRejectedError) as exc_info:
        _run(service.save(_upload(b"", "empty.pdf")))
    assert exc_info.value.status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_local_save_rejects_too_large_file(tmp_path: Path) -> None:
    service = LocalStorageService(str(tmp_path), max_bytes=10)
    with pytest.raises(UploadRejectedError) as exc_info:
        _run(service.save(_upload(b"x" * 11, "big.pdf")))
    assert exc_info.value.status_code == 413
    assert "too large" in str(exc_info.value).lower()
    assert list(tmp_path.iterdir()) == []


def test_local_save_accepts_file_exactly_at_limit(tmp_path: Path) -> None:
    service = LocalStorageService(str(tmp_path), max_bytes=10)
    stored = _run(service.save(_upload(b"x" * 10, "ok.pdf")))
    assert Path(stored).read_bytes() == b"x" * 10


def test_local_save_reads_in_chunks_and_stops_early(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(storage_module, "READ_CHUNK_BYTES", 4)
    reads: list[int] = []

    class CountingUpload:
        filename = "big.pdf"

        def __init__(self) -> None:
            self._buffer = io.BytesIO(b"y" * 100)

        async def read(self, size: int = -1) -> bytes:
            reads.append(size)
            return self._buffer.read(size)

    service = LocalStorageService(str(tmp_path), max_bytes=10)
    with pytest.raises(UploadRejectedError):
        _run(service.save(CountingUpload()))  # type: ignore[arg-type]
    assert all(size == 4 for size in reads)
    assert len(reads) == 3  # 4 + 4 + 4 > 10 -> rejected without reading everything


def test_resolve_local_path(tmp_path: Path) -> None:
    service = LocalStorageService(str(tmp_path / "uploads"))
    stored = _run(service.save(_upload(b"abc", "a.pdf")))

    assert service.resolve_local_path(stored) == Path(stored)
    assert service.resolve_local_path(str(tmp_path / "uploads" / "missing.pdf")) is None
    assert service.resolve_local_path("") is None
    assert service.resolve_local_path("https://example.com/a.pdf") is None
    assert service.resolve_local_path("cloudinary://raw/authenticated/instaretriv/a.pdf") is None
    assert service.resolve_local_path(str(tmp_path / "uploads")) is None  # directory, not a file
    assert service.resolve_local_path("bad\x00path") is None


def test_resolve_local_path_falls_back_to_upload_dir_by_name(tmp_path: Path) -> None:
    service = LocalStorageService(str(tmp_path / "uploads"))
    stored = Path(_run(service.save(_upload(b"abc", "a.pdf"))))
    legacy = f"D:\\old\\machine\\uploads\\{stored.name}"
    assert service.resolve_local_path(legacy) == tmp_path / "uploads" / stored.name


def test_local_iter_remote(monkeypatch) -> None:
    service = LocalStorageService.__new__(LocalStorageService)
    calls: list[str] = []
    monkeypatch.setattr(storage_module, "stream_url", lambda url, timeout=20: calls.append(url) or iter([b"x"]))

    assert list(service.iter_remote("https://example.com/a.pdf")) == [b"x"]
    assert calls == ["https://example.com/a.pdf"]
    with pytest.raises(StorageUnavailableError):
        service.iter_remote("cloudinary://raw/authenticated/instaretriv/a.pdf")
    with pytest.raises(StoredFileNotFoundError):
        service.iter_remote("uploads/a.pdf")


# --- stream_url ------------------------------------------------------------------------


def _fake_response(status: int, chunks: list[bytes] | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.iter_content.return_value = iter(chunks or [])
    return response


def test_stream_url_success(monkeypatch) -> None:
    response = _fake_response(200, [b"ab", b"", b"cd"])
    get = MagicMock(return_value=response)
    monkeypatch.setattr(requests, "get", get)

    iterator = stream_url("https://files.example.com/a.pdf", timeout=5)
    get.assert_called_once_with("https://files.example.com/a.pdf", stream=True, timeout=5)
    assert list(iterator) == [b"ab", b"cd"]
    response.close.assert_called()


@pytest.mark.parametrize("status", [404, 410])
def test_stream_url_not_found_is_eager(monkeypatch, status: int) -> None:
    response = _fake_response(status)
    monkeypatch.setattr(requests, "get", MagicMock(return_value=response))
    with pytest.raises(StoredFileNotFoundError):
        stream_url("https://files.example.com/a.pdf")
    response.close.assert_called()


@pytest.mark.parametrize("status", [401, 500, 503])
def test_stream_url_server_errors_are_unavailable(monkeypatch, status: int) -> None:
    response = _fake_response(status)
    monkeypatch.setattr(requests, "get", MagicMock(return_value=response))
    with pytest.raises(StorageUnavailableError):
        stream_url("https://files.example.com/a.pdf")
    response.close.assert_called()


def test_stream_url_connection_error(monkeypatch) -> None:
    monkeypatch.setattr(requests, "get", MagicMock(side_effect=requests.ConnectionError("boom")))
    with pytest.raises(StorageUnavailableError):
        stream_url("https://files.example.com/a.pdf")


def test_stream_url_interrupted_stream(monkeypatch) -> None:
    response = MagicMock()
    response.status_code = 200

    def broken(chunk_size: int):
        yield b"first"
        raise requests.ConnectionError("reset")

    response.iter_content.side_effect = broken
    monkeypatch.setattr(requests, "get", MagicMock(return_value=response))

    iterator = stream_url("https://files.example.com/a.pdf")
    assert next(iterator) == b"first"
    with pytest.raises(StorageUnavailableError):
        next(iterator)
    response.close.assert_called()


# --- cloudinary path helpers -------------------------------------------------------------


def test_cloudinary_path_round_trip_raw() -> None:
    path = format_cloudinary_path("raw", "instaretriv/abc.pdf")
    assert path == "cloudinary://raw/authenticated/instaretriv/abc.pdf"
    assert parse_cloudinary_path(path) == ("raw", "authenticated", "instaretriv/abc.pdf", None)


def test_cloudinary_path_round_trip_image() -> None:
    path = format_cloudinary_path("image", "instaretriv/abc", file_format="png")
    assert path == "cloudinary://image/authenticated/instaretriv/abc?format=png"
    assert parse_cloudinary_path(path) == ("image", "authenticated", "instaretriv/abc", "png")


@pytest.mark.parametrize(
    "bad",
    [
        "https://res.cloudinary.com/x",
        "cloudinary://",
        "cloudinary://raw",
        "cloudinary://raw/authenticated",
        "cloudinary://raw/authenticated/",
        "cloudinary://banana/authenticated/x",
    ],
)
def test_parse_cloudinary_path_rejects_malformed(bad: str) -> None:
    with pytest.raises(StoredFileNotFoundError):
        parse_cloudinary_path(bad)


# --- CloudinaryStorageService ----------------------------------------------------------


def test_cloudinary_requires_credentials() -> None:
    with pytest.raises(StorageUnavailableError):
        CloudinaryStorageService("", "key", "secret")


def test_cloudinary_save_raw_uses_authenticated_type(monkeypatch) -> None:
    import cloudinary.uploader

    upload = MagicMock(side_effect=lambda data, **kw: {"public_id": kw["public_id"], "resource_type": "raw"})
    monkeypatch.setattr(cloudinary.uploader, "upload", upload)

    stored = _run(_cloudinary().save(_upload(b"%PDF data", "My Resume.PDF")))

    upload.assert_called_once()
    args, kwargs = upload.call_args
    assert args[0] == b"%PDF data"
    assert kwargs["type"] == "authenticated"
    assert kwargs["resource_type"] == "raw"
    assert kwargs["public_id"].startswith("instaretriv/")
    assert kwargs["public_id"].endswith(".pdf")
    assert "access_mode" not in kwargs
    assert stored == f"cloudinary://raw/authenticated/{kwargs['public_id']}"
    assert parse_cloudinary_path(stored)[3] is None


def test_cloudinary_save_image(monkeypatch) -> None:
    import cloudinary.uploader

    upload = MagicMock(side_effect=lambda data, **kw: {"public_id": kw["public_id"], "format": "jpg"})
    monkeypatch.setattr(cloudinary.uploader, "upload", upload)

    stored = _run(_cloudinary().save(_upload(b"\xff\xd8 jpeg", "photo.JPG")))

    kwargs = upload.call_args.kwargs
    assert kwargs["resource_type"] == "image"
    assert kwargs["type"] == "authenticated"
    assert "." not in kwargs["public_id"].split("/")[-1]
    assert stored == f"cloudinary://image/authenticated/{kwargs['public_id']}?format=jpg"


def test_cloudinary_save_rejects_empty_and_large_without_uploading(monkeypatch) -> None:
    import cloudinary.uploader

    upload = MagicMock()
    monkeypatch.setattr(cloudinary.uploader, "upload", upload)
    service = _cloudinary()

    with pytest.raises(UploadRejectedError) as empty:
        _run(service.save(_upload(b"", "a.pdf")))
    assert empty.value.status_code == 400

    with pytest.raises(UploadRejectedError) as large:
        _run(service.save(_upload(b"x" * 2048, "a.pdf")))
    assert large.value.status_code == 413
    upload.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [Exception("cloudinary api error"), requests.ConnectionError("down"), None],
)
def test_cloudinary_save_errors_become_storage_unavailable(monkeypatch, failure) -> None:
    import cloudinary.uploader

    if failure is None:
        upload = MagicMock(return_value={})  # unexpected response without public_id
    else:
        upload = MagicMock(side_effect=failure)
    monkeypatch.setattr(cloudinary.uploader, "upload", upload)

    with pytest.raises(StorageUnavailableError):
        _run(_cloudinary().save(_upload(b"data", "a.pdf")))


def test_cloudinary_signed_delivery_url_raw() -> None:
    url = _cloudinary().signed_delivery_url("cloudinary://raw/authenticated/instaretriv/abc.pdf")
    assert url.startswith("https://res.cloudinary.com/demo-cloud/raw/authenticated/s--")
    assert "--/" in url
    assert url.endswith("/instaretriv/abc.pdf")
    assert "fake-secret" not in url


def test_cloudinary_signed_delivery_url_image_uses_format() -> None:
    url = _cloudinary().signed_delivery_url("cloudinary://image/authenticated/instaretriv/abc?format=png")
    assert url.startswith("https://res.cloudinary.com/demo-cloud/image/authenticated/s--")
    assert url.endswith("/instaretriv/abc.png")


def test_cloudinary_iter_remote_streams_signed_url(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(storage_module, "stream_url", lambda url, timeout=20: calls.append(url) or iter([b"pdf"]))
    service = _cloudinary()

    assert list(service.iter_remote("cloudinary://raw/authenticated/instaretriv/abc.pdf")) == [b"pdf"]
    assert "/raw/authenticated/s--" in calls[0]

    assert list(service.iter_remote("https://res.cloudinary.com/demo-cloud/raw/upload/old.pdf")) == [b"pdf"]
    assert calls[1] == "https://res.cloudinary.com/demo-cloud/raw/upload/old.pdf"

    with pytest.raises(StoredFileNotFoundError):
        service.iter_remote("uploads/local.pdf")
    with pytest.raises(StoredFileNotFoundError):
        service.iter_remote("cloudinary://raw/authenticated")


def test_cloudinary_resolve_local_path_supports_legacy_local_files(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.pdf"
    legacy.write_bytes(b"x")
    service = _cloudinary()
    assert service.resolve_local_path(str(legacy)) == legacy
    assert service.resolve_local_path("cloudinary://raw/authenticated/instaretriv/abc.pdf") is None
    assert service.resolve_local_path(str(tmp_path / "missing.pdf")) is None
