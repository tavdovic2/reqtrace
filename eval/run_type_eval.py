"""Type accuracy on the labelled fixture, before and after the type rules.

Each sentence is shown to the model as an excerpt with the extraction prompt, which
returns its type as during extraction. "Before" is how types were assigned until now
(the model's type, "process" for testing or committing instructions, the unit's type for
ratings-table rows); "after" is `app.categories.assign_type` with the same fallbacks.
Needs Ollama.

Usage: python -m eval.run_type_eval
"""

import sys
from collections import Counter

from app.categories import assign_type
from app.config import Settings
from app.extraction_scope import is_process_instruction, type_from_unit
from app.requirements import ExtractionResult, system_prompt
from app.services import Services, build_services
from eval.type_fixture import TYPE_FIXTURE


def model_type(services: Services, sentence: str) -> str:
    prompt = f"Section: Requirements\nExcerpt (page 1):\n{sentence}"
    result = services.llm.generate_structured(system_prompt(False), prompt, ExtractionResult)
    return result.requirements[0].type if result.requirements else "other"


def main() -> int:
    """Command-line entry point."""
    services = build_services(Settings())
    before_ok = after_ok = 0
    confusion: Counter[tuple[str, str]] = Counter()
    for item in TYPE_FIXTURE:
        if item.table_row:
            fallback = type_from_unit(item.sentence)
            before = fallback
        else:
            fallback = model_type(services, item.sentence)
            before = "process" if is_process_instruction(item.sentence) else fallback
        after = assign_type(item.sentence, fallback)
        before_ok += before == item.type
        after_ok += after == item.type
        if after != item.type:
            confusion[(item.type, after)] += 1
        print(f"{item.type:13} before {before:13} after {after:13} {item.sentence}")
    total = len(TYPE_FIXTURE)
    print(f"\nbefore: {before_ok}/{total} = {before_ok / total:.0%}")
    print(f"after:  {after_ok}/{total} = {after_ok / total:.0%}")
    for (expected, got), count in confusion.most_common():
        print(f"  {expected} typed as {got}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
