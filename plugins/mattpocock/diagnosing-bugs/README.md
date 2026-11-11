# diagnosing-bugs

A step-by-step discipline for hard bugs. First a fast pass/fail reproduction, then ranked hypotheses tested one at a time with instrumentation, then the fix with a regression check. Secrets are redacted from what it shows.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install diagnosing-bugs@skill-sets
```

## Run

```
/diagnosing-bugs:diagnosing-bugs UART drops bytes above 460800 baud
```

## Output

The reproduction, the confirmed cause and the fix, in the chat. `scripts/hitl-loop.template.sh` is a template for steps only you can do.

## Layout

```
skills/diagnosing-bugs/SKILL.md
skills/diagnosing-bugs/scripts/hitl-loop.template.sh
```

## Source

From [mattpocock/skills](https://github.com/mattpocock/skills/tree/4ae2317ddb22b046e26455e4f9c9b3aaaf62212d/skills/engineering/diagnosing-bugs) at commit `4ae2317`, MIT
licensed, see [../LICENSE](../LICENSE). Codex metadata (`agents/`) is left
out.

Changes from upstream: The agent writes the `git bisect run` script but gives the command to you instead of running it.
