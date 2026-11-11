# sch-review

Reviews the schematic of a KiCad or Altium project in two passes:

1. A script checks the netlist: open supply pins, missing decoupling,
   missing pull resistors, undriven inputs, tied outputs, capacitor voltage
   rating, shorted parts, single-pin nets, schematic against board drift.
2. The agent reads the datasheet of each IC and compares supply range,
   decoupling, strap and enable pins, unused pins, absolute maximum ratings
   and the external parts of regulators with the design.

The result lists only the problems, each with its location and the
datasheet page it rests on.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install hw-read@skill-sets
/plugin install sch-review@skill-sets
```

sch-review reads the `design.json` that [hw-read](../hw-read/README.md)
writes, so hw-read must be installed too. Requires Python 3.8 or newer, no
extra packages.

## Run

As a skill, inside the project folder:

```
/sch-review:sch-review
```

The mechanical pass on its own:

```
python skills/sch-review/scripts/schcheck.py <folder with design.json> [options]
```

| Option | Effect |
| --- | --- |
| `--config FILE` | Settings file. Default: `sch-review.json` next to `design.json` |
| `--out DIR` | Output folder. Default: the folder of `design.json` |

Example:

```
python skills/hw-read/scripts/hwread.py ~/boards/sensor-hub
python skills/sch-review/scripts/schcheck.py ~/boards/sensor-hub
```

## Input

`design.json` from hw-read (schema `hw-read/1`). The path can be the file,
its folder, or the project folder that contains `.hwread/`.

## Output

| File | Content |
| --- | --- |
| `sch-checks.md` | Findings of the script and, per IC, supply nets with their capacitors, pins that reach a connector, and configuration pins with their pull resistors |
| `sch-findings.json` | The same as data |
| `sch-review.md` | Final review written by the agent after the datasheet pass |

All three go next to `design.json`.

## Checks

| Id | Severity | Finds |
| --- | --- | --- |
| `SUPPLY_PIN_OPEN` | error | IC supply pin with no connection |
| `NO_DECOUPLING` | warning | IC supply net with no fitted capacitor to ground |
| `NO_PULL_RESISTOR` | warning, note when the net leaves the board | I2C-named or open-collector net with no fitted resistor to a rail |
| `INPUT_NOT_DRIVEN` | warning | Net that has only input pins |
| `OUTPUTS_TIED` | warning | Net driven by outputs of more than one part |
| `CAP_VOLTAGE` | error below the rail, warning below the margin | Capacitor voltage rating against the rail |
| `PIN_ABOVE_SUPPLY` | warning | IC signal pin on a rail above the supply of the part, directly or through a resistor without a sufficient divider |
| `PART_SHORTED` | warning | Two-terminal part with both pins on one net |
| `SINGLE_PIN_NET` | warning, note on connectors | Net with a single pin |
| `PINS_OPEN` | note | IC pins with no net |
| `BOM_NO_MPN` | note | Fitted parts without a manufacturer part number |
| `SCH_BOARD_DIFF` | warning | Differences between schematic and board |

## Settings

Optional `sch-review.json` next to `design.json`. Any key left out keeps
its default.

```json
{
  "net_voltages": {"VBUS_SENSE": 5.0, "VDD33": 3.3},
  "ground_net_regex": "(?i)^(a|d)?gnd$|^pe$",
  "cap_voltage_margin": 2.0,
  "reference_kinds": {"X": "connector", "XT": "crystal"},
  "skip_checks": ["BOM_NO_MPN"]
}
```

`net_voltages` gives the voltage of nets whose name does not carry one.
The pin level check and the capacitor voltage check use it; without it
they only see rails named like `3V3` or `+5V`. `reference_kinds` replaces
the whole default table, so list every prefix you use. The full key list with defaults is at the top of
`skills/sch-review/scripts/schcheck.py`.

## Limits

- The checks work from net names, pin names and pin types. A supply rail
  named without a voltage is not checked for capacitor rating, and a pull
  resistor that goes to an unnamed node without a capacitor is not seen as
  a pull.
- The pin level check runs only for parts whose supply nets all have a
  known voltage, and only against rails or declared nets. A signal net
  that merely has a voltage in its name is not treated as a rail. Parts
  with several supplies are compared with the highest one.
- Altium data does not carry no-connect marks, so open IC pins are listed
  as notes, not as errors.
- With several Altium variants a part counts as not fitted only when it is
  absent in all of them.
- The datasheet pass depends on the agent obtaining each datasheet. ICs it
  could not check are listed at the end of the review.
- Tested on three boards: one KiCad, two Altium.

## Layout

```
skills/sch-review/
  SKILL.md              steps the agent follows, check list
  scripts/schcheck.py   mechanical checks and context tables
```
