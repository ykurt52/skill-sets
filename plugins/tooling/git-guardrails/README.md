# git-guardrails

A `PreToolUse` hook that keeps git under your control. The agent may run
read-only git commands; every command that can change a repository is
blocked before it runs. Your own terminal is not affected.

## Install

```
/plugin marketplace add ykurt52/skill-sets
/plugin install git-guardrails@skill-sets
```

Requires Python 3 on `PATH` as `python`. The hook is active as soon as the
plugin is enabled; there is no skill to run and no settings file to edit.

## What it checks

Every command sent to the `Bash` or `PowerShell` tool. Commands are split
on `;`, `&&`, `||`, `|`, `$(...)` and backticks, and strings passed to
`bash -c`, `powershell -Command` or `Invoke-Expression` are checked too.
Global options such as `-C <path>`, `-c key=value` and `--no-pager` are
skipped before the subcommand is read.

Allowed:

| Command | Allowed form |
| --- | --- |
| `status`, `diff`, `log`, `show`, `blame`, `grep`, `ls-files`, `ls-tree`, `rev-parse`, `rev-list`, `describe`, `shortlog`, `cat-file`, `show-ref`, `merge-base`, `name-rev`, `check-ignore` | any |
| `clone`, `ls-remote` | any (they do not touch an existing repo) |
| `branch`, `tag` | listing only (`-a`, `-v`, `-l`, `--contains`, ...) |
| `remote` | no arguments, `-v`, `show`, `get-url` |
| `stash` | `list`, `show` |
| `config` | `--get`, `--get-all`, `--get-regexp`, `--list` |
| `reflog`, `worktree list`, `submodule status`, `--version`, `help` | read forms |

Everything else is blocked: `add`, `commit`, `push`, `pull`, `fetch`,
`checkout`, `switch`, `restore`, `reset`, `clean`, `merge`, `rebase`,
`stash`, `branch <name>`, `tag <name>`, `rm`, `mv`, `init`, aliases, and
any command the hook cannot parse.

## Output

On a blocked command the hook exits with code 2 and the agent sees:

```
Blocked by git-guardrails: `git push` can change the repository. Only
read-only git commands (status, diff, log, show, ...) are allowed. Give the
command to the user to run.
```

## Limits

- It only guards the agent's shell tools. Scripts the agent runs
  (`python deploy.py` that calls git inside) are not inspected.
- It does not cover `gh` or other forges' CLIs.
- If `python` is missing the hook errors and Claude Code lets the command
  through, so check that `python --version` works.

## Test

From the plugin folder:

```
python tests/test_block_git_writes.py
```

## Layout

```
hooks/hooks.json            registers the hook for Bash and PowerShell
hooks/block_git_writes.py   the check
tests/                      allowed and blocked command list
```

Based on the idea of `git-guardrails-claude-code` in
[mattpocock/skills](https://github.com/mattpocock/skills), rewritten as an
allowlist.
