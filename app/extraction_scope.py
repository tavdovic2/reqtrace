"""What requirement extraction looks at, and what it keeps.

reqTrace extracts exactly two kinds of item:

- **obligation**: the reader (designer, implementer, user) is told to do or not do
  something: "shall"/"must" sentences, explicit recommendations and imperatives;
- **limit**: a specified boundary the product must be operated within, typically a row
  of an absolute-maximum-ratings or recommended-operating-conditions table.

Everything else, including descriptions of how the device behaves, is deliberately
left out. The rules here are deterministic, so the decision does not depend on how a
small model happens to read a sentence; the model only proposes candidates.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import cache

from app.vector_store import ChunkPayload


class Kind(StrEnum):
    OBLIGATION = "obligation"
    LIMIT = "limit"


class Scope(StrEnum):
    """How extraction treats a section."""

    FULL = "full"  # every chunk goes to the model
    SCREEN = "screen"  # only sentences with obligation wording go to the model
    SKIP = "skip"  # nothing is read


# Section rules are regular expressions matched against the *whole* heading, after its
# number, a trailing "(continued)" and footnote marks are removed and case is ignored.
# A rule never matches part of a longer heading: "description" matches "3 Description"
# but not "7 Detailed Description" or "7.3 Feature Description".
#
# Sections that by definition hold neither kind (README, "What is extracted"): front and
# back matter, legal text, ordering and packaging. Nothing in them is read. Applies at
# any depth. Configurable through EXTRACTION_SKIP_SECTIONS.
DEFAULT_SKIP_SECTIONS: tuple[str, ...] = (
    r"(?:document )?revision history|revision record|change history|changes from .*",
    r"(?:table of )?contents|list of (?:figures|tables)",
    r"trademarks?|glossary",
    r"(?:device and )?documentation support|related documentation|support resources",
    r"receiving notification of documentation updates|community resources",
    r"mechanical(?:,? packaging,? and orderable)? information|mechanical data",
    r"package (?:information|outline|dimensions|option addendum|materials information)",
    r"packaging(?: information)?|package marking information|tape and reel information",
    r"ordering information|orderable information|product identification system",
    r"important notice(?: and disclaimer)?|disclaimer|legal(?: notice)?",
)
# Sections that describe the device: overviews, characteristics tables, register maps.
# They are not dropped. A cheap rule-based scan keeps only the sentences with obligation
# wording or an instruction, and only those go to the model; their tables (parameter
# and register tables) are not read. Applies at any depth. Configurable through
# EXTRACTION_SCREEN_SECTIONS.
DEFAULT_SCREEN_SECTIONS: tuple[str, ...] = (
    r"(?:detailed|functional|general|device|product) description",
    r"(?:device |functional )?overview|feature description|(?:device )?functional modes",
    r"(?:functional )?block diagrams?|theory of operation|principle of operation",
    r"(?:typical|electrical|switching|thermal|dc|ac) characteristics|thermal information",
    r"esd ratings|timing diagrams?|device comparison(?: table)?",
    r"register maps?|registers|register (?:descriptions?|summary)|memory map",
)
# The same, but only as top-level sections (the first heading of the path): the front
# page of a datasheet. "8.2 Typical Application" is not affected. Configurable through
# EXTRACTION_SCREEN_TOP_SECTIONS.
DEFAULT_SCREEN_TOP_SECTIONS: tuple[str, ...] = (
    r"(?:general |device |product )?description",
    r"(?:key )?features",
    r"(?:typical |target )?applications",
)
# A ratings section inside a screened chapter ("4 Electrical characteristics > 4.1
# Absolute maximum ratings", common in ST datasheets) is read in full: its rows are limits.
_RATINGS_HEADING = re.compile(
    r"absolute maximum ratings?|maximum ratings|(?:recommended )?operating (?:conditions|ratings)",
    re.IGNORECASE,
)

_NUMBERING = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[A-Z]\.)\s+")
_OBLIGATION_WORDS = re.compile(
    r"\b(?:shall|must|should|required|requires|require|mandatory|recommended(?!\s+operating)|recommends|"
    r"recommend|need to|needs to|has to|have to|make sure)\b"
    # Not followed by "(": require('./x') in code is a call, not an obligation.
    # "Do not", "never", "avoid" and "ensure" count only as instructions at the start of a
    # sentence (see IMPERATIVE_VERBS): "which do not imply ..." or "this ensures ..." are
    # descriptions.
    r"(?!\s*\()",
    re.IGNORECASE,
)
_STRONG_WORDS = re.compile(r"\b(?:shall|must|required|requires|mandatory)\b", re.IGNORECASE)
# Base-form verbs that start instructions in datasheets and assignments. Words that are
# mostly references ("see", "refer", "note") are left out on purpose.
IMPERATIVE_VERBS = frozenset(
    """
    add adjust apply attach avoid bring build calibrate call change check choose clean
    combine commit configure connect cover create declare define delete disable disconnect
    display do don't drive enable ensure export extract fetch filter follow give ground
    group handle hide implement import include increase install keep let limit load
    locate lower make minimize maximize mount move name never open pass place power
    prefer prevent protect provide pull push put raise reduce remove rename render
    replace reset return reuse route run save select separate set show solder sort split
    start stop store submit test tie tighten turn update upload use verify wait
    wire wrap write read review complete finish prepare study watch
    """.split()
)
# Words that open a clause but are never the verb of an instruction: articles,
# pronouns, prepositions, conjunctions, auxiliaries and sentence adverbs. ("Do" is an
# auxiliary too, but "Do not ..." is an instruction; the verb list is checked first.)
_CLOSED_CLASS = frozenset(
    """
    a an the this that these those its their his her our your my it they we you he she
    there here each every all any both either neither no none some such many much most
    more less few several other another one two three half twice once
    about above across after against along among around as at before behind below
    beneath beside besides between beyond by despite down during except for from in
    inside into like unlike near of off on onto out outside over past per since through
    throughout till to toward towards under underneath until up upon via with within
    without plus minus versus than given due
    and or but nor so yet because although though while whereas if unless when whenever
    where wherever whether which who whom whose what how why
    is are was were be been being does did has have had can could will would shall
    should may might must
    also only then thus therefore however hence moreover furthermore otherwise instead
    still even just again now first second third next last finally further
    """.split()
)
# Words that point elsewhere rather than instruct ("See Figure 3", "Note the ...").
_REFERENCE_WORDS = frozenset({"see", "refer", "note", "figure", "table", "example", "equation"})
# Adverbs that may stand before the verb of an instruction: "Only reprogram ...",
# "First unlock ...", "Please send ...".
_LEADING_ADVERBS = frozenset({"please", "always", "only", "also", "first", "then", "just"})
# What usually follows the verb of an instruction: a determiner or a pronoun.
_OBJECT_START = frozenset(
    """
    the a an this that these those its their your our each every all any both no some
    one two it them
    """.split()
)
# Word endings that are not a base-form verb: -ing, -ly, -ed and a plural or third-person
# -s ("-ss" and "-us" are kept: "address", "focus").
_NOT_BASE_FORM = re.compile(r"(?:ing|ly|ed|(?<![su])s)$")
_ADJECTIVE_ENDING = re.compile(r"(?:al|ic|ive|ous|ful|less|able|ible|ary|ory)$")
# A purpose or benefit phrase that can come before an instruction without a comma: "To
# minimize leakage locate the sensor ...", "For best accuracy place ...".
_PURPOSE_INTRO = re.compile(r"^(?:to|for|in order to)\s", re.IGNORECASE)
_MAX_INTRO_WORDS = 15
# A verb-like word joined by a hyphen is part of a compound: "read-only register",
# "power-up time", "start-up".
_COMPOUND_START = re.compile(r"^\W*(?:(?:please|always|only|also|first|then|just)\s+)?\w+-\w", re.I)
# Instructions about how the work is done (testing, version control, submission).
PROCESS_VERBS = frozenset({"test", "commit", "push", "submit", "upload"})
# A finite verb right after the first word means it was a subject, not an instruction:
# "power consumption is ...", "the empty state says so".
_FINITE_AFTER = re.compile(
    r"^\S+(?:\s+\S+){0,2}?\s+(?:is|are|was|were|has|have|had|does|can|will|may|says|remains|becomes)\b",
    re.IGNORECASE,
)
# An introductory phrase or clause before the instruction ("Below the summary row, add
# ...", "Before the lecture, read ..."): anything up to the first comma, if short.
_LEADING_PHRASE = re.compile(r"^([^,;:]{1,120}),\s*(.+)$", re.DOTALL)
_LIST_MARKER = re.compile(r"^\s*(?:[-*•●–]|\(?[0-9a-z]{1,2}[.)])\s+", re.IGNORECASE)
_FOOTNOTE = re.compile(r"^\s*\(\d{1,2}\)\s*")
_FOOTNOTE_MARK = re.compile(r"\(\d{1,2}\)")
# Table conditions such as "Over free-air temperature range (unless otherwise noted)".
_TABLE_CONDITION = re.compile(r"unless otherwise noted|^\s*over\b.{0,60}\brange\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z']+")
_NUMBER = re.compile(r"(?<![A-Za-z])[-+−–]?\d+(?:[.,]\d+)?")
_LIMIT_SECTION = re.compile(
    r"absolute maximum|maximum rating|recommended operating|operating condition|"
    r"\blimits?\b|\bratings?\b",
    re.IGNORECASE,
)
_LIMIT_WORDS = re.compile(
    r"\b(?:max(?:imum)?|min(?:imum)?|not exceed|exceed|up to|at least|at most|no more than|"
    r"no less than|between|range|below|above)\b",
    re.IGNORECASE,
)
# Timing and switching tables describe bus behaviour, not operating limits; none of their
# rows is a limit (documented in the README), whatever words a row contains.
_TIMING_SECTION = re.compile(r"\btiming\b|switching characteristics", re.IGNORECASE)
# A sentence that only introduces the list or table after it ("Check your work with the
# following steps.") carries no requirement of its own; the rows after it do.
_LEAD_IN = re.compile(r"\b(?:the following|as follows)(?:\s+\w+)?\s*[.:]?\s*$", re.IGNORECASE)
_TEST_INPUT = re.compile(r"^\s*(?:input|expected (?:output|result))\b", re.IGNORECASE)
# Code listings: declarations, calls with string arguments and arrow functions. (A line
# ending in ";" is common in prose lists, so it is not taken as a sign of code.)
_CODE = re.compile(
    r"\b(?:const|let|var|function|import|export|type|interface|class|def)\s+\w+\s*[=:{(<]"
    r"|\w+\s*\(\s*['\"]|=>"
)


_CONTINUED = re.compile(r"\s*\((?:continued|cont\.?|\d{1,2})\)\s*$", re.IGNORECASE)


def heading_text(heading: str) -> str:
    """A heading as section rules see it: without number, "(continued)", footnote marks
    and a trailing colon; spaces collapsed, lower case."""
    text = _NUMBERING.sub("", heading.strip())
    previous = None
    while previous != text:
        previous = text
        text = _CONTINUED.sub("", text).strip().rstrip(":").strip()
    return " ".join(text.replace("&", "and").split()).lower()


@cache
def _compile(patterns: tuple[str, ...]) -> re.Pattern[str] | None:
    alternatives = [f"(?:{p.strip()})" for p in patterns if p.strip()]
    return re.compile("|".join(alternatives), re.IGNORECASE) if alternatives else None


def _fullmatch(patterns: tuple[str, ...], text: str) -> bool:
    compiled = _compile(patterns)
    return bool(compiled and compiled.fullmatch(text))


@dataclass(frozen=True)
class SectionRules:
    """Which sections are skipped, screened or read in full (see the DEFAULT_* lists)."""

    skip: tuple[str, ...] = DEFAULT_SKIP_SECTIONS
    screen: tuple[str, ...] = DEFAULT_SCREEN_SECTIONS
    screen_top: tuple[str, ...] = DEFAULT_SCREEN_TOP_SECTIONS

    def scope(self, section: str) -> tuple[Scope, str | None]:
        """How a chunk with this heading path is treated, and the heading that decided.

        A skipped heading anywhere in the path skips the chunk. Otherwise a screened
        heading screens it, unless a ratings section lies below that heading.
        """
        parts = [part for part in section.split(" > ") if part.strip()]
        texts = [heading_text(part) for part in parts]
        for part, text in zip(parts, texts, strict=True):
            if _fullmatch(self.skip, text):
                return Scope.SKIP, part.strip()
        for depth, (part, text) in enumerate(zip(parts, texts, strict=True)):
            if _fullmatch(self.screen, text) or (depth == 0 and _fullmatch(self.screen_top, text)):
                if any(_RATINGS_HEADING.fullmatch(t) for t in texts[depth + 1 :]):
                    return Scope.FULL, None
                return Scope.SCREEN, part.strip()
        return Scope.FULL, None


def table_columns(chunk: ChunkPayload) -> set[str]:
    """Lower-case header cells of a table chunk (empty for text chunks)."""
    if chunk.kind != "table":
        return set()
    header = next((line for line in chunk.text.splitlines() if line.strip().startswith("|")), "")
    return {cell.strip().lower() for cell in header.strip().strip("|").split("|") if cell.strip()}


def non_requirement_table(chunk: ChunkPayload) -> str | None:
    """The kind of table whose rows are never obligations or limits, or None.

    Recognised by the header: test tables, change logs, default-value tables,
    register and bit-field tables, and troubleshooting tables.
    """
    columns = table_columns(chunk)
    if not columns:
        return None
    has = columns.__contains__
    starts = lambda prefix: any(c.startswith(prefix) for c in columns)  # noqa: E731
    if has("input") and (starts("expected") or has("output") or has("result")):
        return "test table"
    if columns & {"revision", "version"} and columns & {"date", "change", "changes"}:
        return "change log"
    if any(c == "default" or c.startswith("default ") for c in columns):
        return "default values"
    if (has("bit") or has("bits")) and columns & {"field", "type", "reset"}:
        return "bit fields"
    if columns & {"address", "offset"} and columns & {"register", "register name", "reset"}:
        return "register map"
    if columns & {"problem", "symptom", "issue"} and columns & {
        "fix",
        "solution",
        "cause",
        "remedy",
        "action",
    }:
        return "troubleshooting"
    return None


def _instruction_starts(sentence: str) -> list[tuple[str, bool]]:
    """Where an instruction could start in a sentence, and whether only a verb from
    IMPERATIVE_VERBS counts there.

    At the beginning; after a short introductory phrase that ends with a comma; and
    after a purpose phrase without a comma ("To minimize leakage locate the sensor ...").
    Without the comma the boundary is only a guess, so it must be a known verb, written
    in lower case and not preceded by a determiner or preposition ("for the power ...").
    """
    sentence = _LIST_MARKER.sub("", sentence).strip()
    starts = [(sentence, False)]
    match = _LEADING_PHRASE.match(sentence)
    if match and len(match.group(1).split()) <= _MAX_INTRO_WORDS:
        starts.append((match.group(2), False))
    if _PURPOSE_INTRO.match(sentence):
        words = list(_WORD.finditer(sentence))
        for index in range(2, min(len(words), _MAX_INTRO_WORDS + 1)):
            word = words[index]
            if "," in sentence[: word.start()]:
                break
            if (
                word.group() in IMPERATIVE_VERBS
                and words[index - 1].group().lower() not in _CLOSED_CLASS
            ):
                starts.append((sentence[word.start() :], True))
                break
    return starts


def _verb_at_start(words: list[str], known_only: bool, sentence_end: bool) -> str | None:
    """The verb of an instruction that starts with `words`, or None.

    A verb from IMPERATIVE_VERBS always counts. Any other word counts when it has the
    form of a base-form verb and is followed by what usually follows one: a determiner
    or pronoun ("Miniaturize the board", "Extend these layers"), or, in a full sentence,
    a part name in capitals ("Design PCB pads ... ."), unless it looks like an adjective
    ("Thermal PCB"). A table header ("Parameter MIN MAX UNIT") is not a sentence.
    """
    index = 0
    while index < len(words) - 1 and words[index].lower() in _LEADING_ADVERBS:
        index += 1
    word = words[index]
    lower = word.lower()
    if len(word) > 1 and word.isupper():
        return None  # a signal name such as STOP or SCL
    if lower in IMPERATIVE_VERBS:
        return lower
    if (
        known_only
        # An ordinary word: lower case or capitalised, not a symbol such as "tHD".
        or not (word.islower() or (word[:1].isupper() and word[1:].islower()))
        or len(lower) < 3
        or lower in _CLOSED_CLASS
        or lower in _REFERENCE_WORDS
        or _NOT_BASE_FORM.search(lower)
    ):
        return None
    following = words[index + 1] if index + 1 < len(words) else ""
    if following.lower() in _OBJECT_START:
        return lower
    if (
        sentence_end
        and len(following) > 1
        and following.isupper()
        and not _ADJECTIVE_ENDING.search(lower)
    ):
        return lower
    return None


def instruction_verb(text: str) -> str | None:
    """The verb an instruction in `text` starts with, or None if there is no instruction.

    A clause counts when it starts with an instruction verb (see _verb_at_start) that
    is not followed by a finite verb, which would make it a subject ("Power consumption
    is ...", "the empty state says so").
    """
    for sentence in _SENTENCE.split(text.strip()):
        # A table cell such as "Load capacitance up to 400 pF" starts with a verb-like
        # noun; an instruction reads as a sentence, so require final punctuation when
        # the text carries numbers.
        if _NUMBER.search(sentence) and not sentence.rstrip().endswith((".", "!", "?")):
            continue
        for start, known_only in _instruction_starts(sentence):
            words = _WORD.findall(start)
            if not words:
                continue
            sentence_end = start.rstrip().endswith((".", "!", "?"))
            verb = _verb_at_start(words, known_only, sentence_end)
            if (
                verb is not None
                and not _FINITE_AFTER.match(start)
                and not _COMPOUND_START.match(start)
            ):
                return verb
    return None


def is_imperative(text: str) -> bool:
    """True when a sentence of `text` is an instruction, at its start or after a short
    introductory phrase ("To prevent oxidation, cover ...", "Below the row, add ...")."""
    return instruction_verb(text) is not None


def is_process_instruction(text: str) -> bool:
    """True for instructions about how the work is done: testing, committing, submitting."""
    return instruction_verb(text) in PROCESS_VERBS


def looks_like_code(text: str) -> bool:
    """True for code listings: they are examples, never obligations."""
    return bool(_CODE.search(text))


def has_obligation_wording(text: str) -> bool:
    """True when the text contains obligation or recommendation wording."""
    return bool(_OBLIGATION_WORDS.search(text))


def is_obligation(text: str) -> bool:
    """An obligation is worded as one, or is an instruction (imperative)."""
    return has_obligation_wording(text) or is_imperative(text)


# "X is recommended", "X are recommended", "it is recommended to X", "X should be used".
_PASSIVE_RECOMMENDATION = re.compile(
    r"\b(?:is|are)\s+(?:\w+ly\s+|also\s+)?recommended\b(?!\s+operating)"
    r"|\bshould\s+(?:not\s+)?(?:\w+ly\s+)?be\s+(?!noted\b)"
    r"(?:\w+ed|\w+en|kept|left|set|put|held|built|made|done|run|read|cut|met|laid|tied|sent)\b",
    re.IGNORECASE,
)
# Wording that makes a sentence in a screened section worth showing to the model.
_SCREEN_WORDS = re.compile(
    r"\b(?:at least|no more than|do not|don't|never|avoid|ensure)\b", re.IGNORECASE
)


def text_sentences(text: str) -> list[str]:
    """The sentences of a chunk's text; table rows and list markers are left out."""
    sentences = []
    for line in text.splitlines():
        line = _LIST_MARKER.sub("", line).strip()
        if line and not line.startswith("|"):
            sentences.extend(s.strip() for s in _SENTENCE.split(line) if s.strip())
    return sentences


def is_passive_recommendation(sentence: str) -> bool:
    """True for recommendations in the passive: "A 0.1-µF capacitor is recommended",
    "it is recommended to ...", "pullup resistors should be used"."""
    return bool(_PASSIVE_RECOMMENDATION.search(sentence)) and not _LEAD_IN.search(sentence)


def worth_screening(sentence: str) -> bool:
    """True when a sentence of a screened section could state an obligation: it has
    obligation wording ("must", "should", "is recommended", "at least", "do not",
    "avoid", ...) or is an instruction."""
    return (
        has_obligation_wording(sentence)
        or bool(_SCREEN_WORDS.search(sentence))
        or is_imperative(sentence)
    )


def screened_text(text: str) -> str:
    """Only the sentences of `text` worth showing to the model, one per line."""
    return "\n".join(s for s in text_sentences(text) if worth_screening(s))


_UNIT_TYPES = (
    (re.compile(r"(?:°\s?[CF]\b|\bK\b)"), "environmental"),
    (re.compile(r"\b(?:[mµunkM]?(?:V|A|W|F)|[kM]?(?:Ω|Ohm))\b"), "electrical"),
    (re.compile(r"\b(?:[mµun]?s|[kM]?Hz)\b", re.IGNORECASE), "performance"),
)
_CELL_SPLIT = re.compile(r"\s*\|\s*")


def is_limit_section(section: str) -> bool:
    """True for ratings and operating-conditions sections, where table rows are limits."""
    return bool(_LIMIT_SECTION.search(section))


def limit_rows(markdown: str) -> list[str]:
    """The data rows of a ratings table that carry values, each as one line of text.

    Cells repeated by the table parser (a spanning cell copied into its neighbour) are
    written once.
    """
    rows = []
    lines = [line.strip() for line in markdown.splitlines() if line.strip().startswith("|")]
    for line in lines[1:]:
        cells = [c.strip() for c in _CELL_SPLIT.split(line.strip().strip("|")) if c.strip()]
        if not cells or all(set(c) <= set("-:") for c in cells):
            continue
        unique = [c for i, c in enumerate(cells) if i == 0 or c != cells[i - 1]]
        text = " ".join(unique)
        if _NUMBER.search(_FOOTNOTE_MARK.sub("", text)) and not _FOOTNOTE.match(text):
            rows.append(text)
    return rows


def type_from_unit(text: str) -> str:
    """A requirement type guessed from the unit of a limit row.

    The last unit in the row decides: it belongs to the values, while earlier ones
    usually belong to a condition ("Supply voltage, TA = -55 °C to 150 °C ... 5.5 V").
    """
    last = max(
        (
            (match.start(), kind)
            for pattern, kind in _UNIT_TYPES
            for match in pattern.finditer(text)
        ),
        default=None,
    )
    return last[1] if last else "other"


def is_limit(text: str, section: str) -> bool:
    """A limit states numbers inside a ratings or operating-conditions section, or
    next to explicit boundary words such as "maximum" or "at least".

    Footnote markers like "(1)" are not values, footnotes themselves and table
    conditions ("over free-air temperature range, unless otherwise noted") are not limits.
    """
    if _FOOTNOTE.match(text) or _TABLE_CONDITION.search(text) or _TIMING_SECTION.search(section):
        return False
    body = _FOOTNOTE_MARK.sub("", text)
    if not _NUMBER.search(body):
        return False
    return bool(_LIMIT_SECTION.search(section) or _LIMIT_WORDS.search(body))


def classify(quote: str, section: str) -> Kind | None:
    """The kind of a verified quote, or None when it is a description.

    Obligation wording wins: "The supply voltage must not drop below 1.8 V" is an
    obligation even though it also names a boundary. Rows with values in a ratings
    section are limits before any imperative reading is tried.
    """
    if _TEST_INPUT.match(quote) or looks_like_code(quote) or _LEAD_IN.search(quote):
        return None
    if has_obligation_wording(quote):
        return Kind.OBLIGATION
    # In a ratings or operating-conditions section, a row with values is a limit even if
    # its first word could be read as a verb ("Supply voltage ...", "Power dissipation ...").
    if _LIMIT_SECTION.search(section) and is_limit(quote, section):
        return Kind.LIMIT
    if is_imperative(quote):
        return Kind.OBLIGATION
    if is_limit(quote, section):
        return Kind.LIMIT
    return None


def invents_obligation(paraphrase: str, quote: str) -> bool:
    """True when the paraphrase says must/shall/required and the quote does not.

    An imperative quote is an instruction, so "The designer must place ..." for
    "Place ..." is not an invention.
    """
    return bool(
        _STRONG_WORDS.search(paraphrase)
        and not _STRONG_WORDS.search(quote)
        and not is_imperative(quote)
    )
