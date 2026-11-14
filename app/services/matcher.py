"""Query -> document matching for WhatsApp retrieval.

Scoring model (per document, per distinct meaningful query token):
- exact hit on a tag word or the category: TAG_WEIGHT
- fuzzy hit (rapidfuzz ``fuzz.ratio`` >= FUZZY_THRESHOLD, both words >= 4 chars, no digits): FUZZY_WEIGHT
- otherwise exact / fuzzy hit on a word of the original file name: FILENAME_WEIGHT / FILENAME_FUZZY_WEIGHT
- token also matches the (non-generic) category: + CATEGORY_BONUS
- "generic" words such as certificate / card / letter count at GENERIC_FACTOR of the above
- every multi-word tag ("pan card") found as a contiguous phrase in the query: + PHRASE_BONUS

Filler words (send, my, please, ...) are removed from BOTH the query and the document
side, synonyms are mapped to one canonical word on both sides, and tokens shorter than
2 characters never match. A document is returned only when its score exceeds
MIN_MATCH_SCORE; ties go to the newest upload.

A document never matches on generic words alone when the query also names something
specific: if the query has distinctive words (not generic, >= DISTINCTIVE_MIN_LENGTH chars)
and none of them hit the document, its score is 0 ("aadhaar card" must not return the PAN card
just because both are cards).
"""

import logging
import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache

from rapidfuzz import fuzz

from app.schemas import DocumentMetadata, RetrievalResult


logger = logging.getLogger("instaretriv.matcher")


# Every key is mapped to its canonical value on both the query and the document side.
SYNONYM_MAP: dict[str, str] = {
    # resume
    "cv": "resume",
    "biodata": "resume",
    "resumes": "resume",
    # aadhaar
    "aadhar": "aadhaar",
    "adhar": "aadhaar",
    "adhaar": "aadhaar",
    "aadhaarcard": "aadhaar",
    "aadharcard": "aadhaar",
    "uid": "aadhaar",
    "uidai": "aadhaar",
    # pan
    "pancard": "pan",
    # certificates / education
    "cert": "certificate",
    "certi": "certificate",
    "certs": "certificate",
    "10th": "ssc",
    "tenth": "ssc",
    "matric": "ssc",
    "12th": "hsc",
    "twelfth": "hsc",
    "marksheets": "marksheet",
    "markslip": "marksheet",
    # identity
    "ids": "id",
    "identity": "id",
    "licence": "license",
    # money
    "payslip": "salary",
    "paystub": "salary",
    "payslips": "salary",
}

# Words that never identify a document: conversational filler, pronouns, request verbs,
# file-format words and generic "document" nouns. Applied to queries AND document words.
STOPWORDS: frozenset[str] = frozenset(
    {
        # request verbs / politeness
        "send", "sent", "sending", "give", "share", "get", "show", "find", "need", "needed",
        "want", "wanted", "require", "required", "provide", "fetch", "attach", "attached",
        "forward", "please", "pls", "plz", "plss", "pleasee", "kindly", "can", "could",
        "would", "will", "shall", "may", "might", "must", "should", "let", "lemme",
        # greetings / acknowledgements
        "hi", "hii", "hello", "hey", "hiya", "ok", "okay", "okk", "thanks", "thank",
        "thx", "ty", "yes", "yeah", "yep", "no", "nope", "sure", "good", "morning",
        "evening", "bro", "sir", "madam", "dear",
        # pronouns / determiners / auxiliaries
        "i", "me", "my", "mine", "myself", "we", "us", "our", "ours", "you", "your",
        "yours", "u", "ur", "he", "she", "his", "her", "him", "they", "them", "their",
        "it", "its", "this", "that", "these", "those", "the", "a", "an", "some", "any",
        "all", "is", "am", "are", "was", "were", "be", "been", "do", "does", "did",
        "have", "has", "had", "here", "there", "what", "which", "where", "who", "whom",
        "how", "now", "just", "also", "again", "only", "one", "same", "other", "another",
        # prepositions / conjunctions
        "of", "for", "to", "and", "or", "with", "without", "on", "in", "at", "from", "by",
        "as", "into", "about", "via", "up",
        # generic file nouns and formats
        "file", "files", "document", "documents", "doc", "docs", "copy", "copies",
        "attachment", "attachments", "pdf", "pdfs", "jpg", "jpeg", "png", "webp", "docx",
        # "which version" words: the newest upload already wins ties
        "latest", "newest", "recent", "new", "final", "updated", "current",
        # Hinglish filler commonly typed on WhatsApp
        "bhej", "bhejo", "bhejna", "bhejdo", "dedo", "dena", "de", "mera", "meri",
        "mere", "mujhe", "muje", "chahiye", "chaiye", "ka", "ki", "ke", "hai", "ha",
        "haan", "na", "kar", "karo", "wala", "wali",
    }
)

# Words that describe a kind of paper rather than which one ("certificate", "card"):
# they still match, but count for less than distinctive words like "pan" or "ssc".
GENERIC_TERMS: frozenset[str] = frozenset(
    {
        "certificate", "card", "letter", "slip", "statement", "sheet", "form", "report",
        "record", "records", "proof", "details", "paper", "papers", "receipt", "list",
    }
)

# Categories that carry no meaning for matching (kept in sync with tagging.GENERIC_CATEGORIES).
_GENERIC_CATEGORY_VALUES: frozenset[str] = frozenset(
    {"", "general", "misc", "other", "document", "documents", "file", "files"}
)

TAG_WEIGHT = 3.0
FUZZY_WEIGHT = 2.0
FILENAME_WEIGHT = 1.5
FILENAME_FUZZY_WEIGHT = 1.0
CATEGORY_BONUS = 1.0
PHRASE_BONUS = 2.0
GENERIC_FACTOR = 0.5

FUZZY_THRESHOLD = 85.0
FUZZY_MIN_LENGTH = 4
MIN_TOKEN_LENGTH = 2
# Query words at least this long that are not GENERIC_TERMS identify which document is meant.
DISTINCTIVE_MIN_LENGTH = 3

# A score must be strictly greater than this to count as a match. The weakest accepted
# hit is a generic word found only in the file name (FILENAME_WEIGHT * GENERIC_FACTOR = 0.75).
MIN_MATCH_SCORE: float = 0.7

_PLURAL_EXEMPT_ENDINGS = ("ss", "us", "is", "ous")


def _fold_latin_accents(text: str) -> str:
    """Drop accents from Latin letters ('résumé' -> 'resume') but keep marks of other scripts."""
    decomposed = unicodedata.normalize("NFKD", text)
    kept: list[str] = []
    previous_base_is_ascii = False
    for char in decomposed:
        if unicodedata.combining(char):
            if previous_base_is_ascii:
                continue
        else:
            previous_base_is_ascii = char.isascii()
        kept.append(char)
    return unicodedata.normalize("NFC", "".join(kept))


def normalize_text(text: str) -> str:
    """Lowercase and keep letters / digits / combining marks of any script; everything else is a space."""
    folded = _fold_latin_accents(unicodedata.normalize("NFKC", text or "")).casefold()
    chars = [
        char if unicodedata.category(char)[0] in {"L", "N", "M"} else " "
        for char in folded
    ]
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def _singularize(token: str) -> str:
    if (
        len(token) >= 5
        and token.isascii()
        and token.isalpha()
        and token.endswith("s")
        and not token.endswith(_PLURAL_EXEMPT_ENDINGS)
    ):
        return token[:-1]
    return token


def _canonical(token: str) -> str:
    if token in SYNONYM_MAP:
        return SYNONYM_MAP[token]
    singular = _singularize(token)
    return SYNONYM_MAP.get(singular, singular)


def _is_meaningful(token: str) -> bool:
    return len(token) >= MIN_TOKEN_LENGTH and token not in STOPWORDS


# Dotted abbreviations such as "C.V." or "U.I.D" -> "CV" / "UID".
_DOTTED_ABBREVIATION = re.compile(r"(?<!\w)(?:[^\W\d_]\.)+[^\W\d_](?!\w)\.?")


def normalized_tokens(text: str) -> list[str]:
    """Meaningful canonical tokens: filler words and 1-char tokens removed, synonyms applied."""
    text = _DOTTED_ABBREVIATION.sub(lambda match: match.group(0).replace(".", ""), text or "")
    tokens: list[str] = []
    for raw in normalize_text(text).split():
        if not _is_meaningful(raw):
            continue
        token = _canonical(raw)
        if _is_meaningful(token):
            tokens.append(token)
    return tokens


def _is_generic_category(category: str) -> bool:
    return normalize_text(category) in _GENERIC_CATEGORY_VALUES


def _file_stem(file_name: str) -> str:
    name = re.split(r"[\\/]", file_name or "")[-1]
    stem, dot, extension = name.rpartition(".")
    if dot and stem and 1 <= len(extension) <= 5:
        return stem
    return name


class _DocumentTerms:
    """Normalized, matchable words of one document."""

    __slots__ = ("tag_words", "category_words", "filename_words", "phrases", "fuzzy_words")

    def __init__(self, tags: tuple[str, ...], category: str, file_name: str) -> None:
        self.tag_words: set[str] = set()
        self.phrases: set[tuple[str, ...]] = set()
        for tag in tags:
            words = normalized_tokens(tag)
            self.tag_words.update(words)
            if len(words) >= 2:
                self.phrases.add(tuple(words))
                # "pan card" is also reachable as the single word "pancard".
                self.tag_words.add(_canonical("".join(words)))

        self.category_words: set[str] = set()
        if not _is_generic_category(category):
            self.category_words.update(normalized_tokens(category))

        self.filename_words: set[str] = {
            word for word in normalized_tokens(_file_stem(file_name)) if not _is_noise_word(word)
        }
        self.fuzzy_words: set[str] = {
            word for word in self.tag_words | self.category_words if _fuzzy_eligible(word)
        }


@lru_cache(maxsize=2048)
def _document_terms(tags: tuple[str, ...], category: str, file_name: str) -> _DocumentTerms:
    return _DocumentTerms(tags, category, file_name)


def _is_noise_word(word: str) -> bool:
    """Version markers and short number runs from file names ('v2', '01')."""
    return bool(re.fullmatch(r"v\d{1,3}|\d{1,3}", word))


def _fuzzy_eligible(token: str) -> bool:
    return len(token) >= FUZZY_MIN_LENGTH and not any(char.isdigit() for char in token)


def _fuzzy_hit(token: str, candidates: set[str]) -> str | None:
    """Closest candidate with ratio >= FUZZY_THRESHOLD (whole-word similarity, never partial)."""
    if not _fuzzy_eligible(token):
        return None
    best_word: str | None = None
    best_ratio = 0.0
    for candidate in sorted(candidates):
        if not _fuzzy_eligible(candidate):
            continue
        ratio = fuzz.ratio(token, candidate)
        if ratio >= FUZZY_THRESHOLD and ratio > best_ratio:
            best_word, best_ratio = candidate, ratio
    return best_word


def _token_score(token: str, terms: _DocumentTerms) -> float:
    matched: str | None
    if token in terms.tag_words or token in terms.category_words:
        matched, score = token, TAG_WEIGHT
    elif (matched := _fuzzy_hit(token, terms.fuzzy_words)) is not None:
        score = FUZZY_WEIGHT
    elif token in terms.filename_words:
        matched, score = token, FILENAME_WEIGHT
    elif (matched := _fuzzy_hit(token, terms.filename_words)) is not None:
        score = FILENAME_FUZZY_WEIGHT
    else:
        return 0.0

    if terms.category_words and (
        token in terms.category_words or _fuzzy_hit(token, terms.category_words) is not None
    ):
        score += CATEGORY_BONUS

    if token in GENERIC_TERMS or matched in GENERIC_TERMS:
        score *= GENERIC_FACTOR
    return score


def _contains_phrase(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    size = len(phrase)
    return any(tuple(tokens[index:index + size]) == phrase for index in range(len(tokens) - size + 1))


def _query_terms(tokens: list[str]) -> list[str]:
    """Distinct tokens, plus joined neighbours ("mark sheet" -> "marksheet")."""
    terms: list[str] = []
    for token in tokens:
        if token not in terms:
            terms.append(token)
    for left, right in zip(tokens, tokens[1:]):
        joined = _canonical(left + right)
        if joined not in terms:
            terms.append(joined)
    return terms


def _is_distinctive(token: str) -> bool:
    return len(token) >= DISTINCTIVE_MIN_LENGTH and token not in GENERIC_TERMS


def _joined_pairs(tokens: list[str]) -> dict[str, tuple[str, str]]:
    return {_canonical(left + right): (left, right) for left, right in zip(tokens, tokens[1:])}


def _score_tokens(tokens: list[str], document: DocumentMetadata) -> float:
    if not tokens:
        return 0.0
    terms = _document_terms(tuple(document.tags), document.doc_category or "", document.file_name or "")

    score = 0.0
    single_tokens = set(tokens)
    joined = _joined_pairs(tokens)
    distinctive = {token for token in single_tokens if _is_distinctive(token)}
    hit: set[str] = set()
    for term in _query_terms(tokens):
        if term in single_tokens:
            term_score = _token_score(term, terms)
            if term_score > 0:
                hit.add(term)
            score += term_score
        elif term in terms.tag_words or term in terms.category_words:
            # A joined neighbour pair only counts on an exact hit ("mark sheet" -> "marksheet").
            score += TAG_WEIGHT * (GENERIC_FACTOR if term in GENERIC_TERMS else 1.0)
            if _is_distinctive(term):
                hit.update(joined.get(term, ()))

    if distinctive and not (distinctive & hit):
        # Only generic words / the category matched while the query names something else.
        return 0.0

    for phrase in terms.phrases:
        if _contains_phrase(tokens, phrase):
            score += PHRASE_BONUS
    return score


def _uploaded_at_utc(document: DocumentMetadata) -> datetime:
    value = document.uploaded_at
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def score_document(query: str, document: DocumentMetadata) -> float:
    return _score_tokens(normalized_tokens(query), document)


def find_best_document(query: str, documents: list[DocumentMetadata]) -> RetrievalResult:
    tokens = normalized_tokens(query)
    if not tokens:
        return RetrievalResult(found=False, score=0.0)

    best_document: DocumentMetadata | None = None
    best_score = 0.0
    for document in documents:
        current_score = _score_tokens(tokens, document)
        if current_score <= MIN_MATCH_SCORE:
            continue
        if best_document is None or current_score > best_score:
            best_document, best_score = document, current_score
        elif current_score == best_score and _uploaded_at_utc(document) > _uploaded_at_utc(best_document):
            best_document = document

    if best_document is None:
        return RetrievalResult(found=False, score=0.0)
    return RetrievalResult(found=True, document=best_document, score=round(best_score, 3))
