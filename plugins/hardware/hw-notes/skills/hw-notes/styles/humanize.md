# Humanize rules (apply only when "humanize" is requested)

Goal: the text reads like an engineer wrote it quickly and precisely, not like generated prose.

Do:
- Short, plain sentences or fragments. One fact per clause.
- Concrete nouns and numbers (part refs, values, mm, counts) instead of adjectives.
- Vary bullet openings; it is fine to start with the part or the net ("C62/C63 added ...").
- Keep the user's own abbreviations and habits (DI/DO, fitted for test, DRC clean, ->).
- Turkish: everyday engineering Turkish ("dizildi", "taşındı", "döküldü", "kontrol edildi"),
  mixed English terms as engineers say them.

Avoid:
- Filler and hype: "comprehensive", "robust", "seamless", "significantly", "ensures", "leverages",
  "kapsamlı", "sorunsuz bir şekilde", "önemli ölçüde", "sağlanmıştır" chains.
- Em dashes, semicolon chains longer than two, rhetorical summaries ("Overall, ...", "Sonuç olarak").
- Repeating the same verb in every bullet ("updated ... updated ... updated").
- Passive constructions stacked in Turkish ("yapılmış olup ... edilmiştir").
- Explaining obvious things or adding reasons nobody gave.
- Perfect symmetry: bullets do not all need the same length or structure.

Keep all facts, numbers and part references unchanged. Humanize changes wording, never content.
