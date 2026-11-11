# code-review

Takes the diff from a fixed point to `HEAD` and reviews it twice: once against the coding standards documented in the repo, once against the spec or issue the work came from. Both run in parallel and are reported side by side.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install code-review@skill-sets
```

## Run

```
/code-review:code-review since main
```

## Output

Two lists of findings, Standards and Spec, in the chat. It only reads git (`diff`, `log`, `rev-parse`).

## Layout

```
skills/code-review/SKILL.md
```

## Source

From [mattpocock/skills](https://github.com/mattpocock/skills/tree/4ae2317ddb22b046e26455e4f9c9b3aaaf62212d/skills/engineering/code-review) at commit `4ae2317`, MIT
licensed, see [../LICENSE](../LICENSE). Codex metadata (`agents/`) is left
out.

Changes from upstream: Without an issue tracker it finds the spec in the commit messages or asks for its path, instead of pointing to a setup skill that is not included.
