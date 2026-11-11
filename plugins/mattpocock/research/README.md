# research

Starts a background agent that reads official docs, specs and source code, follows each claim to its owner, and writes the findings with citations, so you can keep working meanwhile.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install research@skill-sets
```

## Run

```
/research:research which Linux kernel version added the FDCAN driver
```

## Output

One Markdown file in the repo, where it already keeps such notes.

## Layout

```
skills/research/SKILL.md
```

## Source

From [mattpocock/skills](https://github.com/mattpocock/skills/tree/4ae2317ddb22b046e26455e4f9c9b3aaaf62212d/skills/engineering/research) at commit `4ae2317`, MIT
licensed, see [../LICENSE](../LICENSE). Codex metadata (`agents/`) is left
out.

Changes from upstream: Added a rule that an agent which is already a subagent does the research itself, to stop nested agents (upstream issue #530).
