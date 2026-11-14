from datetime import datetime, timedelta, timezone

import pytest

from app.schemas import DocumentMetadata
from app.services.matcher import (
    MIN_MATCH_SCORE,
    STOPWORDS,
    SYNONYM_MAP,
    find_best_document,
    normalize_text,
    normalized_tokens,
    score_document,
)
from app.services.tagging import choose_final_category, derive_final_tags


_BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def build_doc(
    doc_id: str,
    category: str,
    tags: list[str],
    minutes_ago: int,
    file_name: str | None = None,
    is_private: bool = False,
) -> DocumentMetadata:
    return DocumentMetadata(
        id=doc_id,
        file_name=file_name or f"{doc_id}.pdf",
        file_type="pdf",
        doc_category=category,
        tags=tags,
        storage_path=f"uploads/{doc_id}.pdf",
        is_private=is_private,
        uploaded_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


def doc(doc_id: str, category: str, tags: list[str], minutes: int = 0, file_name: str | None = None) -> DocumentMetadata:
    return DocumentMetadata(
        id=doc_id,
        file_name=file_name or f"{doc_id}.pdf",
        file_type="pdf",
        doc_category=category,
        tags=tags,
        storage_path=f"uploads/{doc_id}.pdf",
        uploaded_at=_BASE + timedelta(minutes=minutes),
    )


def best_id(query: str, documents: list[DocumentMetadata]) -> str | None:
    result = find_best_document(query, documents)
    if not result.found:
        assert result.document is None and result.score == 0.0
        return None
    assert result.document is not None
    assert result.score > MIN_MATCH_SCORE
    return result.document.id


# ---------------------------------------------------------------- original tests


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


# ---------------------------------------------------------------- normalization


def test_normalize_text_lowercases_and_splits_punctuation() -> None:
    assert normalize_text("Send---MY__resume!!!") == "send my resume"
    assert normalize_text("   ") == ""
    assert normalize_text("") == ""


def test_normalize_text_keeps_non_latin_scripts_and_folds_latin_accents() -> None:
    assert normalize_text("Résumé") == "resume"
    # Devanagari vowel signs (combining marks) must stay inside the word.
    assert normalize_text("आधार कार्ड!") == "आधार कार्ड"
    assert normalize_text("Ünïcödé_Zertifikat") == "unicode zertifikat"


def test_normalized_tokens_drop_stopwords_and_short_tokens() -> None:
    assert normalized_tokens("please send me my resume") == ["resume"]
    assert normalized_tokens("a i y 1 ok me") == []


def test_normalized_tokens_apply_synonyms() -> None:
    assert normalized_tokens("CV biodata aadhar UID cert adhar 10th 12th") == [
        "resume",
        "resume",
        "aadhaar",
        "aadhaar",
        "certificate",
        "aadhaar",
        "ssc",
        "hsc",
    ]


def test_normalized_tokens_handle_dotted_abbreviations_and_plurals() -> None:
    assert normalized_tokens("send my C.V.") == ["resume"]
    assert normalized_tokens("marksheets certificates") == ["marksheet", "certificate"]
    # Not a dotted abbreviation: a file name stays split normally.
    assert normalized_tokens("a.pdf") == []


def test_synonym_targets_are_not_stopwords() -> None:
    for canonical in set(SYNONYM_MAP.values()):
        assert canonical not in STOPWORDS


# ---------------------------------------------------------------- filler / junk queries


@pytest.mark.parametrize(
    "query",
    [
        "",
        "   ",
        "!!!",
        "send my",
        "a",
        "i",
        "y",
        "ok",
        "1",
        "me",
        "send me",
        "please send",
        "send me the file",
        "can you share the document please",
        "hi",
        "yes",
        "provide",
    ],
)
def test_filler_only_queries_match_nothing(query: str) -> None:
    documents = [
        doc("resume", "resume", ["resume", "cv"]),
        doc("aadhaar", "id", ["aadhaar", "card"], minutes=5),
        doc("salary", "salary", ["salary slip", "2021"], minutes=10),
        doc("passbook", "bank", ["passbook", "bookings"], minutes=15),
    ]
    assert best_id(query, documents) is None


def test_short_tokens_never_fuzzy_match_inside_tags() -> None:
    documents = [
        doc("salary", "general", ["salary"]),
        doc("aadhaar", "general", ["aadhaar"]),
        doc("year", "general", ["2021"]),
    ]
    for query in ["y", "ha", "n", "1", "no", "0", "me", "id"]:
        assert best_id(query, documents) is None, query


def test_two_letter_tag_matches_exactly() -> None:
    documents = [doc("idcard", "general", ["id"]), doc("resume", "resume", ["resume"])]
    assert best_id("send my id", documents) == "idcard"


def test_unrelated_query_not_found() -> None:
    assert best_id("zzz qqq", [doc("a", "resume", ["resume"])]) is None


def test_no_documents_returns_not_found() -> None:
    result = find_best_document("resume", [])
    assert result.found is False and result.document is None and result.score == 0.0


# ---------------------------------------------------------------- typos


@pytest.mark.parametrize(
    "query, expected",
    [
        ("pasport", "passport"),
        ("send my pasport", "passport"),
        ("certficate", "certificate"),
        ("resme", "resume"),
        ("send resme", "resume"),
        ("bank statment", "bank"),
        ("aadhaar", "aadhaar"),
    ],
)
def test_typos_still_match(query: str, expected: str) -> None:
    documents = [
        doc("passport", "id", ["passport"]),
        doc("certificate", "general", ["certificate"], minutes=1),
        doc("resume", "resume", ["resume"], minutes=2),
        doc("bank", "bank", ["bank statement"], minutes=3),
        doc("aadhaar", "id", ["aadhar"], minutes=4),
    ]
    assert best_id(query, documents) == expected


def test_digits_do_not_fuzzy_match() -> None:
    assert best_id("2023", [doc("a", "general", ["2024"])]) is None
    assert best_id("2024", [doc("a", "general", ["2024"])]) == "a"


# ---------------------------------------------------------------- synonyms, both directions


@pytest.mark.parametrize("query", ["resume", "send my resume", "cv", "send my cv", "biodata", "C.V."])
def test_doc_tagged_cv_is_found_by_any_resume_word(query: str) -> None:
    assert best_id(query, [doc("cv-doc", "general", ["cv"])]) == "cv-doc"


@pytest.mark.parametrize("query", ["cv", "resume", "biodata"])
def test_doc_tagged_resume_is_found_by_cv(query: str) -> None:
    assert best_id(query, [doc("resume-doc", "general", ["resume"])]) == "resume-doc"


@pytest.mark.parametrize("tag", ["aadhar", "aadhaar", "uid", "adhar"])
@pytest.mark.parametrize("query", ["aadhar", "aadhaar", "uid", "send my aadhar card", "adhaar"])
def test_aadhaar_spellings_are_interchangeable(tag: str, query: str) -> None:
    documents = [doc("aadhaar", "general", [tag]), doc("other", "general", ["passport"], minutes=99)]
    assert best_id(query, documents) == "aadhaar"


@pytest.mark.parametrize(
    "query, tags",
    [
        ("my aadhar please", ["aadhaar"]),
        ("uid", ["aadhaar"]),
        ("biodata", ["resume"]),
        ("cert", ["certificate"]),
    ],
)
def test_synonyms_map_query_to_canonical_tag(query: str, tags: list[str]) -> None:
    assert find_best_document(query, [doc("a", "general", tags)]).found is True


# ---------------------------------------------------------------- multi-word tags / phrases


@pytest.mark.parametrize("query", ["pan card", "pan", "send my pan card", "PAN-CARD", "pancard"])
def test_multi_word_tag_is_found_by_phrase_and_by_word(query: str) -> None:
    documents = [
        doc("pan", "general", ["pan card"]),
        doc("aadhaar", "general", ["aadhaar card"], minutes=10),
    ]
    assert best_id(query, documents) == "pan"


def test_full_phrase_scores_higher_than_single_word() -> None:
    d = doc("pan", "general", ["pan card"])
    assert score_document("pan card", d) > score_document("pan", d) > 0


def test_ssc_certificate_finds_doc_tagged_ssc_and_certificate() -> None:
    documents = [
        doc("ssc", "general", ["ssc", "certificate"]),
        doc("degree", "certificate", ["degree", "certificate"], minutes=30),
        doc("aadhaar", "id", ["aadhaar"], minutes=60),
    ]
    assert best_id("ssc certificate", documents) == "ssc"


def test_joined_words_match_compound_tag() -> None:
    documents = [doc("marks", "general", ["marksheet"]), doc("resume", "resume", ["resume"], minutes=5)]
    assert best_id("mark sheet", documents) == "marks"


# ---------------------------------------------------------------- user's real queries


@pytest.mark.parametrize(
    "query, expected",
    [
        ("send my resume", "resume"),
        ("pan card", "pan"),
        ("ssc certificate", "ssc"),
        ("aadhar certificate", "aadhaar"),
    ],
)
def test_users_real_queries(query: str, expected: str) -> None:
    documents = [
        doc("resume", "resume", ["resume", "cv"], minutes=0),
        doc("pan", "id", ["pan", "identity"], minutes=1),
        doc("ssc", "certificate", ["ssc", "certificate"], minutes=2),
        doc("aadhaar", "id", ["aadhar"], minutes=3),
        doc("hsc", "certificate", ["hsc", "certificate"], minutes=4),
    ]
    assert best_id(query, documents) == expected


# ---------------------------------------------------------------- file names


def test_filler_filename_does_not_steal_unrelated_query() -> None:
    my_cv = doc("my-cv", "general", [], minutes=100, file_name="my_cv.pdf")
    aadhaar = doc("aadhaar", "id", ["aadhaar"], minutes=0)
    assert best_id("send my aadhaar", [my_cv, aadhaar]) == "aadhaar"
    assert best_id("send my passport", [my_cv]) is None
    assert best_id("send my cv", [my_cv, aadhaar]) == "my-cv"


def test_legacy_filler_tags_do_not_match_filler_queries() -> None:
    # Tags stored by the old extractor (e.g. from "My.Resume.v2.pdf") must not answer "send my passport".
    legacy = doc("legacy", "resume", ["my", "resume", "v2"], minutes=100, file_name="My.Resume.v2.pdf")
    passport = doc("passport", "id", ["passport"], minutes=0)
    assert best_id("send my passport", [legacy, passport]) == "passport"
    assert best_id("send my visa", [legacy, passport]) is None


def test_file_name_words_are_searchable_with_lower_weight() -> None:
    by_name = doc("by-name", "general", ["important"], file_name="Rental_Agreement_2024.pdf")
    assert best_id("rental agreement", [by_name]) == "by-name"
    assert best_id("agrement", [by_name]) == "by-name"

    by_tag = doc("by-tag", "general", ["agreement"], minutes=-10)
    # A tag hit beats a file-name-only hit even though by_name is newer.
    by_name_newer = doc("by-name", "general", ["important"], minutes=50, file_name="agreement.pdf")
    assert score_document("agreement", by_tag) > score_document("agreement", by_name_newer)
    assert best_id("agreement", [by_name_newer, by_tag]) == "by-tag"


def test_file_name_version_noise_is_not_searchable() -> None:
    d = doc("a", "general", ["important"], file_name="scan_v2_001.pdf")
    assert best_id("v2", [d]) is None
    assert best_id("001", [d]) is None


# ---------------------------------------------------------------- unicode


def test_unicode_tags_and_queries_match() -> None:
    documents = [
        doc("aadhaar-hi", "general", ["आधार कार्ड"]),
        doc("resume", "resume", ["résumé"], minutes=5),
        doc("zeugnis", "general", ["Zeugnis"], minutes=10, file_name="Prüfungs_Zeugnis.pdf"),
    ]
    assert best_id("मेरा आधार", documents) == "aadhaar-hi"
    assert best_id("resume", documents) == "resume"
    assert best_id("Résumé please", documents) == "resume"
    assert best_id("zeugnis", documents) == "zeugnis"
    assert best_id("prüfungs", documents) == "zeugnis"


def test_unicode_filename_tokens_match() -> None:
    d = doc("hi", "general", [], file_name="पासपोर्ट_2023.pdf")
    assert best_id("पासपोर्ट", [d]) == "hi"


# ---------------------------------------------------------------- scoring rules


def test_category_boost_breaks_tag_tie() -> None:
    plain = doc("plain", "general", ["passport"], minutes=100)
    categorized = doc("categorized", "passport", ["passport"], minutes=0)
    assert score_document("passport", plain) < score_document("passport", categorized)
    assert best_id("passport", [plain, categorized]) == "categorized"


def test_category_alone_is_matchable() -> None:
    assert best_id("invoice", [doc("a", "invoice", ["important"])]) == "a"


def test_generic_category_is_not_matchable() -> None:
    assert best_id("general", [doc("a", "general", ["important"])]) is None


def test_distinctive_word_beats_generic_word() -> None:
    aadhaar = doc("aadhaar", "id", ["aadhaar"], minutes=0)
    degree = doc("degree", "general", ["degree certificate"], minutes=100)
    assert best_id("aadhar certificate", [aadhaar, degree]) == "aadhaar"


def test_higher_score_beats_newer_document() -> None:
    strong_old = doc("old", "resume", ["resume"], minutes=0)
    weak_new = doc("new", "general", [], minutes=100, file_name="resume_draft.pdf")
    assert best_id("resume", [strong_old, weak_new]) == "old"


def test_tie_picks_newest_regardless_of_list_order() -> None:
    newest = doc("newest", "certificate", ["certificate"], minutes=50)
    older = doc("older", "certificate", ["certificate"], minutes=10)
    assert best_id("certificate", [newest, older]) == "newest"
    assert best_id("certificate", [older, newest]) == "newest"


def test_tie_with_identical_timestamps_keeps_first() -> None:
    first = doc("first", "certificate", ["certificate"], minutes=5)
    second = doc("second", "certificate", ["certificate"], minutes=5)
    assert best_id("certificate", [first, second]) == "first"


def test_document_tags_are_normalized_for_comparison() -> None:
    assert best_id("passport", [doc("a", "general", ["  Passport "])]) == "a"


def test_empty_category_and_tags_do_not_crash() -> None:
    d = doc("a", "", [], file_name="")
    assert score_document("anything", d) == 0.0
    assert best_id("anything", [d]) is None


def test_repeated_query_word_counts_once() -> None:
    d = doc("a", "general", ["passport"])
    assert score_document("passport passport passport", d) == score_document("passport", d)


def test_result_score_is_reported() -> None:
    result = find_best_document("resume", [doc("a", "resume", ["resume"])])
    assert result.found is True and result.score > MIN_MATCH_SCORE


# ---------------------------------------------------------------- realistic corpus


def _upload(
    doc_id: str, file_name: str, category: str, tags: str, minutes: int, is_private: bool = False
) -> DocumentMetadata:
    """Build metadata exactly the way /upload derives it (tagging helpers)."""
    manual = [tag.strip().lower() for tag in tags.split(",") if tag.strip()]
    extension = file_name.rsplit(".", 1)[-1].lower()
    final_tags = derive_final_tags(manual, file_name, category or "general", extension)
    final_category, _, _ = choose_final_category(category, final_tags)
    return DocumentMetadata(
        id=doc_id,
        file_name=file_name,
        file_type=extension,
        doc_category=final_category,
        tags=final_tags,
        storage_path=f"uploads/{doc_id}.{extension}",
        is_private=is_private,
        uploaded_at=_BASE + timedelta(minutes=minutes),
    )


@pytest.fixture(scope="module")
def corpus() -> list[DocumentMetadata]:
    return [
        _upload("resume", "Shashank_Resume_2024_final.pdf", "", "resume, cv", 0),
        _upload("pan", "pan_card.pdf", "id", "pan card", 1, is_private=True),
        _upload("aadhaar", "Aadhar_Card_Scan.jpg", "", "aadhar", 2, is_private=True),
        _upload("passport", "passport_front_back.pdf", "", "", 3, is_private=True),
        _upload("ssc", "SSC_Marksheet.pdf", "", "ssc, 10th, marksheet", 4),
        _upload("hsc", "HSC-marksheet-2019.pdf", "", "hsc, 12th, marksheet", 5),
        _upload("bank", "bank_statement_march.pdf", "", "bank statement", 6),
        _upload("salary", "salary_slip_jan.pdf", "", "salary slip", 7),
        _upload("offer", "Offer_Letter_TCS.pdf", "", "offer letter", 8),
    ]


def test_corpus_categories_are_sensible(corpus: list[DocumentMetadata]) -> None:
    categories = {d.id: d.doc_category for d in corpus}
    assert categories["resume"] == "resume"
    assert categories["pan"] == "id"
    assert categories["aadhaar"] == "id"
    assert categories["passport"] == "id"
    assert categories["ssc"] == "certificate"
    assert categories["bank"] == "bank"
    assert categories["salary"] == "salary"
    assert categories["offer"] == "offer-letter"


@pytest.mark.parametrize(
    "query, expected",
    [
        ("send my resume", "resume"),
        ("resume", "resume"),
        ("cv", "resume"),
        ("Please send my CV", "resume"),
        ("biodata", "resume"),
        ("resme", "resume"),
        ("pan card", "pan"),
        ("send pan", "pan"),
        ("pancard", "pan"),
        ("aadhar", "aadhaar"),
        ("aadhaar card", "aadhaar"),
        ("aadhar certificate", "aadhaar"),
        ("uid", "aadhaar"),
        ("passport", "passport"),
        ("pasport please", "passport"),
        ("ssc certificate", "ssc"),
        ("10th marksheet", "ssc"),
        ("ssc marksheet", "ssc"),
        ("hsc marksheet", "hsc"),
        ("12th marks", "hsc"),
        ("bank statement", "bank"),
        ("send me the bank statment", "bank"),
        ("passbook", None),
        ("salary slip", "salary"),
        ("payslip", "salary"),
        ("offer letter", "offer"),
        ("send me the offer letter please", "offer"),
        ("tcs offer", "offer"),
        ("statement", "bank"),
    ],
)
def test_corpus_natural_queries(corpus: list[DocumentMetadata], query: str, expected: str | None) -> None:
    assert best_id(query, corpus) == expected


@pytest.mark.parametrize(
    "query",
    [
        "weather today",
        "hello",
        "thanks",
        "hi there",
        "how are you",
        "good morning",
        "what is the time",
        "book a cab",
        "call me later",
        "ok thanks",
        "send",
        "lol",
    ],
)
def test_corpus_unrelated_queries_match_nothing(corpus: list[DocumentMetadata], query: str) -> None:
    assert best_id(query, corpus) is None
