"""Tell Bosnian from English questions, so the answer can be written in the same language.

A small model given English excerpts tends to answer in English even when told to
"use the language of the question"; naming the language explicitly works better.
Only the two UI languages are recognised; anything else falls back to a generic
instruction.
"""

import re
from typing import Literal

Language = Literal["Bosnian", "English"]

_WORD = re.compile(r"[^\W\d_]+")
_BOSNIAN_LETTERS = re.compile(r"[čćšžđ]", re.IGNORECASE)
_BOSNIAN = frozenset(
    """
    je li su da se za na od iz u i ili ne nije nisu ima imaju treba mora može moze koji
    koja koje kojeg koliko koliki kolika kakav kakva šta sta što sto kako gdje zašto
    zasto kada kad čemu cemu razlika između izmedju dokument dokumentu uređaj uredjaj
    """.split()
)
_ENGLISH = frozenset(
    """
    the is are was what how which who why when where of and or to does do did in on for
    a an between difference can must should with from this that it its be there
    """.split()
)


def detect_language(text: str) -> Language | None:
    """Bosnian, English, or None when the text gives no clear signal."""
    words = [w.lower() for w in _WORD.findall(text)]
    bosnian = sum(w in _BOSNIAN for w in words) + 2 * len(_BOSNIAN_LETTERS.findall(text))
    english = sum(w in _ENGLISH for w in words)
    if bosnian > english:
        return "Bosnian"
    if english > bosnian:
        return "English"
    return None
