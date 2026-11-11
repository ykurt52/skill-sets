# handoff

Writes a summary of the current session for the next agent: state, decisions, open items and the skills to call. Existing specs, commits and diffs are referenced, not copied. Secrets are redacted.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install handoff@skill-sets
```

## Run

```
/handoff:handoff continue the bring-up of the CAN driver
```

## Output

A Markdown file in the OS temp folder (`%TEMP%` on Windows, `$TMPDIR` or `/tmp` elsewhere). Point the next session at it.

## Layout

```
skills/handoff/SKILL.md
```

## Source

From [mattpocock/skills](https://github.com/mattpocock/skills/tree/4ae2317ddb22b046e26455e4f9c9b3aaaf62212d/skills/productivity/handoff) at commit `4ae2317`, MIT
licensed, see [../LICENSE](../LICENSE). Codex metadata (`agents/`) is left
out.

Changes from upstream: None. Copied as published.
