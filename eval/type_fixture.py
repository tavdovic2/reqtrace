"""Labelled requirement sentences for measuring type assignment (written for this
repository). `table_row` marks rows of a ratings table, which are typed without a model.

Usage: python -m eval.run_type_eval
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Labelled:
    sentence: str
    type: str
    table_row: bool = False


TYPE_FIXTURE: list[Labelled] = [
    # layout: PCB, board design, soldering, pads, copper, traces, vias, air flow
    Labelled("Use PCB with minimal thickness.", "layout"),
    Labelled("Cover bottom of the PCB with copper plane.", "layout"),
    Labelled("Place PCB vertically along air flow.", "layout"),
    Labelled("Solder the exposed pad to a large ground plane.", "layout"),
    Labelled("Avoid running traces or vias underneath the sensor.", "layout"),
    Labelled("Place the bypass capacitor as close as possible to the supply pins.", "layout"),
    Labelled("Miniaturize the board to reduce thermal mass.", "layout"),
    # look like layout, but are not
    Labelled("The board is placed horizontally in the climate chamber during the test.", "other"),
    Labelled("Mount the display at eye level for the operator.", "functional"),
    Labelled("The temperature of the circuit board must stay below 85 °C.", "environmental"),
    # electrical
    Labelled("The supply voltage must not exceed 3.6 V.", "electrical"),
    Labelled("Avoid pullup resistors smaller than 2 kOhm on the SDA pin.", "electrical"),
    Labelled("Supply voltage V+ -0.3 6 V", "electrical", table_row=True),
    Labelled("The input current must stay below 10 mA.", "electrical"),
    # environmental
    Labelled("Junction temperature -55 155 °C", "environmental", table_row=True),
    Labelled("Store the device in a dry place with humidity below 60%.", "environmental"),
    Labelled("The enclosure must withstand vibration up to 5 g.", "environmental"),
    # interface
    Labelled("The app must communicate with the board over Bluetooth Low Energy.", "interface"),
    Labelled("Tie the ADD0 pin high to select the second bus address.", "interface"),
    Labelled("Do not perform any I2C writes until programming is complete.", "interface"),
    Labelled("Return the result as JSON with the fields id, name and length.", "interface"),
    # performance
    Labelled("The list must update within 100 ms after a chip is pressed.", "performance"),
    Labelled("Higher averaging numbers are recommended in noisy systems.", "performance"),
    Labelled("The conversion time must not exceed 15.5 ms.", "performance"),
    Labelled("The reported accuracy must be better than 0.1 °C.", "performance"),
    # functional
    Labelled("Create a TrailList screen that shows all trails from trails.json.", "functional"),
    Labelled("Let the user open a trail by tapping its row.", "functional"),
    Labelled("Add a loading indicator while the trails are loading.", "functional"),
    Labelled("Build a FilterChip component with selected, label and onPress props.", "functional"),
    # safety
    Labelled("Disconnect the USB cable before touching the board.", "safety"),
    Labelled("Handle the device with ESD precautions.", "safety"),
    # process
    Labelled("Commit after every task and push your work at the end of the session.", "process"),
    Labelled("Test long presses on your phone, not only in the simulator.", "process"),
    # other
    Labelled("Designers must validate the suitability of this product for their use.", "other"),
    Labelled("Keep the original packaging until the device is installed.", "other"),
]
