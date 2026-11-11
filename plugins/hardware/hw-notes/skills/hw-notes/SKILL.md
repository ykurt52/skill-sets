---
name: hw-notes
description: Write project-specific git commit messages and Redmine task descriptions for Altium hardware projects, each in English AND Turkish, from the real schematic/PCB/output changes in the repo, plus start/end dates and work hours. Use when the user asks for a commit message, commit mesajı, Redmine description/task açıklaması, release notes or work hours for a hardware (Altium) repo, or invokes /hw-notes. Supports a "humanize" option.
---

# hw-notes: bilingual commit + Redmine notes for hardware repos

Produces four texts for the current hardware repo:
1. Commit message (EN)
2. Commit message (TR)
3. Redmine description (EN)
4. Redmine description (TR)
plus start date, end date and total work hours.

Arguments (free text after the command, any order):
- `humanize`: apply `styles/humanize.md` on top of the style rules.
- `since <ref>` / `until <ref>`: compare range (default: HEAD vs working tree, or HEAD~1 vs HEAD if clean).
- `full`: describe the whole design (initial release) instead of a diff.
- `commit` / `redmine`: produce only that part (default both).
- `en` / `tr`: only one language (default both).

## Steps

1. **Collect facts.** Run from the repo (or pass `--repo`):
   ```bash
   python "${CLAUDE_SKILL_DIR}/scripts/collect.py" --repo "<repo>" [--since REF] [--until REF] [--full]
   ```
   It prints a Markdown fact sheet: version tag guess, variants (not-fitted lists), schematic diff
   (added/removed/changed parts, nets reconnected), PCB diff (moved blocks per sheet, added/removed,
   via/track/pour changes), variant diff, changed files by class, untracked and LOCAL-ONLY files,
   work-time estimate and recent commits. Needs `olefile` (`pip install olefile` if missing).

2. **Add conversation knowledge.** The fact sheet says *what* changed. The *why* (design decisions,
   test plans, review findings) comes from this conversation or the user. Use both. Never invent
   facts that are in neither. If an important "why" is unknown, write the fact without a reason
   instead of guessing.

3. **Read the style files** in `styles/` before writing:
   - `commit.md`: commit format, EN + TR rules and real examples
   - `redmine.md`: Redmine description format, EN + TR
   - `humanize.md`: only when `humanize` is requested

4. **Write the four texts** following the style files exactly. Rules that always apply:
   - Commit: short and simple; part/IC names OK (the MCU or regulator type), but NO designators (J1, C7, U3), nets
     or value lists.
   - Redmine: sales managers read it. Function level only: no part refs, IC names, nets, values
     or layout details (see `styles/redmine.md`).
   - Never say the project was derived/copied from another project, and never name other product
     codes unless the change is about them.
   - Never mention LOCAL-ONLY documents (the fact sheet lists them) in commit messages. They may be mentioned in Redmine (work done) but not as committed files.
   - Do not mention untracked files as committed. If outputs that the commit text claims are
     untracked, say so to the user once.
   - Fitted-for-test parts, DNP decisions and open test items go in the text as facts, without
     re-arguing them.
   - Turkish text is written natively (not word-by-word translation). Common technical terms stay
     as engineers use them: PCB, DRC, BOM, PnP, Gerber, via, pour/polygon, stackup, V-cut, footprint.

5. **Dates and hours.** Report start, end (dd.mm.yyyy) and total hours from the fact sheet's work
   time section. It is an estimate from Altium History saves, changed-file times and commits
   (gaps > 60 min count as breaks). Say it is an estimate and show the per-day lines.

6. **Output.** Show the texts in the chat, each in its own fenced code block, in this order:
   Commit EN, Commit TR, Redmine EN, Redmine TR, then the dates/hours line. Also save them to
   `<repo>/.hwnotes/<yyyy-mm-dd>_<tag>.md` and make sure `.hwnotes/` is listed in
   `<repo>/.git/info/exclude` (local ignore, never `.gitignore`).

7. **Learn from corrections.** When the user edits a text or says "bu benim tarzım değil",
   apply the correction, then update the matching file in `styles/` (rules and/or a new example)
   so the next project gets it right. Keep the style files short; replace outdated examples.

## Notes
- Hardware projects only (Altium: .PrjPCB, .SchDoc, .PcbDoc, OutJob, generated outputs).
- Repo conventions are read from `<repo>/.hwnotes/config.json` when it exists: output folder
  names, local-only document names, version pattern and tag format. Keys and defaults are in
  `DEFAULT_CONFIG` at the top of `scripts/collect.py`. Without the file the defaults apply.
- The collector is read-only; it never modifies the repo. Saving `.hwnotes/` and editing
  `.git/info/exclude` is done by you in step 6.
- Commit prefix is the version tag guess from the fact sheet, built from output file names and
  the branch or folder name as set in the config. If the guess is `None`, ask the user once and
  offer to save the answer as `version_regex` / `tag_format` in `.hwnotes/config.json`.
