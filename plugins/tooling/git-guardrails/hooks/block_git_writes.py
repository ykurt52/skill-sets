#!/usr/bin/env python
"""PreToolUse hook: let the agent run read-only git commands only.

Reads the hook event JSON from stdin. Exit 0 allows the tool call.
Exit 2 blocks it and the stderr text is shown to the agent.

Anything that cannot be parsed is blocked.
"""
import json
import re
import shlex
import sys

READ_ONLY = {
    'blame', 'cat-file', 'check-ignore', 'clone', 'count-objects', 'describe',
    'diff', 'grep', 'help', 'log', 'ls-files', 'ls-remote', 'ls-tree',
    'merge-base', 'name-rev', 'rev-list', 'rev-parse', 'shortlog', 'show',
    'show-branch', 'show-ref', 'status', 'var', 'version', 'whatchanged',
}

# Global options that take a separate value: git -C <path> status
GLOBAL_WITH_VALUE = {'-C', '-c', '--git-dir', '--work-tree', '--namespace',
                     '--exec-path', '--config-env', '--super-prefix'}
GLOBAL_FLAGS = {'--no-pager', '-p', '--paginate', '-P', '--bare',
                '--no-replace-objects', '--literal-pathspecs',
                '--glob-pathspecs', '--noglob-pathspecs', '--icase-pathspecs',
                '--no-optional-locks'}

# Commands that may run git as their argument: sudo git push
WRAPPERS = {'sudo', 'doas', 'command', 'builtin', 'exec', 'time', 'nice',
            'nohup', 'env', 'xargs', 'timeout', 'stdbuf', 'ionice'}

SEPARATORS = re.compile(r'\$\(|&&|\|\||[;&|\n\r(){}`]')
GIT_WORD = re.compile(r'(?i)(?<![\w.-])git(?:\.exe)?(?![\w.-])')

BRANCH_WRITE = {'-d', '-D', '-m', '-M', '-c', '-C', '-f', '-u', '--delete',
                '--move', '--copy', '--force', '--set-upstream-to',
                '--unset-upstream', '--edit-description', '--track',
                '--no-track', '--create-reflog'}
BRANCH_LIST = {'-l', '--list', '--contains', '--no-contains', '--merged',
               '--no-merged', '--points-at', '--show-current'}
TAG_WRITE = {'-a', '-s', '-u', '-f', '-d', '-m', '-F', '-e', '--annotate',
             '--sign', '--local-user', '--force', '--delete', '--message',
             '--file', '--edit', '--create-reflog'}
TAG_LIST = {'-l', '--list', '-n', '--contains', '--no-contains', '--merged',
            '--no-merged', '--points-at', '-v', '--verify'}
CONFIG_WRITE = {'--unset', '--unset-all', '--add', '--replace-all', '--edit',
                '-e', '--rename-section', '--remove-section', 'set', 'unset',
                'edit', 'rename-section', 'remove-section'}
CONFIG_READ = {'--get', '--get-all', '--get-regexp', '--get-urlmatch',
               '--list', '-l', 'get', 'list'}


class Blocked(Exception):
    pass


def split_words(text):
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return [word.strip('\'"') for word in text.split()]


def option_name(word):
    return word.split('=', 1)[0]


def positionals(args):
    return [arg for arg in args if not arg.startswith('-')]


def is_git(word):
    name = re.split(r'[\\/]', word)[-1].lower()
    return name in ('git', 'git.exe')


def git_positions(words):
    """Indexes of words that start a git command, not words that mention git."""
    found = []
    for index, word in enumerate(words):
        if not is_git(word):
            continue
        before = words[:index]
        if all(re.fullmatch(r'\w+=.*', w) or w in WRAPPERS
               or w.startswith('-') or w.isdigit() for w in before):
            found.append(index)
    return found


def subcommand(args):
    """Split args after 'git' into (subcommand, its args)."""
    index = 0
    while index < len(args):
        word = args[index]
        name = option_name(word)
        if name in GLOBAL_WITH_VALUE:
            index += 1 if '=' in word else 2
        elif name in GLOBAL_FLAGS:
            index += 1
        elif word in ('--version', '--help', '-h'):
            return 'version', []
        elif word.startswith('-'):
            raise Blocked('unknown git option ' + word)
        else:
            return word, args[index + 1:]
    return 'help', []


def is_read_only(command, args):
    flags = {option_name(arg) for arg in args if arg.startswith('-')}
    words = positionals(args)
    if command in READ_ONLY:
        return True
    if command == 'branch':
        if flags & BRANCH_WRITE:
            return False
        return bool(flags & BRANCH_LIST) or not words
    if command == 'tag':
        if flags & TAG_WRITE:
            return False
        return bool(flags & TAG_LIST) or not words
    if command == 'remote':
        return not words or words[0] in ('show', 'get-url')
    if command == 'stash':
        return bool(words) and words[0] in ('list', 'show')
    if command == 'worktree':
        return bool(words) and words[0] == 'list'
    if command == 'submodule':
        return bool(words) and words[0] in ('status', 'summary')
    if command == 'reflog':
        return not words or words[0] not in ('expire', 'delete', 'drop')
    if command == 'config':
        if (flags | set(words[:1])) & CONFIG_WRITE:
            return False
        return bool((flags | set(words[:1])) & CONFIG_READ)
    return False


def check(text, depth=0):
    if depth > 4:
        raise Blocked('command is nested too deep to check')
    if not GIT_WORD.search(text):
        return
    for segment in SEPARATORS.split(text):
        if not GIT_WORD.search(segment):
            continue
        words = split_words(segment)
        for start in git_positions(words):
            command, args = subcommand(words[start + 1:])
            if not is_read_only(command, args):
                raise Blocked('`git %s` can change the repository' % command)
        # Strings passed to bash -c, powershell -Command, Invoke-Expression
        for word in words:
            if ' ' in word and GIT_WORD.search(word):
                check(word, depth + 1)


def main():
    try:
        event = json.load(sys.stdin)
        command = event['tool_input']['command']
        if not isinstance(command, str):
            raise Blocked('command is not text')
        check(command)
    except Blocked as reason:
        sys.stderr.write(
            'Blocked by git-guardrails: %s. Only read-only git commands '
            '(status, diff, log, show, ...) are allowed. Give the command to '
            'the user to run.\n' % reason)
        return 2
    except Exception as error:  # noqa: BLE001
        sys.stderr.write('Blocked by git-guardrails: could not check the '
                         'command (%s).\n' % error)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
