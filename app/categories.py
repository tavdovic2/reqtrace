"""Requirement types, decided by rule; the model's type is used only when no rule matches.

Types (one-line definitions in the README, "What is extracted"):

- process: how the work is done (version control, testing, submission);
- safety: protecting people or equipment (hazards, injury, ESD, damage);
- layout: PCB layout and board design, placement of parts, soldering, thermal and exposed pads,
  copper planes, traces, vias, solder mask, air flow around the board;
- electrical: supply, voltage, current, power, resistors, capacitors, grounding;
- environmental: temperature, humidity, vibration, storage, materials;
- performance: speed, timing, accuracy, resolution, response time;
- interface: pins, buses, protocols, addresses, registers, APIs, data formats;
- functional: a behaviour, feature or screen to build;
- other: none of the above.

When several rules match, the first type in that order wins: a capacitor placed next to
the supply pins is a layout rule, a pull-up resistor on the SDA pin is electrical.
"""

import re

from app.extraction_scope import is_process_instruction

TYPES = (
    "functional",
    "performance",
    "electrical",
    "interface",
    "environmental",
    "safety",
    "layout",
    "process",
    "other",
)


def _words(*patterns: str) -> re.Pattern[str]:
    return re.compile(r"(?<![\w-])(?:" + "|".join(patterns) + r")(?![\w-])", re.IGNORECASE)


# In priority order. Each pattern is matched as whole words.
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "process",
        _words(
            r"commit(?:s|ted)?",
            r"git",
            r"repository",
            r"version control",
            r"pull request",
            r"submit(?:ted)?",
            r"submission",
            r"deadline",
            r"code review",
        ),
    ),
    (
        "safety",
        _words(
            r"safety",
            r"hazard(?:s|ous)?",
            r"injur(?:y|ies)",
            r"electric shock",
            r"shock hazard",
            r"fire",
            r"burns?",
            r"explosive",
            r"atex",
            r"esd",
            r"electrostatic discharge",
            r"protective",
            r"personnel",
            r"before touching",
        ),
    ),
    (
        "layout",
        _words(
            r"pcbs?",
            r"printed[- ]circuit boards?",
            r"(?:board|pcb) layout",
            r"layouts?",
            r"solder(?:ing|ed)?",
            r"solder (?:mask|paste)",
            r"thermal pads?",
            r"exposed pads?",
            r"(?:pcb |solder(?:ing)? |corner )?pads?",
            r"copper(?: planes?| layers?| pours?| wires?| areas?)?",
            r"ground planes?",
            r"planes?",
            r"traces?",
            r"vias?",
            r"routing",
            r"footprints?",
            r"keep[- ]out",
            r"heat ?sinks?",
            r"air ?flow(?! temperatures?)",
            r"air movement",
            r"board (?:space|thickness|edge|area|design)",
            r"stencil",
        ),
    ),
    (
        "electrical",
        _words(
            r"voltages?",
            r"supply",
            r"supplies",
            r"currents?",
            r"power",
            r"resistors?",
            r"resistance",
            r"capacitors?",
            r"capacitance",
            r"inductors?",
            r"pull-?ups?",
            r"pull-?downs?",
            r"gnd",
            r"vdd",
            r"v\+",
            r"grounded",
            r"grounding",
            r"short[- ]circuit",
            r"bypass capacitors?",
            r"decoupling",
        ),
    ),
    (
        "environmental",
        _words(
            r"(?:operating|storage|ambient|junction|free-air|case|board) temperatures?",
            r"environment(?:al)? temperatures?",
            r"temperatures?\b[^.]{0,40}\b(?:below|above|between|from|up to|"
            r"range|exceed|withstand|stay)",
            r"withstand",
            r"humidity",
            r"vibration",
            r"shock",
            r"storage",
            r"ambient",
            r"moisture",
            r"altitude",
            r"enclosure",
            r"ip\d\d\w*",
            r"materials?",
            r"corrosion",
            r"free-air",
            r"junction",
        ),
    ),
    (
        "performance",
        _words(
            r"speed",
            r"fast(?:er)?",
            r"timing",
            r"accuracy",
            r"accurate",
            r"precision",
            r"resolution",
            r"response time",
            r"latency",
            r"conversion time",
            r"throughput",
            r"within \d+(?:\.\d+)? ?(?:ms|s|µs|us|seconds?)",
            r"averaging",
            r"noise",
            r"frequency",
            r"clock (?:frequency|speed|rate)",
            r"(?:setup|hold) times?",
            r"respond(?:s)? within",
        ),
    ),
    (
        "interface",
        _words(
            r"i2c",
            r"i ?2 ?c",
            r"smbus",
            r"spi",
            r"uart",
            r"usb",
            r"bus(?:es)?",
            r"pins?",
            r"sda",
            r"scl",
            r"alert",
            r"add0",
            r"address(?:es)?",
            r"protocols?",
            r"registers?",
            r"api",
            r"(?:as|in) json",
            r"json (?:format|object|schema|fields?)",
            r"data formats?",
            r"bluetooth(?: low energy)?",
            r"modbus",
            r"rs-485",
            r"connectors?",
            r"signals?",
            r"communicat(?:e|es|ion)",
            r"general-call",
        ),
    ),
    (
        "functional",
        _words(
            r"screens?",
            r"buttons?",
            r"components?",
            r"display(?:s|ed)?",
            r"shows?",
            r"lists?",
            r"users?",
            r"app",
            r"features?",
            r"tap(?:ping)?",
            r"press(?:ed)?",
            r"chips?",
            r"indicator",
            r"menu",
            r"page",
        ),
    ),
)
# "board" alone is too common ("the board is placed horizontally" in a test set-up), so it
# counts as layout only as the object of a board-design instruction.
_BOARD_DESIGN = re.compile(
    r"^\s*(?:miniaturize|design|shrink|stack|panelize)\b.*\bboard\b", re.IGNORECASE
)
# Placing a part relative to pins, sources or other parts is a layout rule: "Place the
# bypass capacitor as close as possible to the supply pins", "Keep the sensor away from".
_PLACEMENT = re.compile(
    r"^\s*(?:place|locate|position|keep|put)\b.*\b(?:close(?: as possible)? to|near|"
    r"next to|away from|underneath|directly under)\b",
    re.IGNORECASE,
)


def rule_type(text: str) -> str | None:
    """The type the rules give `text`, or None when no rule matches."""
    if is_process_instruction(text):
        return "process"
    for name, pattern in RULES:
        if name == "layout" and (_BOARD_DESIGN.search(text) or _PLACEMENT.search(text)):
            return "layout"
        if pattern.search(text):
            return name
    return None


def assign_type(text: str, fallback: str) -> str:
    """Rule type first; otherwise `fallback` (the model's or the unit's type), if valid."""
    return rule_type(text) or (fallback if fallback in TYPES else "other")
