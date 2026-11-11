"""Minimal S-expression reader for KiCad files.

A node is a Python list whose first item is the tag: ['at', '10', '20'].
Quoted strings and bare atoms both come back as str.
"""
import re

_TOKEN = re.compile(r'\(|\)|"((?:[^"\\]|\\.)*)"|([^\s()"]+)')
_ESCAPE = re.compile(r'\\(.)')
_ESCAPE_MAP = {'n': '\n', 't': '\t', 'r': '\r'}


def _unescape(text):
    if '\\' not in text:
        return text
    return _ESCAPE.sub(lambda m: _ESCAPE_MAP.get(m.group(1), m.group(1)), text)


def parse(text):
    """Parse one S-expression and return its root node."""
    root = []
    stack = [root]
    for match in _TOKEN.finditer(text):
        token = match.group(0)
        if token == '(':
            node = []
            stack[-1].append(node)
            stack.append(node)
        elif token == ')':
            if len(stack) == 1:
                raise ValueError('unbalanced ")" at offset %d' % match.start())
            stack.pop()
        elif match.group(1) is not None:
            stack[-1].append(_unescape(match.group(1)))
        else:
            stack[-1].append(token)
    if len(stack) != 1 or not root:
        raise ValueError('unbalanced "(" or empty input')
    return root[0]


def load(path):
    with open(path, encoding='utf-8') as handle:
        return parse(handle.read())


def children(node, tag):
    """All direct child nodes with the given tag."""
    return [item for item in node if isinstance(item, list) and item and item[0] == tag]


def child(node, tag):
    """First direct child node with the given tag, or None."""
    for item in node:
        if isinstance(item, list) and item and item[0] == tag:
            return item
    return None


def value(node, tag, default=None):
    """First argument of the first child with the given tag."""
    found = child(node, tag)
    if found is None or len(found) < 2:
        return default
    return found[1]


def number(node, tag, default=None):
    """Like value(), converted to float."""
    found = value(node, tag)
    if found is None or isinstance(found, list):
        return default
    return float(found)
