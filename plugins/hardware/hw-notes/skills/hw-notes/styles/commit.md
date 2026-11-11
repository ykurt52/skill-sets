# Commit message style

## Format (both languages)
```
<Tag>: <short summary, no trailing period>

- <Area>: <what changed>; <second fact>
  <continuation lines indented 2 spaces>
- <Area>: ...
```
- `<Tag>` = version tag from the fact sheet, e.g. `V1`. First release of a project: `<Tag>: <PRODUCT> initial release`.
- Summary line: <= 72 chars, describes the main change.
- **Keep it short and simple:** max ~6 bullets, 1-2 lines each, plain readable sentences.
  Details live in the schematic, not the commit.
- **No designators** (J1.3, C7, R14, U3, TP1...) and no net/pin names. Part names and IC types are
  fine (an MCU, a load switch, a TVS array by type). Say "connector", "module", "LDO", not "J1", "U3", "U2".
- No signal chains, no value lists, no pin mapping.
- Bullets start with `- `, wrapped at ~72 chars, continuation indented 2 spaces.
- Area names (EN): Variants, Inputs, Outputs, CPU, Power, SPI bus, Isolation, PCB, Panel, Outputs, Doc, 3D, Library.
- Numbers only where they are the change (`10k -> 4.7k`), never with a designator.
- Mention test status inline: `(fitted for test)`, `DRC clean (0 violations)`, `polygons repoured`.
- Outputs bullet lists what was regenerated (BOM, PnP, schematic/assembly PDFs, panel Gerber/NC drill).
- No marketing words, no "this commit", no emojis.

## Turkish (TR) rules
- Same structure and tag. Area names: Varyantlar, Girişler, Çıkışlar, CPU, Güç, SPI bus, İzolasyon, PCB, Panel, Çıktılar, Doküman, 3D, Kütüphane.
- Short technical sentences, no verb-heavy prose: "Filtre direnci tüm kanallarda değiştirildi" style.
- Keep technical terms in English where engineers use them (PCB, DRC, BOM, PnP, Gerber, via, pour, footprint, stackup).

## Example (EN)
The product and parts below are made up. Replace them with your own approved examples.
```
V1: SENSOR_HUB initial release

- Function: RS-485 sensor hub with 8 analog inputs; MCU reads the
  ADC over SPI and answers Modbus RTU
- Power: 24 V input, buck converter to 5 V, LDO to 3.3 V
- Protection: TVS on all field terminals, reverse polarity
  protection on the supply input
- PCB: 4 layers, analog and digital sections separated, DRC clean
- Outputs: 4x3 panel Gerber/NC drill, BOM, PnP, PDFs
- Repo: .gitignore added, old outputs removed
```

## Example (TR)
```
V1: SENSOR_HUB ilk sürüm

- Fonksiyon: 8 analog girişli RS-485 sensör hub; MCU, ADC'yi SPI
  üzerinden okuyor ve Modbus RTU ile cevap veriyor
- Güç: 24 V giriş, 5 V için buck converter, 3.3 V için LDO
- Koruma: tüm saha klemenslerinde TVS, besleme girişinde ters
  bağlantı koruması
- PCB: 4 katman, analog ve dijital bölümler ayrıldı, DRC temiz
- Çıktılar: 4x3 panel Gerber/NC drill, BOM, PnP, PDF'ler
- Repo: .gitignore eklendi, eski çıktılar silindi
```

## Example of a small change
```
V2: input filter corner frequency lowered

- Inputs: filter resistor 10k -> 4.7k on all analog channels
- PCB: no layout change, polygons repoured, DRC clean
- Outputs: BOM and schematic PDF regenerated
```

## What NOT to write (too detailed)
```
- Inputs: R14/C7 filter on AIN3 changed, J1.3 moved to net ADC_IN3,
  U3 pin 12 rerouted on L3
```
Designators, pin numbers, net names and layer routing belong in the schematic and PCB.
