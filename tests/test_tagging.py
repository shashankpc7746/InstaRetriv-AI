import pytest

from app.services.tagging import (
    GENERIC_CATEGORIES,
    choose_final_category,
    derive_final_tags,
    extract_filename_tags,
    suggest_doc_category,
)


# ---------------------------------------------------------------- extract_filename_tags


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("pan_card_2024_final.pdf", ["pan", "card", "2024"]),
        ("My.Resume.v2.pdf", ["resume"]),
        ("send_the_pan_card.pdf", ["pan", "card"]),
        ("John_and_me_passport.pdf", ["john", "passport"]),
        ("copy (2) of offer-letter.pdf", ["offer", "letter"]),
        ("resume_cv_latest.pdf", ["resume", "cv"]),
        ("IMG_20240115_123045.jpg", []),
        ("scan_v3_001_updated_version.pdf", []),
        ("Résumé.pdf", ["resume"]),
        ("आधार_कार्ड.pdf", ["आधार", "कार्ड"]),
        ("voter id.png", ["voter", "id"]),
        ("my_cv.pdf", ["cv"]),
        ("Resume Resume RESUME.pdf", ["resume"]),
        ("", []),
        (".pdf", []),
        ("C:\\Users\\me\\Documents\\pan_card.pdf", ["pan", "card"]),
        ("folder/sub/passport.pdf", ["passport"]),
    ],
)
def test_extract_filename_tags(filename: str, expected: list[str]) -> None:
    assert extract_filename_tags(filename) == expected


def test_extract_filename_tags_keeps_four_digit_years_only() -> None:
    assert extract_filename_tags("marksheet_2019_12_99999.pdf") == ["marksheet", "2019"]


def test_extract_filename_tags_never_returns_stopwords_or_single_chars() -> None:
    tags = extract_filename_tags("a_b_my_the_and_of_for_final_new_copy_scan_img_doc_file_x.pdf")
    assert tags == []


# ---------------------------------------------------------------- derive_final_tags


def test_derive_merges_manual_filename_and_category_without_duplicates() -> None:
    tags = derive_final_tags(["important"], "pan_card_2024_final.pdf", "id", "pdf")
    assert tags == ["important", "pan", "card", "2024", "id"]


def test_derive_does_not_duplicate_manual_tags() -> None:
    tags = derive_final_tags(["resume", "cv"], "resume_cv_latest.pdf", "resume", "pdf")
    assert tags.count("resume") == 1
    assert tags.count("cv") == 1


def test_derive_keeps_multi_word_manual_tag_and_skips_covered_filename_words() -> None:
    assert derive_final_tags(["Pan  Card"], "pan_card.pdf", "general", "pdf") == ["pan card"]


def test_derive_skips_generic_category_and_falls_back_to_extension() -> None:
    assert derive_final_tags([], "IMG_1234.jpg", "general", "jpg") == ["jpg"]
    assert derive_final_tags([" ", ""], "IMG_1234.jpg", "", ".jpg") == ["jpg"]


@pytest.mark.parametrize("category", sorted(GENERIC_CATEGORIES))
def test_derive_never_adds_generic_category(category: str) -> None:
    assert derive_final_tags(["passport"], "x.pdf", category, "pdf") == ["passport"]


def test_derive_cleans_tag_punctuation_and_case() -> None:
    tags = derive_final_tags(["  #Resume! ", "Offer-Letter"], "x.pdf", "general", "pdf")
    assert tags == ["resume", "offer-letter"]


def test_derive_keeps_explicit_id_category() -> None:
    assert "id" in derive_final_tags(["pan"], "x.pdf", "id", "pdf")


# ---------------------------------------------------------------- suggest_doc_category


@pytest.mark.parametrize(
    "tags, expected",
    [
        (["resume"], "resume"),
        (["cv"], "resume"),
        (["biodata"], "resume"),
        (["aadhar"], "id"),
        (["pan card"], "id"),
        (["passport"], "id"),
        (["ssc", "marksheet"], "certificate"),
        (["10th"], "certificate"),
        (["bank statement"], "bank"),
        (["salary slip"], "salary"),
        (["payslip"], "salary"),
        (["offer letter"], "offer-letter"),
        (["cover letter"], "cover-letter"),
        (["invoice"], "invoice"),
    ],
)
def test_suggest_doc_category(tags: list[str], expected: str) -> None:
    category, confidence = suggest_doc_category(tags)
    assert category == expected
    assert confidence >= 0.5


def test_suggest_returns_none_for_unknown_or_empty_tags() -> None:
    assert suggest_doc_category([]) == (None, 0.0)
    assert suggest_doc_category(["important", "2024"]) == (None, 0.0)


def test_suggest_returns_none_when_ambiguous() -> None:
    assert suggest_doc_category(["resume", "passport"]) == (None, 0.0)
    assert suggest_doc_category(["letter"]) == (None, 0.0)


def test_weak_keyword_alone_is_below_threshold() -> None:
    category, confidence = suggest_doc_category(["card"])
    assert category == "id"
    assert confidence < 0.5


def test_more_keywords_raise_confidence() -> None:
    _, one = suggest_doc_category(["passport"])
    _, two = suggest_doc_category(["passport", "aadhaar"])
    assert 0.5 <= one < two <= 1.0


# ---------------------------------------------------------------- choose_final_category


def test_choose_overrides_generic_input_with_confident_suggestion() -> None:
    assert choose_final_category("other", ["important", "resume", "cv", "2026"]) == ("resume", "resume", 0.5)
    assert choose_final_category("", ["passport"])[0] == "id"
    assert choose_final_category("  General ", ["passport"])[0] == "id"


def test_choose_keeps_specific_input_category() -> None:
    final, suggested, confidence = choose_final_category("invoice", ["resume", "profile"])
    assert final == "invoice"
    assert suggested == "resume"
    assert confidence > 0


def test_choose_keeps_explicit_id_category() -> None:
    # "id" used to be treated as generic and silently overwritten.
    assert choose_final_category("id", ["resume"])[0] == "id"
    assert choose_final_category("ID", ["salary"])[0] == "id"


def test_choose_falls_back_to_general_without_confident_suggestion() -> None:
    assert choose_final_category("", ["important"]) == ("general", None, 0.0)
    assert choose_final_category("", ["card"])[0] == "general"
    assert choose_final_category("misc", ["card"])[0] == "misc"


def test_choose_normalizes_case_and_whitespace() -> None:
    assert choose_final_category("  Cover-Letter ", [])[0] == "cover-letter"


def test_generic_categories_contract() -> None:
    assert "id" not in GENERIC_CATEGORIES
    assert {"", "general", "misc", "other", "document", "documents", "file", "files"} == set(GENERIC_CATEGORIES)
