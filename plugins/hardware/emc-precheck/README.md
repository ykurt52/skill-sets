# emc-precheck

Checks the port protection of a KiCad or Altium board before it goes to an
EMC lab. For every net that leaves through a connector it walks from the
connector toward the ICs and reports:

- lines that reach an IC with no clamping part on the way;
- ICs that sit before the clamp, or on the same net with no series part;
- clamps whose standoff voltage is at or below the working voltage;
- clamps far from their connector, or without a short ground return;
- supply nets on a connector with no fuse in line;
- how each connector shield is tied to ground.

The findings are also drawn on the board in a single HTML file.

It can also simulate a surge, burst or ESD pulse into one port and report
the voltage at the IC pin and the stress on the clamp.

This is a design pre-check. It says nothing about emissions and does not
replace a lab test.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install hw-read@skill-sets
/plugin install emc-precheck@skill-sets
```

emc-precheck reads the `design.json` and `layout.json` that
[hw-read](../hw-read/README.md) writes. Requires Python 3.8 or newer, no
extra packages. The pulse simulation needs ngspice: the program on the
path, or the library that KiCad installs.

## Run

As a skill, inside the project folder:

```
/emc-precheck:emc-precheck
```

The script on its own:

```
python skills/emc-precheck/scripts/emccheck.py <folder with design.json> [options]
```

| Option | Effect |
| --- | --- |
| `--config FILE` | Settings file. Default: `emc-precheck.json` next to `design.json` |
| `--out DIR` | Output folder. Default: the folder of `design.json` |

Example:

```
python skills/hw-read/scripts/hwread.py ~/boards/sensor-hub
python skills/emc-precheck/scripts/emccheck.py ~/boards/sensor-hub
```

## Pulse simulation

Check the generator models, then run a case:

```
python skills/emc-precheck/scripts/emcsim.py verify
python skills/emc-precheck/scripts/emcsim.py run my-port.json
```

`verify` drives each generator into its calibration loads and compares
front time, duration, peak and source impedance with the tolerances of the
basic standard. All 18 quantities pass with the models in this plugin.

A case file describes one port:

```json
{
  "name": "example-io-surge",
  "generator": {"type": "surge", "level_v": 500, "coupling": "io_line", "polarity": "+"},
  "netlist": ["R1 port pin 1k", "Cpin pin 0 5p"],
  "clamps": [{"name": "D1", "a": "port", "b": "0", "vbr": 6.0, "vc": 18.0, "ipp": 24.0,
              "cj_pf": 350, "bidirectional": true}],
  "measure": [{"node": "pin", "label": "IC pin behind 1k", "max_v": 3.6, "min_v": -0.3}]
}
```

| Key | Meaning |
| --- | --- |
| `generator.type` | `surge`, `burst` or `esd` |
| `generator.level_v` | Surge: open circuit peak. Burst: set voltage. ESD: contact discharge voltage |
| `generator.coupling` | Surge: `line_to_line`, `line_to_ground`, `io_line`, `direct`. Burst: `power_line`, `clamp` with `clamp_pf`, `direct` |
| `generator.polarity` | `+` or `-` |
| `netlist` | SPICE lines between node `port` and the IC pin; ground is node `0` |
| `clamps` | Clamps built from datasheet values: `vbr`, `vc` at `ipp`, `cj_pf`, `bidirectional` |
| `measure` | Node voltages or currents to report, with optional limits |
| `step_s`, `stop_s` | Optional time step and length |

Output next to the case file: `<name>.md` (peaks against limits, clamp
current and absorbed energy), `<name>.json` (results and the full deck)
and `<name>.html` (waveform plots).

The numbers in the example are made up to show the format.

### Where the generator models come from

| Pulse | Basis | Model |
| --- | --- | --- |
| Surge | IEC 61000-4-5:2014, 6.2 and 6.3 | The generator circuit of the standard. Its component values are not given there; `scripts/tools/fit_cwg.py` fits them to the waveform definitions. Coupling: 18 uF line to line, 9 uF + 10 ohm line to ground, 0.5 uF + 40 ohm for I/O lines |
| Burst | IEC 61000-4-4:2012, 6.2.2 and 6.3.1 | Ideal single pulse behind 50 ohm and the 10 nF blocking capacitor. Coupling: 33 nF for supply lines; the clamp capacitance depends on the cable and must be given |
| ESD | IEC 61000-4-2:2025, 6.2 | Ideal contact discharge current behind a 330 ohm discharge resistor |

The parameters are stored in `scripts/generators.py` in this project's own
form. No text, table or figure of a standard is included; get the
standards from IEC.

## Input

`design.json` and `layout.json` from hw-read (schema `hw-read/1`). Without
`layout.json` the placement checks are skipped and no board view is
written.

## Output

| File | Content |
| --- | --- |
| `emc-checks.md` | Findings and a table of every connector pin with the path found behind it |
| `emc-findings.json` | The same as data, with board coordinates of each finding |
| `emc-board.html` | The board drawn from the layout data, findings marked; click a finding to locate it. One file, no network access needed |

All three go next to `design.json`.

## Checks

| Id | Severity | Finds |
| --- | --- | --- |
| `PORT_NO_CLAMP` | warning for signals, note for supply nets | Connector net that reaches an IC with no clamping part on the way |
| `VICTIM_BEFORE_CLAMP` | warning | IC pin reached before the first clamp on the line |
| `CLAMP_NO_SERIES` | note | IC pin on the same net as its clamp, no series part between them |
| `CLAMP_STANDOFF` | warning below, note when equal | Clamp standoff voltage against the working voltage of the net |
| `CLAMP_PLACEMENT` | warning, note for the distance limit | Clamp farther from the connector than the IC it protects, or beyond the limit |
| `CLAMP_GROUND_RETURN` | warning | Clamp ground pad with no ground via nearby and no ground zone on its layer |
| `POWER_PORT_NO_FUSE` | note | Supply net on a connector with no fuse in line |
| `SHIELD_TIE` | note | How each connector shield is tied to ground |

## Settings

Optional `emc-precheck.json` next to `design.json`. Any key left out keeps
its default.

```json
{
  "internal_connectors": ["J8"],
  "external_connectors": ["J2"],
  "net_voltages": {"VBUS_SENSE": 5.0, "FIELD_IN": 24.0},
  "clamp_max_distance_mm": 15,
  "skip_checks": ["POWER_PORT_NO_FUSE"]
}
```

Connectors with 60 pins or more are treated as internal (module sockets,
board to board) unless listed in `external_connectors`. The full key list
with defaults is at the top of `skills/emc-precheck/scripts/emccheck.py`.

## Limits

- Clamps are recognized by reference prefix, part number family and the
  words in the part description. A protection part with an unusual name is
  missed, and the line is then reported as unprotected. Extend
  `clamp_part_regex` for your library.
- Distances are straight lines between pad centers, not the routed path.
- The direction of a supply pin is not known, so every supply net on a
  connector is listed for the fuse check.
- The walk follows two-terminal series parts and common mode chokes. A
  line that passes through a switch, a transistor or an IC ends there.
- Standoff voltages come from the part fields or description; working
  voltages from the net name or the settings.
- Pulse simulation: one port at a time, one pulse of a burst. Clamps
  are straight lines through two datasheet points. Layout inductance,
  cables and the powered state of the board are not modelled unless added
  to the case. The ESD source is only valid while the clamped voltage is
  small against the test voltage. Air discharge is not modelled.
- The simulator was run through the KiCad ngspice library on Windows;
  the ngspice program and the macOS and Linux library paths are untested.
- Tested on three boards: one KiCad, two Altium.

## Layout

```
skills/emc-precheck/
  SKILL.md               steps the agent follows, check list
  scripts/emccheck.py    ports, protection path, checks
  scripts/boardview.py   HTML board view with the findings
  scripts/emcsim.py      pulse simulation of one port, generator check
  scripts/generators.py  surge, burst and ESD generator models
  scripts/spicerun.py    runs ngspice without a window
  scripts/tools/fit_cwg.py   fits the surge generator component values
```
