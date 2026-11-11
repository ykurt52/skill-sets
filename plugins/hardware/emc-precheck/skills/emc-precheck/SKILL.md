---
name: emc-precheck
description: Pre-check the port protection of a KiCad or Altium board before EMC testing - finds every net that leaves through a connector, walks from the connector toward the ICs and reports lines with no clamp, ICs placed before the clamp, clamps with too low a standoff voltage, clamps far from their connector or without a short ground return, supply inputs without a fuse and how shields are tied. Writes an HTML board view with the findings marked on the layout. It can also simulate a surge, burst or ESD pulse into one port with ngspice and report the voltage at the IC pin and the stress on the clamp. Use when the user asks for an EMC, ESD, EFT, burst or surge pre-check, a virtual surge or ESD test, a port protection review, TVS placement check, or invokes /emc-precheck. Needs the hw-read skill. It is a design pre-check, not a replacement for a lab test.
---

# emc-precheck: port protection pre-check

A script follows each connector pin into the circuit and checks what stands
between the outside world and the first IC, in the netlist and on the
layout. You then settle the open questions with the user and compare the
clamps with the parts they protect.

## Steps

1. **Read the project** with the hw-read skill (`/hw-read:hw-read`), every
   time. If hw-read is not installed, tell the user to install it from the
   same marketplace and stop.

2. **Run the check** on the folder hw-read wrote to:
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/emccheck.py" "<folder with design.json>"
   ```
   It prints findings and a port table, and saves `emc-checks.md`,
   `emc-findings.json` and `emc-board.html` next to `design.json`.

3. **Confirm the ports with the user**, in one message:
   - which connectors leave the product and which are internal (board to
     board, module sockets). The script treats connectors with 60 pins or
     more as internal and says so in its output;
   - the working voltage of port nets whose name carries none;
   - which supply pins are inputs and which only feed something outside.
   Write the answers into `emc-precheck.json` next to `design.json`
   (`internal_connectors`, `external_connectors`, `net_voltages`) and run
   step 2 again.

4. **Triage the findings.** Each one is a candidate. Use the port table to
   see the path the script found, and `design.json` to check it. Drop what
   the design explains: a line that stays inside the enclosure, a part the
   script took for a clamp or failed to recognize as one (the `Basis` text
   says how clamps were recognized). A finding about placement uses the
   straight distance between pads, not the routed path; open
   `layout.json` when the order along the track matters.

5. **Clamp against victim.** For each external line that has a clamp and
   an IC behind it, read both datasheets and compare:
   - clamp standoff voltage against the working voltage of the line;
   - clamping voltage at the test current against the absolute maximum
     rating of the IC pin, taking any series part between them into
     account;
   - clamp capacitance against the signal speed on data lines.
   The test level (kV, class) comes from the user or their test plan. Do
   not pick one yourself, and do not quote standard values from memory.
   Every datasheet value carries its page.

6. **Pulse simulation**, when the user wants numbers for a port or a
   finding of step 5 is close. See "Pulse simulation" below.

7. **Report.** Give the user only the problems, most severe first: port
   (connector, pin, net), what is on the path, what is missing or wrong,
   the datasheet values with pages where step 5 was done, simulated peaks
   where step 6 was done, and the action. Mark each as `verified`,
   `simulated` or `inferred`. Tell the user that `emc-board.html` shows
   every located finding on the board; open or send it if the session can.
   Say plainly that this is a pre-check: the simulation covers one port at
   a time with simple models, and nothing here estimates emissions.

## Checks the script runs

| Id | Finds |
| --- | --- |
| `PORT_NO_CLAMP` | connector net that reaches an IC with no clamping part on the way |
| `VICTIM_BEFORE_CLAMP` | IC pin reached before the first clamp on the line |
| `CLAMP_NO_SERIES` | IC pin on the same net as its clamp, no series part between them |
| `CLAMP_STANDOFF` | clamp standoff voltage at or below the working voltage of the net |
| `CLAMP_PLACEMENT` | clamp farther from the connector than the IC it protects, or beyond the distance limit |
| `CLAMP_GROUND_RETURN` | clamp ground pad with no ground via nearby and no ground zone on its layer |
| `POWER_PORT_NO_FUSE` | supply net on a connector with no fuse in line |
| `SHIELD_TIE` | how each connector shield is tied to ground |

The walk goes through at most three series parts. A signal line stops at
supply rails; a supply line continues only through fuses, chokes and
diodes. Pins with the same name on a protection array count as one node.
On a common mode choke the script pairs the windings from a bypass part
when there is one; otherwise it says the pairing was assumed.

## Pulse simulation

`scripts/emcsim.py` drives one port with a surge, burst or ESD generator
and measures what reaches the IC pin and what the clamp has to take. It
needs ngspice: the program on the path, or the library that KiCad
installs.

1. Check the generators once per machine:
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/emcsim.py" verify
   ```
   Every line must say `ok`. If not, stop and report it.

2. Get the test from the user: pulse type, level in volts, polarity and
   how it is coupled. Do not choose a level yourself.

   | Type | Couplings | Level means |
   | --- | --- | --- |
   | `surge` | `line_to_line`, `line_to_ground`, `io_line`, `direct` | open circuit peak voltage |
   | `burst` | `power_line`, `clamp` (give `clamp_pf`), `direct` | generator set voltage |
   | `esd` | contact discharge only | test voltage |

3. Write a case file next to `design.json`. Take the port path from the
   port table of step 2 and the part values from `design.json`:
   ```json
   {
     "name": "j1-pin7-surge",
     "generator": {"type": "surge", "level_v": 500, "coupling": "io_line", "polarity": "+"},
     "netlist": ["R14 port pin 1k", "Cpin pin 0 5p"],
     "clamps": [{"name": "D2", "a": "port", "b": "0", "vbr": 6.0, "vc": 18.0, "ipp": 24.0,
                 "cj_pf": 350, "bidirectional": true}],
     "measure": [{"node": "pin", "label": "U3.17", "max_v": 3.6, "min_v": -0.3}]
   }
   ```
   - The generator drives node `port` against node `0` (board ground).
   - `netlist` holds plain SPICE lines for everything between the
     connector and the IC pin: series parts, capacitors, the pin itself.
   - Each clamp is built from four datasheet values: breakdown voltage
     `vbr`, clamping voltage `vc` at peak pulse current `ipp`, and
     capacitance `cj_pf`. Read them from the datasheet with the page; do
     not estimate them.
   - `measure` lists node voltages (`node`, `max_v`, `min_v`) or currents
     through a 0 V source you placed in the netlist (`current`, `max_a`).

4. Run it and read the table:
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/emcsim.py" run "<case file>"
   ```
   It writes `<name>.md`, `<name>.json` (with the full deck) and
   `<name>.html` (waveform plots).

5. Model the IC pin both ways and report both:
   - as an open pin with a few pF: the highest voltage it could see;
   - clamped to its rails through its protection diodes (a diode from the
     pin to the supply node, one from ground to the pin, and a 0 V source
     to measure): the current forced into the pin, to compare with the
     injection current limit in the datasheet.

6. Run both polarities. For a burst the result is one pulse of the train.

What the simulation is and is not:

- the generators meet the waveform definitions of their standards (the
  `verify` command shows it); the surge generator values are fitted to
  those definitions, the burst and ESD sources follow the ideal waveform
  formulas;
- the ESD source is the ideal contact discharge current behind the
  discharge resistor: valid while the clamped voltage is small against
  the test voltage;
- a clamp is a straight line from its breakdown point to its clamping
  point, so real parts differ, most of all for ESD;
- layout inductance, the cable and the supply of the board are not in
  the circuit unless you add them to `netlist`.

## Project settings

Optional `emc-precheck.json` next to `design.json` overrides the defaults
at the top of `scripts/emccheck.py`: `internal_connectors`,
`external_connectors`, `internal_pin_count`, `net_voltages`,
`clamp_part_regex` and `clamp_text_regex` (how clamps are recognized),
`clamp_max_distance_mm`, `clamp_ground_via_mm`, `max_series_parts`,
`reference_kinds` and `skip_checks`.
