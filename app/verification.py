"""Verification of extracted requirements against the PDF itself.

Two independent checks, because the LLM can fail in two independent ways:

1. **Is the quote on the page?** The quote is located in the page's own text layer
   after normalising what PDF extraction changes (line breaks, hyphenation,
   ligatures, typographic characters). The stored quote is then the exact span of
   page text that matched, never the model's transcription of it.
2. **Does the requirement say what the quote says?** Every significant term of the
   reworded requirement must occur in the quote. A real quote attached to an
   unrelated or invented requirement fails here.
"""

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

_TRANSLATION = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "−": "-",
    "­": "",
    "|": " ",
}
_HYPHENATED_BREAK = re.compile(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)")
_TABLE_RULE = re.compile(r"(?<!\w)[-:]{3,}(?!\w)")
_WHITESPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_TOKEN = re.compile(r"[^\W_]+(?:[._/+-][^\W_]+)*")
_IDENTIFIER = re.compile(r"\b(?:[a-z]+[A-Z]\w*|[A-Z][a-z0-9]+[A-Z]\w*|\w+_\w+)\b")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
# A line break followed by a capital letter or a digit starts a new sentence, heading
# or table row; a wrapped sentence continues in lower case.
_NEW_LINE_START = re.compile(r"\n\s*[A-Z0-9]")

# Words that carry no content of their own in a reworded requirement: articles,
# pronouns, prepositions, auxiliaries, modal verbs, generic requirement verbs and
# generic subjects. Everything else must be backed by the quote.
EXEMPT_WORDS = frozenset(
    """
    a an the this that these those it its they them their there here of to in on at by
    for with from into onto over under as per via than then and or nor but if when while
    so such each every all any both either neither is are was were be been being am do
    does did done has have had having shall must should may might can could will would
    need needs required require requires not no only also between least most system
    application app software product device component implementer developer user users
    student students you your we our allow allows enable enables provide provides
    support supports ensure ensures able capable using use used make makes
    """.split()
)
_STRONG = frozenset({"shall", "must", "required", "requires", "mandatory"})
_WEAK = frozenset({"should", "recommended", "recommend", "recommends", "ideally"})
_OPTIONAL = frozenset({"may", "might", "can", "could", "optional", "optionally"})


@dataclass(frozen=True)
class Normalized:
    """Normalised text plus, for every character, its index in the source text."""

    text: str
    origin: list[int]


def _substitute(norm: Normalized, pattern: re.Pattern[str], replacement: str) -> Normalized:
    """Regex substitution that keeps the index map aligned."""
    chars: list[str] = []
    origin: list[int] = []
    last = 0
    for match in pattern.finditer(norm.text):
        chars.extend(norm.text[last : match.start()])
        origin.extend(norm.origin[last : match.start()])
        if replacement:
            chars.extend(replacement)
            origin.extend([norm.origin[match.start()]] * len(replacement))
        last = match.end()
    chars.extend(norm.text[last:])
    origin.extend(norm.origin[last:])
    return Normalized("".join(chars), origin)


def normalize_with_map(text: str) -> Normalized:
    """Canonical form for comparison, keeping track of where each character came from.

    NFKC, typographic characters unified, hyphenation across line breaks removed,
    table pipes and rules dropped, whitespace collapsed, lower case.
    """
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(text):
        for out in unicodedata.normalize("NFKC", char):
            out = _TRANSLATION.get(out, out)
            for lowered in out.lower():
                chars.append(lowered)
                origin.append(index)
    norm = Normalized("".join(chars), origin)
    norm = _substitute(norm, _HYPHENATED_BREAK, "")
    norm = _substitute(norm, _TABLE_RULE, " ")
    norm = _substitute(norm, _WHITESPACE, " ")
    start = len(norm.text) - len(norm.text.lstrip())
    end = len(norm.text.rstrip())
    return Normalized(norm.text[start:end], norm.origin[start:end])


def normalize(text: str) -> str:
    """Canonical form for comparison (see normalize_with_map)."""
    return normalize_with_map(text).text


@dataclass(frozen=True)
class QuoteMatch:
    """Where a quote was found on the page: [start, end) in the original page text."""

    start: int
    end: int
    score: float


def _widen_to_words(text: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and text[start - 1].isalnum():
        start -= 1
    while end < len(text) and text[end].isalnum():
        end += 1
    return start, end


def _trim_foreign_edges(text: str, start: int, end: int, needle: str) -> tuple[int, int]:
    """Drop words at either edge of a fuzzy match that the quote does not contain.

    On table text the alignment window often overlaps the neighbouring rows
    ("Meaning A Output 4 to 20 mA ... HART 7 V"); those edge words are not part of
    the quote.
    """
    tokens = _TOKEN.findall(needle)
    words, opening, closing = set(tokens), set(tokens[:2]), set(tokens[-2:])
    spans = [m.span() for m in _TOKEN.finditer(text, start, end)]
    while spans and text[spans[0][0] : spans[0][1]] not in words:
        spans.pop(0)
    while spans and text[spans[-1][0] : spans[-1][1]] not in words:
        spans.pop()
    if not spans:
        return start, end
    # A word the quote contains can still belong to the neighbouring row ("°C Media
    # temperature", "IP67 Ingress"): start at the quote's opening words and end at its
    # closing words when they are present.
    first = next((i for i, (a, b) in enumerate(spans) if text[a:b] in opening), 0)
    last = next(
        (i for i in range(len(spans) - 1, first - 1, -1) if text[slice(*spans[i])] in closing),
        len(spans) - 1,
    )
    spans = spans[first : last + 1]
    first, last = spans[0][0], spans[-1][1]
    while last < end and not text[last].isspace() and not text[last].isalnum():
        last += 1
    return first, last


def _extend_to_sentence_end(page_text: str, end: int, limit: int) -> int:
    """Complete a quote the model cut off mid-sentence, if the sentence ends nearby.

    The extension never crosses into a line that starts like a new sentence, so a
    heading is not merged with the paragraph below it.
    """
    if page_text[:end].rstrip().endswith((".", "!", "?", ";", ":")):
        return end
    window = page_text[end : end + limit]
    match = _SENTENCE_END.search(window)
    if match is None or _NEW_LINE_START.search(window[: match.start() + 1]):
        return end
    return end + match.end()


def locate_quote(
    quote: str,
    page_text: str,
    threshold: float,
    min_chars: int,
    sentence_extension: int = 80,
) -> QuoteMatch | None:
    """Find `quote` on the page, exactly or with small extraction differences.

    Returns the matching span of the original page text, or None. Very short
    quotes are rejected because they would match almost any page.
    """
    needle = normalize(quote)
    page = normalize_with_map(page_text)
    if len(needle) < min_chars or not page.text:
        return None
    position = page.text.find(needle)
    if position != -1:
        a, b, score = position, position + len(needle), 100.0
    else:
        alignment = fuzz.partial_ratio_alignment(needle, page.text)
        if alignment is None or alignment.score < threshold:
            return None
        a, b, score = alignment.dest_start, alignment.dest_end, alignment.score
        a, b = _trim_foreign_edges(page.text, *_widen_to_words(page.text, a, b), needle)
    start, end = page.origin[a], page.origin[b - 1] + 1
    end = _extend_to_sentence_end(page_text, end, sentence_extension)
    return QuoteMatch(start=start, end=end, score=score)


def numbers_in(text: str) -> list[str]:
    """The numbers in a text, in order, after normalisation."""
    return _NUMBER.findall(normalize(text))


# Number words in the two UI languages. A source that says "four" supports a claim
# that says "4".
NUMBER_WORDS: dict[str, str] = {
    word: str(value)
    for value, words in enumerate(
        [
            "zero nula",
            "one jedan jedna jedno",
            "two dva dvije dvoje",
            "three tri troje",
            "four četiri cetiri",
            "five pet",
            "six šest sest",
            "seven sedam",
            "eight osam",
            "nine devet",
            "ten deset",
            "eleven jedanaest",
            "twelve dvanaest",
            "thirteen trinaest",
            "fourteen četrnaest cetrnaest",
            "fifteen petnaest",
            "sixteen šesnaest sesnaest",
            "seventeen sedamnaest",
            "eighteen osamnaest",
            "nineteen devetnaest",
            "twenty dvadeset",
        ]
    )
    for word in words.split()
}


def numbers_with_words(text: str) -> set[str]:
    """Numbers written as digits or as number words ("four", "četiri")."""
    words = {NUMBER_WORDS[t] for t in _TOKEN.findall(normalize(text)) if t in NUMBER_WORDS}
    return set(numbers_in(text)) | words


def _stem(token: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _covered(term: str, quote_tokens: set[str], quote_stems: set[str]) -> bool:
    if term in quote_tokens or _stem(term) in quote_stems:
        return True
    stem = _stem(term)
    return len(stem) >= 4 and any(
        q.startswith(stem) or stem.startswith(q) for q in quote_stems if len(q) >= 4
    )


@dataclass(frozen=True)
class Support:
    """How well a reworded requirement is backed by its quote."""

    score: float
    missing: list[str]
    missing_strict: list[str]

    def passes(self, threshold: float) -> bool:
        return not self.missing_strict and self.score >= threshold


def requirement_support(requirement: str, quote: str) -> Support:
    """Share of the requirement's significant terms that occur in the quote.

    Numbers, units next to them and code identifiers are strict: one missing
    strict term fails the check regardless of the share.
    """
    identifiers = {m.lower() for m in _IDENTIFIER.findall(requirement)}
    quote_tokens = set(_TOKEN.findall(normalize(quote)))
    quote_stems = {_stem(t) for t in quote_tokens}
    terms = [
        t
        for t in _TOKEN.findall(normalize(requirement))
        if t.isdigit() or (len(t) > 1 and t not in EXEMPT_WORDS)
    ]
    missing = [t for t in terms if not _covered(t, quote_tokens, quote_stems)]
    strict = [t for t in missing if any(c.isdigit() for c in t) or t in identifiers]
    score = 1.0 if not terms else (len(terms) - len(missing)) / len(terms)
    return Support(score=score, missing=missing, missing_strict=strict)


def _modality(text: str) -> int:
    tokens = set(_TOKEN.findall(normalize(text)))
    if tokens & _STRONG:
        return 3
    if tokens & _WEAK:
        return 2
    if tokens & _OPTIONAL:
        return 1
    return 0


def modality_preserved(requirement: str, quote: str) -> bool:
    """False when the rewording is stronger than an explicitly weaker source.

    "should" must not become "must"; "may" must not become "should". A quote with
    no modal verb (an imperative task or a specification row) is not judged here.
    """
    source = _modality(quote)
    return source == 0 or _modality(requirement) <= source


def drop_foreign_colons(requirement: str, quote: str) -> str:
    """Remove colons the quote does not contain.

    qwen3:4b under constrained JSON decoding sometimes inserts colons into copied
    text ("2:00" for "200", "Labs : 4"). A colon absent from the quote cannot be
    content, so it is removed before the support check.
    """
    if ":" in quote:
        return requirement
    return _WHITESPACE.sub(" ", requirement.replace(":", "")).strip()


def is_fragment(text: str) -> bool:
    """A heading, a cut-off phrase or text ending in a colon is not a requirement."""
    stripped = text.strip()
    return len(re.findall(r"\w+", stripped)) < 4 or stripped.endswith((":", ",", "-"))


def display_quote(quote: str) -> str:
    """A stored quote for display and export: one line, single spaces.

    The stored quote keeps the page's own line breaks so that it stays an exact span
    of the page text; readers and spreadsheets get it on one line. A hyphen at a line
    end is kept and joined without a space ("power-\nup" becomes "power-up").
    """
    joined = re.sub(r"-[ \t]*\r?\n[ \t]*", "-", quote)
    return _WHITESPACE.sub(" ", joined).strip()
