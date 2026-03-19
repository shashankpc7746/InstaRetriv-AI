import re

from rapidfuzz import fuzz

from app.schemas import DocumentMetadata, RetrievalResult


SYNONYM_MAP = {
    "cv": "resume",
    "biodata": "resume",
    "aadhar": "aadhaar",
    "uid": "aadhaar",
    "cert": "certificate",
}


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def normalized_tokens(text: str) -> list[str]:
    tokens = normalize_text(text).split()
    return [SYNONYM_MAP.get(token, token) for token in tokens]


def score_document(query: str, document: DocumentMetadata) -> float:
    tokens = normalized_tokens(query)
    tags = [tag.lower().strip() for tag in document.tags]
    category = document.doc_category.lower().strip()
    score = 0.0

    for token in tokens:
        if token in tags:
            score += 2.0
        if token == category:
            score += 2.0

    fuzzy_tag_scores = [fuzz.partial_ratio(token, tag) for token in tokens for tag in tags]
    fuzzy_category_scores = [fuzz.partial_ratio(token, category) for token in tokens] if category else []
    fuzzy_max = max(fuzzy_tag_scores + fuzzy_category_scores + [0])

    if fuzzy_max >= 85:
        score += 1.0

    return score


def find_best_document(query: str, documents: list[DocumentMetadata]) -> RetrievalResult:
    best_document = None
    best_score = 0.0

    for document in documents:
        current_score = score_document(query, document)
        if current_score > best_score:
            best_document = document
            best_score = current_score
        elif current_score == best_score and best_document is not None and current_score > 0:
            if document.uploaded_at > best_document.uploaded_at:
                best_document = document

    if best_document is None or best_score <= 0:
        return RetrievalResult(found=False, score=0.0)

    return RetrievalResult(found=True, document=best_document, score=best_score)
