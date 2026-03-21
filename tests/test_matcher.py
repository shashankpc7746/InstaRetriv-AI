from datetime import datetime, timedelta, timezone

from app.schemas import DocumentMetadata
from app.services.matcher import find_best_document


def build_doc(doc_id: str, category: str, tags: list[str], minutes_ago: int) -> DocumentMetadata:
    return DocumentMetadata(
        id=doc_id,
        file_name=f"{doc_id}.pdf",
        file_type="pdf",
        doc_category=category,
        tags=tags,
        storage_path=f"uploads/{doc_id}.pdf",
        uploaded_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


def test_matcher_handles_synonyms() -> None:
    docs = [build_doc("doc-1", "resume", ["resume", "latest"], 10)]
    result = find_best_document("Give CV", docs)
    assert result.found is True
    assert result.document is not None
    assert result.document.id == "doc-1"


def test_matcher_picks_latest_on_score_tie() -> None:
    older = build_doc("old", "certificate", ["certificate"], 30)
    newer = build_doc("new", "certificate", ["certificate"], 1)
    result = find_best_document("send certificate", [older, newer])
    assert result.found is True
    assert result.document is not None
    assert result.document.id == "new"
