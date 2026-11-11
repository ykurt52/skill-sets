# pcb-review

Reviews the layout of a KiCad or Altium board. A script measures the board
data and reports:

- IC supply pins whose decoupling capacitor is too far away;
- differential pairs with a length or via mismatch;
- critical nets routed with no copper zone on an adjacent layer;
- track ends that connect to nothing, and nets with no copper at all;
- tracks and vias too close to the board edge;
- copper of two net groups closer than a set isolation distance.

Every located finding is drawn on the board in a single HTML file. The
agent then covers what needs judgment: track width against current,
placement of regulators and crystals, stackup and impedance.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install hw-read@skill-sets
/plugin install pcb-review@skill-sets
```

pcb-review reads the `design.json` and `layout.json` that
[hw-read](../hw-read/README.md) writes. Requires Python 3.8 or newer, no
extra packages.

## Run

As a skill, inside the project folder:

```
/pcb-review:pcb-review
```

The script on its own:

```
python skills/pcb-review/scripts/pcbcheck.py <folder with design.json> [options]
```

| Option | Effect |
| --- | --- |
| `--config FILE` | Settings file. Default: `pcb-review.json` next to `design.json` |
| `--out DIR` | Output folder. Default: the folder of `design.json` |

Example:

```
python skills/hw-read/scripts/hwread.py ~/boards/sensor-hub
python skills/pcb-review/scripts/pcbcheck.py ~/boards/sensor-hub
```

## Input

`design.json` and `layout.json` from hw-read (schema `hw-read/1`). The
project must have a board file.

## Output

| File | Content |
| --- | --- |
| `pcb-checks.md` | Findings and a table of the supply nets with length, narrowest track, vias and zone layers |
| `pcb-findings.json` | The findings as data, with board coordinates |
| `pcb-board.html` | The board drawn from the layout data, findings marked; click a finding to locate it. One file, no network access needed |

All three go next to `design.json`.

## Checks

| Id | Severity | Default limit | Finds |
| --- | --- | --- | --- |
| `DECOUPLING_DISTANCE` | warning | 5 mm | IC supply pin whose nearest capacitor to ground is farther away |
| `DIFF_PAIR_SKEW` | warning | 1 mm | Length difference between the two nets of a pair |
| `DIFF_PAIR_VIAS` | note | | Different via count on the two nets of a pair |
| `NO_REFERENCE_PLANE` | warning | 1 mm | Critical net routed where no adjacent layer has a copper zone |
| `DANGLING_TRACK` | warning | | Track end that touches no pad, via, track or zone of its net |
| `NET_NO_COPPER` | warning | | Net with several pads and no track, via or zone |
| `EDGE_CLEARANCE` | warning | 0.3 mm, or the board rule | Track or via too close to the board edge |
| `ISOLATION` | error | from the settings | Copper of two net groups too close on a shared layer |

The limits are defaults to start from, not values from a standard. Set
them per project.

## Settings

Optional `pcb-review.json` next to `design.json`. Any key left out keeps
its default.

```json
{
  "isolation": [
    {"name": "field to logic", "a": "^FIELD_|^IN\\d+$", "b": "^3V3$|^GND$", "min_mm": 3.0}
  ],
  "decoupling_max_mm": 4,
  "diff_pair_max_skew_mm": 0.5,
  "skip_checks": ["DIFF_PAIR_VIAS"]
}
```

The isolation check runs only when a rule is given. `a` and `b` are
regular expressions matched against net names. The full key list with
defaults is at the top of `skills/pcb-review/scripts/pcbcheck.py`.

## Limits

- Distances between parts are straight lines between pad centers.
- Routed length is the sum of track segments; length inside pads and vias
  is not counted.
- Zones are known by their outline, not by the poured copper. Cutouts and
  islands inside a zone are not seen. Copper that lies inside a pour of
  another group is reported with the pour clearance when the board file
  gives one, otherwise as a point to check by hand.
- In the isolation check pads count as circles of their larger dimension,
  so a reported gap can be smaller than the real one. Distances through
  the board, between layers, are not measured, and slots do not lengthen
  the path.
- Track width against current, impedance and thermal behaviour are left
  to the agent pass.
- Tested on three boards: one KiCad, two Altium. The isolation check was
  only exercised with made-up rules.

## Layout

```
skills/pcb-review/
  SKILL.md               steps the agent follows, check list
  scripts/pcbcheck.py    measurements and checks
  scripts/boardview.py   HTML board view with the findings
```
