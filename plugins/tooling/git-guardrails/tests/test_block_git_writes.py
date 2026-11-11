"""Run: python tests/test_block_git_writes.py  (from the plugin folder)"""
import json
import os
import subprocess
import sys

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'hooks',
                    'block_git_writes.py')

ALLOWED = [
    'git status',
    'git diff HEAD~3 -- src/',
    'git log --oneline -20',
    'git show abc123',
    'git -C /tmp/repo status',
    'git --no-pager log -p',
    'git -c color.ui=never diff',
    'git blame file.c',
    'git rev-parse HEAD',
    'git branch',
    'git branch -a -v',
    'git branch --contains abc123',
    'git branch --show-current',
    'git tag',
    'git tag -l "v1.*"',
    'git remote -v',
    'git remote show origin',
    'git stash list',
    'git config --get user.name',
    'git config --list',
    'git reflog',
    'git worktree list',
    'git --version',
    'git clone https://github.com/a/b /tmp/b',
    'git ls-remote --heads https://github.com/a/b',
    'C:/Program\\ Files/Git/cmd/git.exe status',
    'ls -la && git status | head',
    'grep -rn git src/',
    'echo "see git docs"',
    'Get-ChildItem; git log -1',
    'python build.py',
]

BLOCKED = [
    'git commit -m "x"',
    'git add .',
    'git push',
    'git push --force origin main',
    'git -C repo push',
    'git --git-dir=.git commit',
    'git reset --hard',
    'git clean -fd',
    'git checkout main',
    'git checkout .',
    'git switch dev',
    'git restore .',
    'git pull',
    'git fetch',
    'git merge dev',
    'git rebase main',
    'git cherry-pick abc',
    'git revert abc',
    'git stash',
    'git stash pop',
    'git branch new-feature',
    'git branch -D old',
    'git tag v1.0',
    'git tag -a v1.0 -m x',
    'git remote add up url',
    'git config user.name x',
    'git config --unset user.name',
    'git reflog expire --all',
    'git worktree add ../x',
    'git init',
    'git rm file',
    'git mv a b',
    'git am p.patch',
    'git apply p.patch',
    'git gc',
    'git st',
    'git --frobnicate status',
    'ls && git commit -am x',
    'git status; git push',
    'echo $(git commit -m x)',
    'sudo git push',
    'GIT_DIR=x git commit',
    'bash -c "git push origin main"',
    'powershell -Command "git commit -m x"',
    'Invoke-Expression "git push"',
    '"C:/Program Files/Git/cmd/git.exe" push',
    'GIT commit',
]


def run(command):
    event = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}})
    result = subprocess.run([sys.executable, '-I', HOOK], input=event,
                            capture_output=True, text=True)
    return result.returncode


def main():
    failures = []
    for command in ALLOWED:
        if run(command) != 0:
            failures.append('should allow: ' + command)
    for command in BLOCKED:
        if run(command) != 2:
            failures.append('should block: ' + command)
    for bad_input in ('not json', '{}', '{"tool_input": {"command": 5}}'):
        result = subprocess.run([sys.executable, '-I', HOOK], input=bad_input,
                                capture_output=True, text=True)
        if result.returncode != 2:
            failures.append('should block bad input: ' + bad_input)
    total = len(ALLOWED) + len(BLOCKED) + 3
    print('%d/%d passed' % (total - len(failures), total))
    for failure in failures:
        print('  FAIL', failure)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
