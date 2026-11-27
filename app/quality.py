"""Quality flags for requirement rows: vague wording, a missing unit, several obligations.

Rule-based, no model calls. Flags only inform the reviewer: they never hide, block or
remove a row. They are computed when a row is read (see `row_flags`), so a change to the
rules applies to existing rows at once and an edited text is checked as soon as it is
saved.
"""

import re
from dataclasses import dataclass, field

from app.extraction_scope import (
    has_obligation_wording,
    instruction_verb,
    is_imperative,
    text_sentences,
)

EXPLANATIONS = {
    "vague": "Contains wording that cannot be verified; state a value or a clear condition.",
    "missing_unit": "Contains a number without a unit; add the unit.",
    "compound": "States several obligations; split it so each can be verified on its own.",
}


@dataclass(frozen=True)
class Trigger:
    """The words that set a flag, as a [start, end) span of the checked text."""

    text: str
    start: int
    end: int


@dataclass
class Flag:
    code: str
    # "text": the reviewer's edited text or the model's paraphrase; "quote": the quote.
    source: str
    triggers: list[Trigger] = field(default_factory=list)

    @property
    def explanation(self) -> str:
        return EXPLANATIONS[self.code]


# ---------- vague ----------

_VAGUE = re.compile(
    r"(?<![\w-])(?:"
    r"fast|quick(?:ly)?|adequate(?:ly)?|sufficient(?:ly)?|appropriate(?:ly)?|suitable|"
    r"proper(?:ly)?|reasonabl[ey]|significant(?:ly)?|minimal|usually|normally|generally|"
    r"typically|easy|easily|robust|user-friendly|"
    r"if (?:possible|practical|practicable|necessary|needed|feasible)|"
    r"as (?:close|short|small|low|high|soon|far) as (?:possible|practical)|"
    r"as (?:needed|required|appropriate)|etc\.?|and so (?:on|forth)"
    r")(?![\w-])",
    re.IGNORECASE,
)
# A vague word that is part of a name: "Fast-mode", "Fast-mode Plus", "fast table mode",
# "high-speed mode", or a register or field name in capitals.
_MODE_NAME = re.compile(r"^(?:[- ]?(?:table |speed |)mode\b)", re.IGNORECASE)


def _vague(text: str) -> list[Trigger]:
    triggers = []
    for match in _VAGUE.finditer(text):
        word = match.group()
        rest = text[match.end() :]
        if _MODE_NAME.match(rest) or (len(word) > 2 and word.isupper()):
            continue
        triggers.append(Trigger(word, match.start(), match.end()))
    return triggers


# ---------- missing_unit ----------

# Minus sign and en dash as signs, written as escapes.
_NUMBER = re.compile(r"(?<![\w.:\[/-])[-+\u2212\u2013±]?\d+(?:[.,]\d+)?(?![\w:\]/]|-\w|[.,]\d)")
_UNIT = re.compile(
    r"^\s*(?:[-\u2013]\s*)?(?:"
    r"[pnuµμmkMG]?(?:V|A|W|F|Hz|Ω|Ohms?|ohms?|s|g|m)\b|"
    r"[kKM]?(?:ohm|Ohm)s?\b|°\s?[CF]|K\b|%|ppm\b|dB\w*|LSB\b|bits?\b|bytes?\b|bar\b|Pa\b|"
    r"mm\b|cm\b|mils?\b|oz\b|in\b|inch(?:es)?\b|"
    r"(?:milli|micro|nano)?seconds?\b|minutes?\b|hours?\b|days?\b|weeks?\b|months?\b|years?\b|"
    r"volts?\b|amps?\b|watts?\b|degrees?\b|samples?\b|cycles?\b|times\b|x\b|×"
    r")",
)
# Numbers that never need a unit: references, counts written before a noun with a hyphen,
# register bits, ratios. (Part numbers and revision codes are not standalone numbers.)
_REFERENCE = re.compile(
    r"(?:table|figure|fig\.|section|chapter|equation|eq\.|step|task|lab|page|p\.|pin|rev\.?|"
    r"revision|version|item|note|appendix|example|level|mode|channel|bit|bits|byte|register|"
    r"address|device|option|grade|class|category|type|ip)\s*$",
    re.IGNORECASE,
)
# A number right after a code in capitals is part of a name: "SIL 2", "IEC 61508", "ISO 9001".
_CODE_BEFORE = re.compile(r"(?<![\w-])[A-Z]{2,}\s*$")
# A number is a quantity that needs a unit when one of these words stands shortly before it.
_QUANTITY_CUE = re.compile(
    r"\b(?:voltage|supply|current|power|temperature|resistance|resistor|resistors|capacitance|"
    r"capacitor|frequency|clock|speed|rate|time|delay|timeout|period|duration|interval|"
    r"distance|length|thickness|width|height|spacing|clearance|pressure|weight|mass|"
    r"at least|at most|no more than|no less than|not exceed|exceed|exceeds|maximum|minimum|"
    r"max|min|below|above|up to|within|less than|greater than|more than|lower than|"
    r"higher than|after|wait|draw|draws)\b[^.;]{0,25}$",
    re.IGNORECASE,
)
_RANGE_GAP = re.compile(r"^\s*(?:to|and|or|-|–|/|,)?\s*$")


def _unit_follows(text: str, end: int) -> bool:
    """A unit right after the number, or after a range or list of numbers ("1.8 to 5.5 V")."""
    rest = text[end:]
    while True:
        if _UNIT.match(rest):
            return True
        nxt = _NUMBER.search(rest)
        if not nxt or not _RANGE_GAP.match(rest[: nxt.start()]):
            return False
        rest = rest[nxt.end() :]


def _missing_unit(text: str) -> list[Trigger]:
    triggers = []
    for match in _NUMBER.finditer(text):
        before = text[: match.start()]
        if _REFERENCE.search(before) or _CODE_BEFORE.search(before):
            continue
        if not _QUANTITY_CUE.search(before):
            continue
        if _unit_follows(text, match.end()):
            continue
        # A unit written before the number, as in "V+ = 3.3": the cue word is the unit's
        # quantity and a symbol stands next to it.
        if re.search(r"(?:[A-Z]{1,3}\+?|V\w*)\s*[=:]\s*$", before):
            continue
        triggers.append(Trigger(match.group(), match.start(), match.end()))
    return triggers


# ---------- compound ----------

_CLAUSE_SPLIT = re.compile(r";\s*|,?\s+and\s+(?=\S)|,?\s+then\s+", re.IGNORECASE)
_MODAL = re.compile(r"\b(?:must|shall|should)\b", re.IGNORECASE)
_OBLIGATION_WORD = re.compile(
    r"\b(?:must|shall|should|required|requires|recommended|recommends|need to|needs to)\b",
    re.IGNORECASE,
)
_OBJECT_AFTER_VERB = re.compile(
    r"^\s*(?:please\s+)?[A-Za-z']+\s+(?:the|a|an|this|that|these|those|its|their|your|our|"
    r"each|every|all|any|both|it|them|no)\b",
    re.IGNORECASE,
)


def _is_obligation(sentence: str) -> bool:
    return has_obligation_wording(sentence) or is_imperative(sentence)


def _compound(text: str) -> list[Trigger]:
    """Two obligations in one row: two sentences that each state one, or a second clause
    after "and" or ";" that is an instruction of its own ("..., and push your work") or
    has its own "must"/"should". A list after one verb ("add a header, empty, loading and
    error states") is one obligation."""
    obligations = [s for s in text_sentences(text) if _is_obligation(s)]
    if len(obligations) >= 2:
        return [_word_in(text, s) for s in obligations]
    for sentence in obligations:
        parts = _CLAUSE_SPLIT.split(sentence)
        clauses = [parts[0]] + [
            p
            for p in parts[1:]
            if (is_imperative(p) and _OBJECT_AFTER_VERB.match(p))
            or (_MODAL.search(p) and _MODAL.search(parts[0]))
        ]
        if len(clauses) >= 2:
            return [_span(text, _clause_word(c)) for c in clauses if c.split()]
    return []


def _clause_word(clause: str) -> str:
    """The word that makes a clause an obligation: its modal verb or obligation word
    ("required", "recommended"), else its first word (the instruction verb)."""
    word = _OBLIGATION_WORD.search(clause)
    if word:
        return word.group()
    verb = instruction_verb(clause)
    if verb:
        found = re.search(r"\b" + re.escape(verb) + r"\b", clause, re.IGNORECASE)
        if found:
            return found.group()
    return clause.split()[0]


def _word_in(text: str, sentence: str) -> Trigger:
    """The obligation word of a sentence, as a span of the whole text."""
    offset = max(0, text.find(sentence))
    word = _clause_word(sentence)
    start = offset + max(0, sentence.find(word))
    return Trigger(word, start, start + len(word))


def _span(text: str, part: str) -> Trigger:
    start = max(0, text.find(part))
    return Trigger(part, start, start + len(part))


def check(text: str, source: str) -> list[Flag]:
    """The flags of one text, with the words that triggered each."""
    flags = []
    for code, finder in (
        ("vague", _vague),
        ("missing_unit", _missing_unit),
        ("compound", _compound),
    ):
        triggers = finder(text)
        if triggers:
            flags.append(Flag(code, source, triggers))
    return flags


def row_flags(quote: str, paraphrase: str, edited_text: str | None) -> list[Flag]:
    """The flags of a requirement row.

    An edited text replaces the original wording, so only it is checked: fixing the
    wording removes the flag. Without an edit the quote is checked, and the paraphrase
    adds flags the quote does not have.
    """
    if edited_text:
        return check(edited_text, "text")
    flags = check(quote, "quote")
    if paraphrase and paraphrase != quote:
        codes = {f.code for f in flags}
        flags += [f for f in check(paraphrase, "text") if f.code not in codes]
    return flags
