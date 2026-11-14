"""Tag and category derivation for uploaded documents."""

import logging
import re

from app.services.matcher import STOPWORDS, normalize_text, normalized_tokens


logger = logging.getLogger("instaretriv.tagging")


# Categories that say nothing about the document. "id" is deliberately NOT generic:
# an explicit "id" category chosen by the owner is kept.
GENERIC_CATEGORIES: frozenset[str] = frozenset(
    {"", "general", "misc", "other", "document", "documents", "file", "files"}
)

# Extra file-name noise on top of the matcher's conversational STOPWORDS.
_FILENAME_NOISE: frozenset[str] = frozenset(
    {
        "final", "new", "copy", "scan", "scanned", "img", "image", "images", "document",
        "doc", "file", "latest", "updated", "update", "version", "ver", "rev", "draft",
        "edited", "compressed", "signed", "screenshot", "whatsapp", "dsc", "camscanner",
        "pdf", "jpg", "jpeg", "png", "webp", "docx", "untitled",
    }
)

# Version / duplicate markers and camera / timestamp fragments: v2, ver3, rev1, copy2, img001, dsc0045.
_FILENAME_NOISE_PATTERN = re.compile(
    r"(v|ver|rev|version|copy|img|image|scan|dsc|pxl|vid)\d+|\d{1,3}|\d{5,}"
)

# Camera / export prefixes whose following number is a counter, not a year ("IMG_1234").
_COUNTER_PREFIXES: frozenset[str] = frozenset(
    {"img", "image", "dsc", "dscn", "pxl", "vid", "scan", "screenshot", "whatsapp", "copy"}
)

MAX_TAG_LENGTH = 40
CATEGORY_CONFIDENCE_THRESHOLD = 0.5

# category -> (strong keywords, weak keywords). Keywords are canonical matcher tokens
# (synonyms already applied, e.g. "cv" -> "resume", "aadhar" -> "aadhaar", "10th" -> "ssc").
_STRONG_POINTS = 1.0
_WEAK_POINTS = 0.5
_CATEGORY_KEYWORDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "resume": (frozenset({"resume", "curriculum", "vitae"}), frozenset({"profile"})),
    "cover-letter": (frozenset({"cover", "motivation"}), frozenset({"letter"})),
    "offer-letter": (frozenset({"offer", "appointment", "joining"}), frozenset({"letter"})),
    "certificate": (
        frozenset({"certificate", "diploma", "transcript", "marksheet", "ssc", "hsc", "degree"}),
        frozenset({"mark", "result"}),
    ),
    "id": (
        frozenset({"aadhaar", "pan", "passport", "license", "voter"}),
        frozenset({"id", "card", "driving"}),
    ),
    "invoice": (frozenset({"invoice", "receipt", "gst"}), frozenset({"bill", "payment"})),
    "bank": (frozenset({"bank", "passbook", "ifsc"}), frozenset({"statement", "account"})),
    "salary": (frozenset({"salary"}), frozenset({"slip", "ctc"})),
}


def _clean_tag(value: str) -> str:
    """Lowercase, collapse whitespace and trim surrounding punctuation; keeps inner words intact."""
    collapsed = re.sub(r"\s+", " ", (value or "").strip().lower())
    return collapsed.strip(" \t.,;:!?\"'`()[]{}<>#*-_/\\|")[:MAX_TAG_LENGTH].strip()


def _filename_stem(filename: str) -> str:
    name = re.split(r"[\\/]", filename or "")[-1]
    stem, dot, extension = name.rpartition(".")
    if dot and stem and 1 <= len(extension) <= 5:
        return stem
    return name


def _is_filename_noise(token: str) -> bool:
    return (
        len(token) < 2
        or token in STOPWORDS
        or token in _FILENAME_NOISE
        or _FILENAME_NOISE_PATTERN.fullmatch(token) is not None
    )


def extract_filename_tags(filename: str) -> list[str]:
    """Meaningful words of a file name: 'My_PAN-card 2024 (final) v2.pdf' -> ['pan', 'card', '2024']."""
    extracted: list[str] = []
    previous = ""
    for token in normalize_text(_filename_stem(filename)).split():
        is_counter = token.isdigit() and previous in _COUNTER_PREFIXES
        previous = token
        if is_counter or _is_filename_noise(token) or token in extracted:
            continue
        extracted.append(token)
    return extracted


def derive_final_tags(manual_tags: list[str], filename: str, category: str, extension: str) -> list[str]:
    """Manual tags first, then file-name words, then a non-generic category; extension only as a last resort."""
    merged: list[str] = []
    seen: set[str] = set()
    covered_words: set[str] = set()

    def add_tag(value: str) -> None:
        tag = _clean_tag(value)
        if not tag or tag in seen:
            return
        seen.add(tag)
        merged.append(tag)
        covered_words.update(normalize_text(tag).split())

    for tag in manual_tags:
        add_tag(tag)

    for token in extract_filename_tags(filename):
        # "pan card" typed manually already covers file-name words "pan" and "card".
        if token not in covered_words:
            add_tag(token)

    normalized_category = _clean_tag(category)
    if normalized_category not in GENERIC_CATEGORIES:
        add_tag(normalized_category)

    if not merged:
        add_tag((extension or "").lstrip("."))

    return merged


def suggest_doc_category(tags: list[str]) -> tuple[str | None, float]:
    """Best category for the tags and a confidence in [0, 1]; (None, 0.0) when unknown or ambiguous.

    A strong keyword (e.g. 'passport') scores 1 point, a weak one (e.g. 'card') 0.5;
    confidence = points / 2, so one strong keyword reaches the 0.5 override threshold.
    """
    words: set[str] = set()
    for tag in tags:
        words.update(normalized_tokens(tag))
    if not words:
        return None, 0.0

    scored: list[tuple[float, str]] = []
    for category, (strong, weak) in _CATEGORY_KEYWORDS.items():
        points = _STRONG_POINTS * len(words & strong) + _WEAK_POINTS * len(words & weak)
        if points > 0:
            scored.append((points, category))
    if not scored:
        return None, 0.0

    scored.sort(key=lambda item: item[0], reverse=True)
    best_points, best_category = scored[0]
    if len(scored) > 1 and scored[1][0] == best_points:
        # e.g. tags 'resume' + 'passport': no reliable single category.
        return None, 0.0

    return best_category, round(min(1.0, best_points / 2.0), 2)


def choose_final_category(input_category: str, tags: list[str]) -> tuple[str, str | None, float]:
    """(final_category, suggested_category, confidence).

    A specific category typed by the owner (including "id") is always kept; the suggestion only
    replaces an empty/generic one when its confidence is at least CATEGORY_CONFIDENCE_THRESHOLD.
    """
    category = _clean_tag(input_category)
    suggested, confidence = suggest_doc_category(tags)

    if category not in GENERIC_CATEGORIES:
        return category, suggested, confidence
    if suggested and confidence >= CATEGORY_CONFIDENCE_THRESHOLD:
        return suggested, suggested, confidence
    return category or "general", suggested, confidence
