# Redmine description style

## Rules
- **Audience: sales managers read Redmine.** Write at product / function level. NO part refs
  (R5, C16, U3), NO MPNs or IC names, NO net/pin names, NO component values, NO layer/stackup/via
  details, NO internal fab settings. Say what the module does and what work was done ("protected
  sensor inputs", "production files prepared"). Product-level specs are fine
  (number of channels, isolation voltage).
- **No section headings.** No "Scope / Changes / Production / Prototype tests" blocks; they read
  as generated text. Write 2-4 short plain paragraphs like an engineer typing into Redmine: what
  the module is, what was done, what will be tested. Bullets only if the user's own text uses them.
- First line: product code + colon + plain description of the product (not the version tag).
- Value changes, when a product-level value changes, use ">" (`8 channels > 16 channels`), not "->".
- Short reason in parentheses only when known and non-technical.
- No commit hash, no dates/hours inside the description (those go to Redmine fields; report them
  separately in the chat).
- Do NOT write "derived from <other project>".

## Languages
- TR is the primary text. EN mirrors it paragraph by paragraph.
- Turkish is written natively, not translated word by word.

## Example (TR)
The product below is made up. Replace it with your own approved examples.
```
SENSOR_HUB: saha sensörleri için RS-485 toplama modülü

Sahadaki 8 analog sensörü tek bir RS-485 hattı üzerinden kontrol cihazına bağlayan modül tasarlandı. Kart ölçüleri ve klemens yerleşimi mevcut modüllerle aynı; mevcut panolara ek işlem gerektirmeden takılabiliyor.

Devre ve PCB tasarımı tamamlandı, üretim dosyaları hazırlandı (12'li panel).

Prototipler gelince ölçüm doğruluğu, ısınma, uzun kabloda haberleşme ve ESD dayanımı test edilecek.
```

## Example (EN)
```
SENSOR_HUB: RS-485 acquisition module for field sensors

A module was designed that connects 8 analog field sensors to the controller over a single RS-485 line. Board size and terminal layout match the existing modules, so it fits current cabinets without extra work.

Circuit and PCB design are complete and the production files are ready (panel of 12).

When the prototypes arrive, measurement accuracy, heating, communication over long cable and ESD immunity will be tested.
```

## What NOT to write (too detailed for this audience)
```
- Input resistors 270R > 330R (total MCU I/O current).
- Y capacitors C16, C53 added across the isolation barrier.
- SCK RC filter: R5 100R + C54 47pF.
```
Part references, values and circuit-level reasons belong in the commit and the schematic.
