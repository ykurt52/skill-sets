---
name: sch-review
description: Review the schematic of a KiCad or Altium hardware project - mechanical checks on the netlist (open supply pins, missing decoupling, missing pull resistors, undriven inputs, tied outputs, capacitor voltage rating, shorted parts, schematic against board drift) followed by a datasheet pass per IC (supply range, decoupling, strap and enable pins, unused pins, absolute maximum ratings, external parts of regulators). Use when the user asks for a schematic review, şematik inceleme, design review of a circuit, a check of a design against datasheets, or invokes /sch-review. Needs the hw-read skill for reading the project.
---

# sch-review: schematic review

Two passes. A script finds what can be found from the netlist alone. Then you
compare each IC with its datasheet. The result is a list of problems with
their location, nothing else.

## Steps

1. **Read the project** with the hw-read skill (`/hw-read:hw-read`), every
   time, so the data is current. If hw-read is not installed, tell the user
   to install it from the same marketplace and stop. Note the folder it
   wrote to (`<project folder>/.hwread/` by default).

2. **Run the checks** on that folder:
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/schcheck.py" "<folder with design.json>"
   ```
   It prints findings and a context section, and saves `sch-checks.md` and
   `sch-findings.json` next to `design.json`.

3. **Triage the findings.** Each one is a candidate, not a verdict. Open
   `design.json`, look at the nets and parts it names, and decide:
   - confirmed: keep it;
   - explained by the design (pull resistors on a plug-in module, a supply
     pin that is really an output, a test net): drop it;
   - depends on something you cannot see: ask the user for that one
     condition (supply voltage, operating mode, variant) before deciding.
   The `Basis` text says what the check assumed. A finding built on a net
   name or a pin name is weaker than one built on pin types.

4. **Settle the external levels.** The context lists, per IC, the pins that
   reach a connector (`external pin` lines) and the supply voltage of the
   part. Where a level or a supply voltage is `unknown`, the script could
   not check that pin against the supply. Ask the user for those voltages
   in one list, write them into `sch-review.json` next to `design.json` as
   `{"net_voltages": {"<net>": <volt>}}`, and run step 2 again. A pin fed
   from a higher rail through a series resistor only, with no resistor to
   ground on the pin side, is the pattern to look for.

5. **Datasheet pass**, one IC at a time, in the order of the context
   section. For each IC:
   - Get the datasheet: the URL in the context, otherwise search by MPN.
     Download the PDF into a temporary folder outside the project. If the
     site refuses or times out, do not work around it: ask the user to
     drop the PDF into a folder, or list the IC under "not checked" with
     the open points that need the datasheet.
   - Read it by page. If pages cannot be rendered directly, extract the
     text with a PDF library (PyMuPDF or pypdf) and render only the pages
     with drawings (pin assignment, application circuit) as images.
     Pinouts and reference circuits are drawings; text extraction alone
     misses them.
   - Read the pin description, absolute maximum ratings, recommended
     operating conditions and the typical application circuit.
   - Compare with the design, using the context lines and `design.json`:
     - every supply pin: rail voltage inside the recommended range;
     - decoupling and bulk capacitors: value and count against the
       recommendation;
     - every enable, reset, boot, mode, address and strap pin: the state
       the circuit sets, and whether that is the intended mode;
     - unused pins: tied or left open as the datasheet asks;
     - every pin: the highest voltage its net can carry against the
       absolute maximum;
     - regulators: output voltage computed from the feedback divider
       values, inductor, bootstrap and compensation parts, input and
       output capacitance;
     - open-drain outputs: pull resistor present and its value sensible
       for the rail and the sink current.
   - Never state a datasheet value from memory. Every value you report
     carries the page or section it came from. If the datasheet does not
     say, write that.

6. **Across parts.** After the per-IC pass, check what no single datasheet
   shows: logic levels between ICs on different rails, total load on each
   regulator against its rating when the numbers are available, LED and
   indicator currents, and parts marked not fitted that another part
   depends on.

7. **Report.** Give the user only the problems, most severe first. Each
   entry: location (reference, pin, net, sheet), what the design has, what
   the datasheet or rule says with its page, and the action. Mark each as
   `verified` (datasheet or design data read) or `inferred` (from names or
   conventions). End with the ICs that were not checked and why. If there
   are no problems, say so in one line. Save the same text as
   `sch-review.md` next to `design.json`.

## Large designs

With more than about 15 ICs, do the mechanical pass for the whole design,
then ask which sheets or ICs to take first, or go sheet by sheet. Do not
skim all datasheets to finish faster.

## Checks the script runs

| Id | Finds |
| --- | --- |
| `SUPPLY_PIN_OPEN` | IC supply pin with no connection |
| `NO_DECOUPLING` | IC supply net with no fitted capacitor to ground, also looking one zero-ohm link or inductor away |
| `NO_PULL_RESISTOR` | I2C-named or open-collector net with no fitted resistor to a rail |
| `INPUT_NOT_DRIVEN` | net that has only input pins |
| `OUTPUTS_TIED` | net driven by outputs of more than one part |
| `CAP_VOLTAGE` | capacitor rated below the margin times the rail voltage |
| `PIN_ABOVE_SUPPLY` | IC signal pin on a rail above the supply of the part, directly or through a resistor without a sufficient divider |
| `PART_SHORTED` | two-terminal part with both pins on one net |
| `SINGLE_PIN_NET` | net with a single pin |
| `PINS_OPEN` | IC pins with no net (information only) |
| `BOM_NO_MPN` | fitted parts without a manufacturer part number |
| `SCH_BOARD_DIFF` | differences between schematic and board found by hw-read |

Parts marked not fitted do not count as present. Rail voltages come from
net names such as `3V3`, `+5V`, `1V8_SYS`; a rail with another naming style
is not checked for capacitor voltage.

## Project settings

Optional `sch-review.json` next to `design.json` overrides the defaults at
the top of `scripts/schcheck.py`: net name patterns for ground and power,
pin name patterns, `net_voltages` (voltage of nets whose name does not
carry one), `cap_voltage_margin` (default 1.5), `pin_overvoltage_margin`
(default 0.3 V), `reference_kinds` (reference prefix to part kind) and
`skip_checks`.
