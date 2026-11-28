"""A synthetic pair of extracted requirement tables (older and newer revision) with known
differences, written for this repository."""

from app.compare import Side


def side(code: str, kind: str, type_: str, page: int, quote: str, section: str) -> Side:
    return Side(
        id=int(code[-3:]),
        code=code,
        kind=kind,
        type=type_,
        page=page,
        quote=quote,
        section=section,
    )


AMR = "absolute maximum ratings"
ROC = "recommended operating conditions"

OLD = [
    side("REQ-001", "limit", "electrical", 5, "Supply voltage V+ -0.3 6 V", AMR),
    side(
        "REQ-002", "limit", "environmental", 5, "Operating junction temperature, TJ -55 150 °C", AMR
    ),
    side(
        "REQ-003",
        "limit",
        "electrical",
        5,
        "V+ Supply voltage, TA = -55 °C to 150 °C 1.8 3.3 5.5 V",
        ROC,
    ),
    side(
        "REQ-004",
        "obligation",
        "electrical",
        33,
        "Use a bypass capacitor of 100 nF between V+ and GND.",
        "design requirements",
    ),
    side(
        "REQ-005",
        "obligation",
        "layout",
        36,
        "The thermal pad must be soldered to the ground plane of the PCB.",
        "layout guidelines",
    ),
    side(
        "REQ-006",
        "obligation",
        "interface",
        20,
        "Do not perform any I2C writes until programming is complete.",
        "programming the eeprom",
    ),
    side(
        "REQ-007",
        "obligation",
        "functional",
        18,
        "Avoid using the device to perform temperature conversions during EEPROM programming.",
        "programming the eeprom",
    ),
    side(
        "REQ-008",
        "obligation",
        "layout",
        36,
        "Use PCB with minimal thickness and cover the bottom of the PCB with a copper plane.",
        "layout guidelines",
    ),
]

NEW = [
    side("REQ-001", "limit", "electrical", 5, "Supply voltage V+ -0.3 6 V", AMR),
    side(
        "REQ-002", "limit", "environmental", 5, "Operating junction temperature, TJ -55 155 °C", AMR
    ),
    side(
        "REQ-003",
        "limit",
        "electrical",
        5,
        "V+ Supply voltage, TJ = -55 °C to 150 °C 1.8 3.3 5.5 V",
        ROC,
    ),
    side(
        "REQ-004",
        "obligation",
        "electrical",
        33,
        "Use a bypass capacitor of 100 pF between V+ and GND.",
        "design requirements",
    ),
    side(
        "REQ-005",
        "obligation",
        "layout",
        36,
        "The thermal pad should be soldered to the ground plane of the PCB.",
        "layout guidelines",
    ),
    # The same row, moved one page on and numbered differently.
    side(
        "REQ-009",
        "obligation",
        "interface",
        21,
        "Do not perform any I2C writes until programming is complete.",
        "programming the eeprom",
    ),
    # A new ratings row next to a similar old one.
    side(
        "REQ-010",
        "limit",
        "electrical",
        5,
        "V+ Supply voltage, TA = -55 °C to 70 °C 1.7 5.5 V",
        ROC,
    ),
    # REQ-008 split into two rows.
    side(
        "REQ-011",
        "obligation",
        "layout",
        37,
        "Use PCB with minimal thickness.",
        "layout guidelines",
    ),
    side(
        "REQ-012",
        "obligation",
        "layout",
        37,
        "Cover the bottom of the PCB with a copper plane.",
        "layout guidelines",
    ),
    side(
        "REQ-013",
        "obligation",
        "electrical",
        33,
        "Add a 10-kΩ pullup resistor on the ALERT pin.",
        "design requirements",
    ),
]

# (old code, new code) -> class; None for an added or removed row.
EXPECTED = {
    ("REQ-001", "REQ-001"): "unchanged",
    ("REQ-006", "REQ-009"): "unchanged",
    ("REQ-002", "REQ-002"): "changed",
    ("REQ-003", "REQ-003"): "changed",
    ("REQ-004", "REQ-004"): "changed",
    ("REQ-005", "REQ-005"): "changed",
    # The split: the closer half is the match, the other half is added and names REQ-008.
    ("REQ-008", "REQ-012"): "changed",
    (None, "REQ-011"): "added",
    (None, "REQ-010"): "added",
    (None, "REQ-013"): "added",
    ("REQ-007", None): "removed",
}
