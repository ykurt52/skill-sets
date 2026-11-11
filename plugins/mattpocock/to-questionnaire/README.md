# to-questionnaire

When a decision needs input from another person or team, the agent writes a questionnaire with the context and the exact questions, so the answers come back in a usable form.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install to-questionnaire@skill-sets
```

## Run

```
/to-questionnaire:to-questionnaire connector choice for the field I/O board
```

## Output

`to-questionnaire-<topic>.md` in the current folder, with context, how to answer, and the questions grouped by theme. The agent first asks who it goes to and what you need back.

## Layout

```
skills/to-questionnaire/SKILL.md
```

## Source

From [mattpocock/skills](https://github.com/mattpocock/skills/tree/4ae2317ddb22b046e26455e4f9c9b3aaaf62212d/skills/productivity/to-questionnaire) at commit `4ae2317`, MIT
licensed, see [../LICENSE](../LICENSE). Codex metadata (`agents/`) is left
out.

Changes from upstream: None. Copied as published.
