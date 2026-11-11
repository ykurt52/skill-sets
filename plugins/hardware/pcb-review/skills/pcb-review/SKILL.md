---
name: pcb-review
description: Review the PCB layout of a KiCad or Altium board - decoupling capacitor distance to IC supply pins, differential pair length and via mismatch, critical nets routed without a reference plane, dangling track ends, nets with no copper, copper too close to the board edge, and isolation distance between net groups. Writes an HTML board view with every finding marked on the layout. Use when the user asks for a layout review, PCB review, layout incelemesi, placement or routing check, creepage or isolation check, or invokes /pcb-review. Needs the hw-read skill.
---

# pcb-review: layout review

A script measures what can be measured from the board data. You settle the
project-specific limits with the user, judge the findings, and report only
the problems with their location.

## Steps

1. **Read the project** with the hw-read skill (`/hw-read:hw-read`), every
   time. If hw-read is not installed, tell the user to install it from the
   same marketplace and stop.

2. **Run the checks** on the folder hw-read wrote to:
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/pcbcheck.py" "<folder with design.json>"
   ```
   It prints findings and a supply net table, and saves `pcb-checks.md`,
   `pcb-findings.json` and `pcb-board.html` next to `design.json`.

3. **Ask for the limits the board data cannot give**, in one message:
   - isolation: which net groups must stay apart and by how much (field
     side against logic, mains against low voltage). Without a rule the
     isolation check does not run;
   - which interfaces are length-matched and to what tolerance, when the
     default of 1 mm does not fit;
   - expected current on the supply nets, when track width matters.
   Write the answers into `pcb-review.json` next to `design.json` and run
   step 2 again.

4. **Triage the findings.** Each one is a candidate:
   - distances are straight lines between pad centers;
   - the reference plane check uses zone outlines, not the poured copper;
   - an isolation finding that says copper lies inside a pour means the gap
     is the pour clearance: look it up in `rules` in `design.json` (KiCad
     zone clearance, Altium polygon clearance rule) and compare.
   Open `layout.json` and measure when a finding is close to its limit.
   Drop what the design explains and say why.

5. **Checks only you can do.** With `design.json`, `layout.json` and the
   supply net table:
   - track width on supply nets against the current the user gave, using
     the narrowest width and the copper thickness from the stackup;
   - via count on nets that change layers under load;
   - placement of crystals, switching regulators and their loop parts
     relative to the ICs they serve;
   - stackup: which layer each critical net references, and whether the
     net classes match the impedance the stackup gives.
   State what you measured and how. Do not quote impedance or current
   capacity figures from memory; compute them or name the source.

6. **Report.** Give the user only the problems, most severe first: location
   (references, nets, layer), the measured value, the limit and where the
   limit comes from, and the action. Mark each as `verified` (measured from
   the board data) or `inferred`. Tell the user that `pcb-board.html` shows
   every located finding on the board; open or send it if the session can.

## Checks the script runs

| Id | Finds |
| --- | --- |
| `DECOUPLING_DISTANCE` | IC supply pin whose nearest capacitor to ground is farther than the limit |
| `DIFF_PAIR_SKEW` | differential pair whose two nets differ in routed length by more than the limit |
| `DIFF_PAIR_VIAS` | differential pair with a different via count on each net |
| `NO_REFERENCE_PLANE` | critical net routed where no adjacent layer has a copper zone |
| `DANGLING_TRACK` | track end that touches no pad, via, track or zone of its net |
| `NET_NO_COPPER` | net with several pads and no track, via or zone |
| `EDGE_CLEARANCE` | track or via closer to the board edge than the limit |
| `ISOLATION` | copper of two net groups closer than the configured distance on a shared layer |

Critical nets are differential pairs, nets in a net class other than the
default, and nets whose name matches `critical_net_regex`. Differential
pairs come from the board file when it lists them, otherwise from net name
suffixes such as `_P` / `_N`.

## Project settings

Optional `pcb-review.json` next to `design.json` overrides the defaults at
the top of `scripts/pcbcheck.py`:

```json
{
  "isolation": [
    {"name": "field to logic", "a": "^FIELD_|^IN\\d+$", "b": "^3V3$|^GND$", "min_mm": 3.0}
  ],
  "decoupling_max_mm": 4,
  "diff_pair_max_skew_mm": 0.5,
  "critical_net_regex": "(?i)clk|usb|eth",
  "skip_checks": ["DIFF_PAIR_VIAS"]
}
```

`a` and `b` are regular expressions matched against net names.
