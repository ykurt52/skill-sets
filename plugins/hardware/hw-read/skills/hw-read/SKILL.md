---
name: hw-read
description: Read a KiCad or Altium hardware project directly from its project files (not from PDF or exports) into structured JSON - components with MPN and placement, pin to net mapping, nets with routing summary, net classes, design rules, stackup, zones and raw layout geometry. Use before any schematic review, layout review, EMC check or BOM work on a KiCad (.kicad_pro) or Altium (.PrjPcb, .SchDoc, .PcbDoc) project, when the user asks to read, load, parse or summarize a hardware project, or invokes /hw-read.
---

# hw-read: hardware project to JSON

Turns a design project into three files that other skills and you can query
without opening the CAD tool.

## Steps

1. **Run the reader** on the project folder or project file:
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/hwread.py" "<project path>"
   ```
   Options: `--out DIR` (default `<project folder>/.hwread/`), `--no-layout`,
   `--no-kicad-cli`. If several projects are found the script lists them and
   stops; ask the user which one, then pass that file. Altium files need the
   `olefile` package (`pip install olefile` if the script asks for it).

2. **Read the printed summary.** It is also saved as `summary.md`. Report to
   the user: board size and layer count, component and net counts, the
   "Schematic against board" section and the "Not read" section. Do not
   repeat the tables unless asked.

3. **Answer from the JSON, not from memory.** Load only the part you need:
   - `design.json` for parts, pins, nets, rules, stackup, zones.
   - `layout.json` only for geometry questions (distances, track paths, pad
     positions). It is large; filter it with a short script instead of
     reading it whole.

## design.json

| Key | Content |
| --- | --- |
| `source` | tool, file names, format versions, where the netlist came from |
| `title_block` | title, revision, date, company |
| `sheets` | hierarchy path, file, page, component count, no-connect count |
| `board` | size, outline box, thickness, copper layers, item counts, `y_axis` (`down` for KiCad, `up` for Altium) |
| `stackup` | every layer with type, thickness, material, Er, loss tangent |
| `net_classes` | KiCad: clearance, track width, via size, diff pair width and gap. Altium: member count only, the numbers are in the rules |
| `rules` | KiCad: board minimums, predefined track and via sizes, custom rule text. Altium: `altium_rules`, every design rule with kind, scope expressions and values in mm |
| `components` | by reference: value, footprint, MPN, manufacturer, datasheet, extra fields, sheet, side, x, y, rotation, mount, dnp, `pins` |
| `components.<ref>.pins` | by pad number: net, pin name, electrical type |
| `nets` | by name: pins (`REF.PAD`), classes, track length, min and max width, layers, via count, zone layers |
| `zones` | net, layers, priority, pad connection, clearance, filled area per layer, keepout rules |
| `diff_pairs` | Altium only: name, positive net, negative net |
| `variants` | Altium only: variant name and the references not fitted in it |
| `notes` | free text placed on the schematic sheets |
| `checks.schematic_vs_board` | parts on one side only, value / footprint / dnp differences; Altium also lists nets whose pins group differently in the schematic and on the board |
| `limits` | what could not be read for this project |

Units: mm and degrees. Coordinates are the ones stored in the board file;
check `board.y_axis` before reasoning about above, below or rotation sense.

## layout.json

`pads` (absolute position, size, drill, layers, net), `tracks` (segments and
arcs with length), `vias`, `zone_outlines` (same order as `zones`), `outline`,
and for Altium `fills` (rectangular copper fills).

## Notes

- The reader never writes into the project files. The output folder ignores
  itself in git.
- With a board file the netlist comes from the pads, so it reflects the
  board as last synchronized. Any drift from the schematic shows up in
  `checks.schematic_vs_board`.
- KiCad without a board file: the netlist is exported through `kicad-cli`.
  If that is not installed the JSON still has the components but no nets;
  say so.
- Altium without a board file: the netlist is traced from the schematic
  wires as a flat design. Treat it as less reliable than a board netlist.
- Altium `dnp` is true when the part is not fitted in every variant of the
  project; `not_fitted_in` lists the variants per part.
- `mpn` is filled only when a field has a recognizable name (MPN,
  Manufacturer Part Number, ...). Otherwise look in `fields`.
- Always pass on the `limits` entries when you report results built on this
  data.
