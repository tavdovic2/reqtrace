"""Generate the synthetic evaluation datasheet (a fictional pressure transmitter).

The repository ships no third-party PDFs, so evaluation runs against this generated
document until a real datasheet is placed in samples/. Every fact in it is invented.

Usage: python eval/make_sample_pdf.py [output.pdf]
"""

import sys
from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace

DEFAULT_OUTPUT = Path(__file__).parent / "data" / "nx310_datasheet.pdf"

Element = tuple[str, object]

PAGES: list[list[Element]] = [
    [
        ("title", "NX-310 Industrial Pressure Transmitter"),
        ("p", "Datasheet, revision 1.4. Norvale Instruments (fictional manufacturer)."),
        ("h1", "1 Overview"),
        (
            "p",
            "The NX-310 is a piezoresistive pressure transmitter for liquids and gases in "
            "industrial process control. A thin-film sensing element welded into a 316L "
            "stainless steel body gives a fully welded, oil-free measuring cell.",
        ),
        (
            "p",
            "Key features: measuring ranges from 0 to 10 bar up to 0 to 400 bar; accuracy of "
            "±0.1 % of full scale; analog 4 to 20 mA output with HART, 0 to 10 V output, or "
            "Modbus RTU over RS-485; protection class up to IP69K; optional ATEX approval for "
            "hazardous areas.",
        ),
        ("h2", "1.1 Applications"),
        (
            "p",
            "Typical applications are hydraulic power units, water and waste water treatment, "
            "air compressors, refrigeration and HVAC systems, and test benches.",
        ),
    ],
    [
        ("h1", "2 Ordering Information"),
        (
            "p",
            "The part number is built from the output code, the range code and the process "
            "connection code, for example NX-310-A-100-G.",
        ),
        (
            "table",
            [
                ["Code", "Meaning"],
                ["A", "Output 4 to 20 mA, 2-wire, with HART 7"],
                ["V", "Output 0 to 10 V, 3-wire"],
                ["M", "Modbus RTU over RS-485, 4-wire"],
                [
                    "010 / 025 / 100 / 250 / 400",
                    "Measuring range 0 to 10 / 25 / 100 / 250 / 400 bar",
                ],
                ["G", "Process connection G1/4 (ISO 228-1)"],
                ["N", "Process connection 1/4 NPT"],
                ["X (suffix)", "ATEX intrinsically safe version, only with output A"],
            ],
        ),
        (
            "p",
            "Example: NX-310-A-100-G is a 4 to 20 mA transmitter with a range of 0 to 100 bar "
            "and a G1/4 process connection. Delivery time for standard variants is two weeks.",
        ),
    ],
    [
        ("h1", "3 Absolute Maximum Ratings"),
        (
            "p",
            "Stresses beyond the values below may cause permanent damage to the device. "
            "Operation at these limits is not implied.",
        ),
        (
            "table",
            [
                ["Parameter", "Value"],
                ["Supply voltage", "36 V DC"],
                ["Overpressure", "2 x full scale, at most 600 bar"],
                ["Burst pressure", "5 x full scale"],
                ["Storage temperature", "-40 to +100 °C"],
                ["Media temperature", "-40 to +125 °C"],
            ],
        ),
        ("h1", "4 Electrical Specifications"),
        (
            "table",
            [
                ["Parameter", "Condition", "Value"],
                ["Supply voltage", "Output A (4 to 20 mA)", "9 to 32 V DC"],
                ["Supply voltage", "Output V (0 to 10 V)", "15 to 32 V DC"],
                ["Supply voltage", "Output M (Modbus RTU)", "8 to 30 V DC"],
                ["Current consumption", "Output M, typical", "12 mA"],
                ["Insulation resistance", "at 500 V DC", "> 100 MOhm"],
            ],
        ),
        (
            "p",
            "The maximum load resistance for output A is (Vs - 9 V) / 0.02 A. All variants "
            "include reverse polarity protection on the supply input.",
        ),
        (
            "p",
            "A 0.1-µF ceramic capacitor is recommended between the supply input and ground, "
            "placed as close to the connector as possible.",
        ),
    ],
    [
        ("h1", "5 Performance"),
        (
            "table",
            [
                ["Parameter", "Value"],
                ["Accuracy (BFSL, at 25 °C)", "±0.1 % FS"],
                ["Long-term stability", "±0.1 % FS per year"],
                ["Thermal error in compensated range", "±0.015 % FS/K"],
                ["Compensated temperature range", "-20 to +85 °C"],
                ["Response time (10 to 90 %)", "< 2 ms"],
                ["Internal sampling rate, output M", "100 Hz"],
            ],
        ),
        (
            "p",
            "The transmitter shall reach its specified accuracy within 500 ms after power-up. "
            "During this start-up period the analog output is held at 3.6 mA.",
        ),
        (
            "p",
            "If the sensing element fails, the 4 to 20 mA output shall go to a failure current "
            "of 21.5 mA so that the control system can detect the fault.",
        ),
    ],
    [
        ("h1", "6 Communication Interface"),
        ("h2", "6.1 HART"),
        (
            "p",
            "Variant A supports the HART 7 protocol superimposed on the 4 to 20 mA loop. Range, "
            "damping and tag can be configured with any HART 7 compatible handheld.",
        ),
        ("h2", "6.2 Modbus RTU"),
        (
            "p",
            "Variant M communicates over a half-duplex RS-485 bus. Supported baud rates are 9600 "
            "to 115200 baud. The factory default setting is 19200 baud with 8 data bits, even "
            "parity and 1 stop bit. The default slave address is 247; addresses from 1 to 247 "
            "can be configured.",
        ),
        (
            "p",
            "The device shall respond to a valid Modbus request within 10 ms. The bus "
            "termination resistor of 120 Ohm is not built in and must be fitted externally.",
        ),
        (
            "p",
            "Up to four transmitters can share one RS-485 segment without a repeater.",
        ),
        (
            "table",
            [
                ["Register", "Content", "Type"],
                ["30001", "Pressure in bar", "float32, read only"],
                ["30003", "Sensor temperature in °C", "float32, read only"],
                ["40001", "Slave address", "uint16, read/write"],
                ["40002", "Baud rate code", "uint16, read/write"],
                ["40010", "Zero adjustment command", "uint16, write only"],
            ],
        ),
    ],
    [
        ("h1", "7 Environmental and Mechanical Data"),
        (
            "table",
            [
                ["Parameter", "Value"],
                ["Operating ambient temperature", "-40 to +85 °C"],
                ["Ingress protection, M12 connector", "IP67"],
                ["Ingress protection, cable gland option", "IP69K"],
                ["Vibration", "20 g, 10 to 2000 Hz, IEC 60068-2-6"],
                ["Shock", "100 g, 11 ms, IEC 60068-2-27"],
                ["Housing material", "Stainless steel 316L"],
                ["Weight", "approx. 120 g"],
            ],
        ),
        (
            "p",
            "The device meets the EMC requirements of EN 61326-1 for industrial environments. "
            "Versions with the cable gland option shall withstand high-pressure washdown with "
            "water at 80 °C.",
        ),
    ],
    [
        ("h1", "8 Safety and Certifications"),
        (
            "p",
            "The X variant is approved as ATEX II 1G Ex ia IIC T4 Ga. Intrinsically safe "
            "variants shall only be powered through a certified barrier with Ui <= 28 V, "
            "Ii <= 93 mA and Pi <= 0.66 W.",
        ),
        (
            "p",
            "The 4 to 20 mA variant is suitable for use in safety functions up to SIL 2 "
            "according to IEC 61508. All variants carry the CE marking and comply with RoHS.",
        ),
        ("h2", "8.1 Installation"),
        (
            "p",
            "The G1/4 process connection shall be tightened with a torque of 25 Nm. Do not "
            "install the transmitter where pressure spikes can exceed the overpressure limit; "
            "use a snubber in systems with water hammer.",
        ),
    ],
    [
        ("h1", "9 Pin Assignment"),
        (
            "table",
            [
                ["M12 pin", "Output A", "Output V", "Output M"],
                ["1", "+Vs", "+Vs", "+Vs"],
                ["2", "not connected", "Signal out", "RS-485 B"],
                ["3", "Loop return", "0 V", "0 V"],
                ["4", "not connected", "not connected", "RS-485 A"],
            ],
        ),
        ("h1", "10 Calibration and Maintenance"),
        (
            "p",
            "The recommended recalibration interval is 24 months. A zero adjustment can be "
            "triggered with the magnetic button on the housing or by writing to register 40010. "
            "The device contains no user-serviceable parts and the housing must not be opened.",
        ),
        ("h1", "11 Revision History"),
        (
            "table",
            [
                ["Revision", "Date", "Change"],
                ["1.2", "2025-03-10", "Added Modbus RTU variant"],
                ["1.3", "2025-09-02", "Added IP69K cable gland option"],
                ["1.4", "2026-02-18", "Corrected thermal error specification"],
            ],
        ),
    ],
]


def render(output: Path) -> Path:
    """Write the datasheet PDF to `output`."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    for elements in PAGES:
        pdf.add_page()
        for kind, content in elements:
            _render_element(pdf, kind, content)
    output.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(output))
    return output


def _render_element(pdf: FPDF, kind: str, content: object) -> None:
    if kind == "title":
        pdf.set_font("Helvetica", "B", 18)
        pdf.multi_cell(0, 10, str(content), new_x="LMARGIN", new_y="NEXT")
    elif kind in ("h1", "h2"):
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 14 if kind == "h1" else 12)
        pdf.multi_cell(0, 8, str(content), new_x="LMARGIN", new_y="NEXT")
    elif kind == "p":
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5.5, str(content), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
    elif kind == "table":
        pdf.set_font("Helvetica", "", 9)
        with pdf.table(headings_style=FontFace(emphasis="BOLD")) as table:
            for row in content:  # type: ignore[attr-defined]
                cells = table.row()
                for cell in row:
                    cells.cell(cell)
        pdf.ln(3)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    print(f"Wrote {render(target)}")
