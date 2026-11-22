"""Synthetic documents with labelled ground truth, for testing extraction.

All text is written for this repository. Each fixture lists the items that must be
extracted (with their kind and type) and sentences that must not be. `render`
writes the fixture as a PDF.

What counts follows the definition in the README: an *obligation* (the reader is told
to do or not do something, including recommendations and imperatives) or a *limit*
(a specified boundary the product must be operated within, e.g. absolute maximum
ratings and recommended operating conditions). Descriptions of behaviour do not count.
"""

from dataclasses import dataclass, field
from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace


@dataclass(frozen=True)
class Expected:
    sentence: str
    type: str
    kind: str = "obligation"


@dataclass(frozen=True)
class Fixture:
    name: str
    pages: list[list[tuple[str, object]]]
    genuine: list[Expected] = field(default_factory=list)
    not_requirements: list[str] = field(default_factory=list)


READING = Fixture(
    name="reading_no_requirements",
    pages=[
        [
            ("h1", "1 Virtualised lists"),
            (
                "p",
                "A virtualised list renders only the rows that are currently visible. Rows that "
                "scroll out of view are recycled, so memory use stays flat even for thousands of "
                "items.",
            ),
            (
                "p",
                "The component measures each row after it is drawn and keeps an estimate of the "
                "total height, which is why the scroll bar can jump slightly on the first pass.",
            ),
        ],
        [
            ("h1", "2 Props and their defaults"),
            (
                "p",
                "The table lists the props the component accepts and the value used when a prop "
                "is omitted.",
            ),
            (
                "table",
                [
                    ["Prop", "Meaning", "Default"],
                    ["initialBatch", "Rows drawn before the first frame", "10"],
                    ["windowFactor", "Screens of content kept around the viewport", "21"],
                    ["batchDelayMs", "Pause between drawing batches", "50"],
                    ["inverted", "Draws the list from the bottom up", "false"],
                ],
            ),
            (
                "p",
                "Larger windows reduce blank areas during fast scrolling but cost memory. Smaller "
                "batches make the first frame appear sooner.",
            ),
        ],
    ],
)

LAB = Fixture(
    name="lab_assignment",
    pages=[
        [
            ("h1", "Lab 6: Trails"),
            ("h2", "Learning objectives"),
            (
                "p",
                "After this lab, students should be able to explain how list virtualisation works.",
            ),
            ("p", "Students should be able to describe the role of keys in a list."),
            ("h2", "Working method"),
            ("p", "Commit after every task and push your work at the end of the session."),
        ],
        [
            ("h1", "Task 1: Trail list"),
            ("p", "Create a TrailList screen that shows all trails from trails.json."),
            ("p", "Each row shows the trail name and its length in kilometres."),
            ("p", "Let the user open a trail by tapping its row."),
            ("p", "You should add a loading indicator while the trails are loading."),
            ("p", "The data has the following shape:"),
            ("code", "type Trail = { id: string; name: string; lengthKm: number }"),
            ("code", "const trails = require('./trails.json');"),
            (
                "p",
                "Tip: a FlatList is a good fit here, but a ScrollView also works for short lists.",
            ),
        ],
        [
            ("h1", "Task 2: Filter chips"),
            (
                "p",
                "Build a FilterChip component with selected, label and onPress props, and give it "
                "accessibilityState.",
            ),
            ("p", "Put the TrailCard component in its own file, src/components/TrailCard.tsx."),
            ("p", "Add an activeOpacity of 0.7 to every pressable trail card."),
            ("p", "The list must update within 100 ms after a chip is pressed."),
            ("p", "Below the summary row, add a row of chips: All, Easy, Medium and Hard."),
            ("p", "When a filter matches nothing, the empty state says so."),
            ("p", "Before the next lab, read the chapter on navigation."),
            ("p", "Test long presses and the chips on your phone, not only in the simulator."),
            ("p", "Check your work with the following steps."),
            (
                "table",
                [
                    ["Step", "Input", "Expected output"],
                    ["1", "Long press on a trail card", "A context menu opens"],
                    ["2", "Tap the Easy chip", "Only easy trails remain"],
                ],
            ),
        ],
        [
            ("h1", "Task 3: Sensor board"),
            ("p", "The sensor board must be powered from 3.3 V and draw no more than 50 mA."),
            ("p", "The enclosure must withstand temperatures from 0 to 50 °C."),
            ("p", "Disconnect the USB cable before touching the board."),
            ("p", "The app must communicate with the board over Bluetooth Low Energy."),
            ("h2", "If something goes wrong"),
            (
                "table",
                [
                    ["Problem", "Fix"],
                    ["The list stays empty", "Check that trails.json is in the project root"],
                    ["Images do not appear", "Make sure the device has network access"],
                ],
            ),
            ("h2", "Wrapping up"),
            ("p", "The examples reuse the trail app from Labs 2 and 3."),
            ("p", "Your project now covers the list screen part of the course project."),
            ("p", "In this lab you built a filterable list screen."),
        ],
    ],
    genuine=[
        Expected(
            "Commit after every task and push your work at the end of the session.", "process"
        ),
        Expected("Create a TrailList screen that shows all trails from trails.json.", "functional"),
        Expected("Let the user open a trail by tapping its row.", "functional"),
        Expected("You should add a loading indicator while the trails are loading.", "functional"),
        Expected(
            "Build a FilterChip component with selected, label and onPress props, and give it "
            "accessibilityState.",
            "functional",
        ),
        Expected(
            "Put the TrailCard component in its own file, src/components/TrailCard.tsx.",
            "functional",
        ),
        Expected("Add an activeOpacity of 0.7 to every pressable trail card.", "functional"),
        Expected("The list must update within 100 ms after a chip is pressed.", "performance"),
        Expected(
            "Below the summary row, add a row of chips: All, Easy, Medium and Hard.", "functional"
        ),
        Expected("Before the next lab, read the chapter on navigation.", "process"),
        Expected(
            "Test long presses and the chips on your phone, not only in the simulator.", "process"
        ),
        Expected(
            "The sensor board must be powered from 3.3 V and draw no more than 50 mA.", "electrical"
        ),
        Expected("The enclosure must withstand temperatures from 0 to 50 °C.", "environmental"),
        Expected("Disconnect the USB cable before touching the board.", "safety"),
        Expected("The app must communicate with the board over Bluetooth Low Energy.", "interface"),
    ],
    not_requirements=[
        "After this lab, students should be able to explain how list virtualisation works.",
        "Students should be able to describe the role of keys in a list.",
        "Each row shows the trail name and its length in kilometres.",
        "When a filter matches nothing, the empty state says so.",
        "type Trail = { id: string; name: string; lengthKm: number }",
        "const trails = require('./trails.json');",
        "Tip: a FlatList is a good fit here, but a ScrollView also works for short lists.",
        "Long press on a trail card",
        "Tap the Easy chip",
        "Check that trails.json is in the project root",
        "Make sure the device has network access",
        "The examples reuse the trail app from Labs 2 and 3.",
        "Your project now covers the list screen part of the course project.",
        "In this lab you built a filterable list screen.",
    ],
)

DATASHEET = Fixture(
    name="datasheet",
    pages=[
        [
            ("h1", "TS-100 Digital Temperature Sensor"),
            ("h2", "1 Features"),
            ("bullet", "High-accuracy digital temperature sensor"),
            ("bullet", "Accuracy of 0.1 °C from 0 °C to 60 °C"),
            ("bullet", "I2C and SMBus compatible interface"),
            ("bullet", "Programmable alert limits"),
            ("h2", "2 Applications"),
            ("bullet", "Cold chain monitoring"),
            ("bullet", "Medical thermometers"),
            ("h2", "3 Description"),
            (
                "p",
                "The TS-100 is a low-power digital temperature sensor that replaces thermistors "
                "and RTDs in precision designs. It reports temperature with 16-bit resolution over "
                "a two-wire interface.",
            ),
            (
                "p",
                "To keep this accuracy, do not place the TS-100 next to power components.",
            ),
        ],
        [
            ("h2", "4 Revision History"),
            (
                "table",
                [
                    ["Revision", "Date", "Change"],
                    ["B", "2026-02-01", "Changed the alert timing diagram"],
                    ["A", "2025-06-10", "Initial release"],
                ],
            ),
            ("h2", "5 Specifications"),
            ("h2", "5.1 Absolute Maximum Ratings"),
            (
                "table",
                [
                    ["Parameter", "MIN", "MAX", "UNIT"],
                    ["Supply voltage, V+", "-0.3", "6", "V"],
                    ["Voltage at SCL, SDA and ALERT", "-0.3", "6", "V"],
                    ["Junction temperature", "-55", "150", "°C"],
                    ["Storage temperature", "-65", "150", "°C"],
                ],
            ),
            ("p", "(1) Stresses beyond those listed may cause permanent damage to the device."),
            ("h2", "5.2 Recommended Operating Conditions"),
            (
                "table",
                [
                    ["Parameter", "MIN", "NOM", "MAX", "UNIT"],
                    ["Supply voltage, V+", "1.8", "3.3", "5.5", "V"],
                    ["Operating ambient temperature", "-40", "", "125", "°C"],
                ],
            ),
            ("h2", "5.3 Electrical Characteristics"),
            (
                "table",
                [
                    ["Parameter", "TYP", "UNIT"],
                    ["Quiescent current", "3.5", "µA"],
                    ["Conversion time", "15.5", "ms"],
                ],
            ),
        ],
        [
            ("h2", "6 Detailed Description"),
            ("h2", "6.1 Overview"),
            (
                "p",
                "The TS-100 measures temperature with an internal diode and converts it with a "
                "sigma-delta converter. The device acknowledges each data byte it receives.",
            ),
            (
                "p",
                "When the temperature exceeds the high limit, the ALERT pin goes low until the "
                "temperature falls below the low limit.",
            ),
            ("p", "The TS-100 never stretches the clock."),
            ("p", "Wait at least 2 ms after power-up before the first read."),
            ("h2", "6.2 Register Map"),
            (
                "table",
                [
                    ["Address", "Register", "Reset", "Description"],
                    ["00h", "TEMP_RESULT", "8000h", "Temperature result"],
                    ["01h", "CONFIG", "0220h", "Configuration"],
                ],
            ),
            (
                "table",
                [
                    ["Bit", "Field", "Type", "Reset", "Description"],
                    ["15:12", "REV[3:0]", "R", "0h", "Indicates the revision number"],
                ],
            ),
        ],
        [
            ("h2", "7 Application and Implementation"),
            ("h2", "7.1 Layout Guidelines"),
            (
                "p",
                "Place the TS-100 as close as possible to the heat source that is to be measured.",
            ),
            ("p", "A 0.1-µF bypass capacitor is recommended between V+ and GND."),
            ("p", "Avoid pullup resistors smaller than 2 kOhm on the SDA and ALERT pins."),
            ("p", "To prevent oxidation, cover any exposed copper with solder mask."),
            ("p", "Do not route high-speed digital traces underneath the device."),
            ("p", "The supply voltage must not drop below 1.8 V during a conversion."),
            ("p", "For best accuracy solder the exposed pad to a large ground plane."),
            ("p", "Extend the ground plane under the whole package."),
            ("p", "Pullup resistors of 5 kOhm should be used on long buses."),
            ("p", "A thin board lets the sensor follow temperature changes faster."),
            ("p", "For low thermal mass the TS-100 comes in a small package."),
            ("h2", "7.2 Troubleshooting"),
            (
                "table",
                [
                    ["Problem", "Cause", "Fix"],
                    ["No acknowledge", "Wrong address", "Check the ADD0 connection"],
                    ["Result stays at 8000h", "No conversion yet", "Wait for the first conversion"],
                ],
            ),
        ],
        [
            ("h2", "8 Device and Documentation Support"),
            ("h2", "8.1 Related Documentation"),
            ("bullet", "TS-100 evaluation module user guide"),
            ("bullet", "Application note: precise temperature measurement"),
            ("h2", "8.2 Trademarks"),
            ("p", "All trademarks are the property of their respective owners."),
            ("h2", "Important Notice and Disclaimer"),
            (
                "p",
                "Designers must validate the suitability of this product for their application and "
                "are solely responsible for all safety requirements.",
            ),
            ("p", "The manufacturer provides technical data as is and disclaims all warranties."),
        ],
    ],
    genuine=[
        Expected("Supply voltage, V+ -0.3 6 V", "electrical", "limit"),
        Expected("Voltage at SCL, SDA and ALERT -0.3 6 V", "electrical", "limit"),
        Expected("Junction temperature -55 150 °C", "environmental", "limit"),
        Expected("Storage temperature -65 150 °C", "environmental", "limit"),
        Expected("Supply voltage, V+ 1.8 3.3 5.5 V", "electrical", "limit"),
        Expected("Operating ambient temperature -40 125 °C", "environmental", "limit"),
        Expected(
            "Place the TS-100 as close as possible to the heat source that is to be measured.",
            "other",
        ),
        Expected("A 0.1-µF bypass capacitor is recommended between V+ and GND.", "electrical"),
        Expected(
            "Avoid pullup resistors smaller than 2 kOhm on the SDA and ALERT pins.", "electrical"
        ),
        Expected("To prevent oxidation, cover any exposed copper with solder mask.", "other"),
        Expected("Do not route high-speed digital traces underneath the device.", "other"),
        Expected("The supply voltage must not drop below 1.8 V during a conversion.", "electrical"),
        # In descriptive sections, which are screened rather than read in full.
        Expected(
            "To keep this accuracy, do not place the TS-100 next to power components.", "other"
        ),
        Expected("Wait at least 2 ms after power-up before the first read.", "performance"),
        # An instruction after a purpose phrase without a comma, a verb outside the verb
        # list, and a passive recommendation.
        Expected("For best accuracy solder the exposed pad to a large ground plane.", "other"),
        Expected("Extend the ground plane under the whole package.", "other"),
        Expected("Pullup resistors of 5 kOhm should be used on long buses.", "electrical"),
    ],
    not_requirements=[
        "High-accuracy digital temperature sensor",
        "Accuracy of 0.1 °C from 0 °C to 60 °C",
        "I2C and SMBus compatible interface",
        "Programmable alert limits",
        "Cold chain monitoring",
        "Medical thermometers",
        "The TS-100 is a low-power digital temperature sensor that replaces thermistors and RTDs",
        "It reports temperature with 16-bit resolution over a two-wire interface.",
        "B 2026-02-01 Changed the alert timing diagram",
        "A 2025-06-10 Initial release",
        "(1) Stresses beyond those listed may cause permanent damage to the device.",
        "Quiescent current 3.5 µA",
        "Conversion time 15.5 ms",
        "The TS-100 measures temperature with an internal diode",
        "The device acknowledges each data byte it receives.",
        "When the temperature exceeds the high limit, the ALERT pin goes low",
        "00h TEMP_RESULT 8000h Temperature result",
        "01h CONFIG 0220h Configuration",
        "15:12 REV[3:0] R 0h Indicates the revision number",
        "A thin board lets the sensor follow temperature changes faster.",
        "For low thermal mass the TS-100 comes in a small package.",
        "The TS-100 never stretches the clock.",
        "No acknowledge Wrong address Check the ADD0 connection",
        "Result stays at 8000h No conversion yet Wait for the first conversion",
        "TS-100 evaluation module user guide",
        "Application note: precise temperature measurement",
        "All trademarks are the property of their respective owners.",
        "Designers must validate the suitability of this product for their application",
        "The manufacturer provides technical data as is and disclaims all warranties.",
    ],
)

FIXTURES = [READING, LAB, DATASHEET]


def render(fixture: Fixture, output: Path) -> Path:
    """Write the fixture as a PDF."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    for elements in fixture.pages:
        pdf.add_page()
        for kind, content in elements:
            _render_element(pdf, kind, content)
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output))
    return output


def _render_element(pdf: FPDF, kind: str, content: object) -> None:
    if kind in ("h1", "h2"):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 14 if kind == "h1" else 12)
        pdf.multi_cell(0, 8, str(content), new_x="LMARGIN", new_y="NEXT")
    elif kind == "p":
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5.5, str(content), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    elif kind == "bullet":
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5.5, f"- {content}", new_x="LMARGIN", new_y="NEXT")
    elif kind == "code":
        pdf.set_font("Courier", "", 9)
        pdf.multi_cell(0, 5, str(content), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    elif kind == "table":
        pdf.set_font("Helvetica", "", 9)
        with pdf.table(headings_style=FontFace(emphasis="BOLD")) as table:
            for row in content:  # type: ignore[attr-defined]
                cells = table.row()
                for cell in row:
                    cells.cell(cell)
        pdf.ln(3)
