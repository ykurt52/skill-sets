# grilling

Asks the open decisions of a plan in rounds. Each question comes with a recommended answer, so "yes" accepts it. Facts are looked up by the agent, decisions are left to you. Nothing is built until you confirm the plan is settled.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install grilling@skill-sets
```

## Run

```
/grilling:grilling SPI DMA driver design for the ADC
```

## Output

The questions and the agreed decisions, in the chat.

## Layout

```
skills/grilling/SKILL.md
```

## Source

From [mattpocock/skills](https://github.com/mattpocock/skills/tree/4ae2317ddb22b046e26455e4f9c9b3aaaf62212d/skills/productivity/grilling) at commit `4ae2317`, MIT
licensed, see [../LICENSE](../LICENSE). Codex metadata (`agents/`) is left
out.

Changes from upstream: None. Copied as published.
