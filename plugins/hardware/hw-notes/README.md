# hw-notes

Writes commit messages and Redmine task descriptions for Altium hardware
repos, in English and Turkish, from the real schematic, PCB and output
changes. Also reports start date, end date and estimated work hours.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install hw-notes@skill-sets
```

Requires Python 3 and `olefile`:

```
pip install olefile
```

## Run

Inside the hardware repo:

```
/hw-notes:hw-notes [options]
```

| Option | Effect |
| --- | --- |
| `humanize` | Apply the rules in `styles/humanize.md` |
| `since <ref>` / `until <ref>` | Compare range. Default: HEAD vs working tree |
| `full` | Describe the whole design (initial release) |
| `commit` / `redmine` | Produce only that part |
| `en` / `tr` | Produce only that language |

Example:

```
/hw-notes:hw-notes since v1.0 humanize
```

## Input

An Altium repo: `.PrjPCB`, `.SchDoc`, `.PcbDoc`, OutJob and generated
outputs, plus its git history.

## Config

Optional. Put `.hwnotes/config.json` in the hardware repo to describe its
conventions. Missing keys keep their default.

| Key | Default | Meaning |
| --- | --- | --- |
| `output_dirs` | `["Doc", "Docs", "Outputs", "Project Outputs*"]` | Folder names that hold generated outputs (wildcards allowed) |
| `local_only_hints` | `[]` | File name fragments of documents that stay local and are kept out of commit texts |
| `version_regex` | `(?:^\|[_-])V(?P<ver>\d+)(?:[_.-]\|$)` | Searched in output file names. Groups: `ver`, optional `rev` |
| `stage_regex` | `(?P<stage>alpha\|beta\|rc\|rel)(?P<num>\d+)` | Matched against branch name, then repo folder name |
| `tag_format` | `V{ver}` | Commit prefix. Placeholders: `{ver}` `{rev}` `{stage}` `{Stage}` `{stage_num}` |

Example for a repo with outputs in `Release/`, files named
`BOARD_R2_V3_bom.xlsx` and branches named `beta1`:

```json
{
  "output_dirs": ["Release"],
  "local_only_hints": ["Review Notes"],
  "version_regex": "_(?P<rev>R\\d+)_V(?P<ver>\\d+)_",
  "tag_format": "{Stage}{stage_num}-V{ver}"
}
```

This gives the tag `Beta1-V3`. If a placeholder has no value, the tag is
empty and the skill asks for it.

## Output

- Commit message (EN, TR) and Redmine description (EN, TR) in the chat.
- Dates and estimated hours.
- A copy in `<repo>/.hwnotes/<yyyy-mm-dd>_<tag>.md`. The folder is added
  to `.git/info/exclude`, not to `.gitignore`.

The collector script only reads the repo.

## Layout

```
skills/hw-notes/
  SKILL.md             steps the agent follows
  scripts/collect.py   builds the fact sheet from the repo
  scripts/altium.py    Altium file parser
  styles/commit.md     commit format and examples
  styles/redmine.md    Redmine format and examples
  styles/humanize.md   wording rules for the humanize option
```

Edit the files in `styles/` to match your own format.
