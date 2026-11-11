# hw-read

Reads a hardware design project straight from its project files and writes
it as JSON: parts, pins, nets, net classes, design rules, stackup, zones and
the raw layout geometry. Other skills (schematic review, layout review, EMC
checks) build on this output.

Supported: KiCad 10 and KiCad 9 projects, and Altium Designer projects
(binary `.SchDoc` and `.PcbDoc`).

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install hw-read@skill-sets
```

Requires Python 3.8 or newer. KiCad projects need no extra packages;
`kicad-cli` is used only for projects that have a schematic but no board
file. Altium projects need `olefile`:

```
pip install olefile
```

## Run

As a skill, inside the project folder:

```
/hw-read:hw-read
```

Or the script on its own:

```
python skills/hw-read/scripts/hwread.py <project folder or file> [options]
```

| Option | Effect |
| --- | --- |
| `--out DIR` | Output folder. Default: `<project folder>/.hwread/` |
| `--no-layout` | Do not write `layout.json` |
| `--no-kicad-cli` | Never call `kicad-cli` |

Example:

```
python skills/hw-read/scripts/hwread.py ~/boards/sensor-hub
```

## Input

KiCad: a folder with a `.kicad_pro`, its root `.kicad_sch` and
`.kicad_pcb`. Sub sheets are followed from the root schematic.

Altium: a folder with a `.PrjPcb`. The sheets and the board are taken from
its document list. When the project has several boards, the one without
`panel` in its name is read.

A board or a schematic alone also works, with less data.

## Output

| File | Content |
| --- | --- |
| `design.json` | Components with MPN, placement and pin to net mapping; nets with routing summary; net classes; rules; stackup; zones; schematic notes; schematic against board check |
| `layout.json` | Pads, tracks, vias, zone outlines and board outline with coordinates |
| `summary.md` | Short overview, also printed to the terminal |

The output folder contains its own `.gitignore`, so it never shows up in
the project's git status. The project files are only read.

Units are mm and degrees. The key list is in
[skills/hw-read/SKILL.md](skills/hw-read/SKILL.md).

## How the data is taken (KiCad)

- Netlist: from the pads in the board file. Checked against the netlist
  that KiCad 10 exports for the same project: all 1473 pins of the test
  board map to the same net.
- Part data: from the board, completed with the schematic symbols. Parts
  that differ between the two are listed in `checks.schematic_vs_board`.
- Net classes: explicit assignments first, then the patterns from the
  project file, tried as exact name, wildcard and regular expression.
- Without a board file: netlist through `kicad-cli sch export netlist`.

## How the data is taken (Altium)

- Netlist: from the pads in the board file.
- Part values and parameters: from the schematic sheets. A `Comment` of
  the form `=Name` is resolved to that parameter.
- Schematic netlist: traced from wires, junctions, net labels, ports and
  power ports, then compared with the board. On the test board 1023 of
  1027 schematic pins group the same way as on the board; the other four
  are hidden crystal pins without a net name, which the schematic trace
  leaves unconnected.
- Fitted or not: from the variants in the project file.
- Stackup: from the layer chain in the board file.
- Filled copper area: from the poured regions, holes subtracted.
- Solid regions drawn by hand are listed with the zones, kind
  `Solid Region`.

## Limits

- Schematics older than KiCad 7 keep the references in a different place;
  the reader then falls back to the reference stored in the symbol and says
  so in `limits`.
- Board outline size is the bounding box of the outline layer. Arcs count
  by their start, middle and end points.
- Buses, net ties and custom rule files are not interpreted. The custom
  rule file is passed through as text.
- Altium: tested on two four-layer boards, one saved in an older and one
  in a recent file version. Boards that store the layer stack only in the
  newer format are reported in `limits` instead of being read.
- Altium: copper fills are written to `layout.json` (`fills`) but neither
  test board has one, so that part is untested. Net classes that Altium
  generates per component (`R1_Nets`) are left out.
- Altium: the schematic trace treats the design as flat. Repeated sheets
  and multi-channel designs are not expanded. With a board file this only
  affects the schematic against board check.
- Altium: ASCII-saved files, split planes, pad stacks that differ per
  layer and slotted holes are not read.
- Altium rules are passed through with their scope expressions; they are
  not evaluated per net.

## Layout

```
skills/hw-read/
  SKILL.md            steps the agent follows, JSON key list
  scripts/hwread.py   command line entry, project search, summary
  scripts/kicad.py    KiCad project, schematic and board reader
  scripts/altium.py   Altium project, schematic and board reader
  scripts/common.py   helpers shared by both readers
  scripts/sexpr.py    S-expression parser
```
