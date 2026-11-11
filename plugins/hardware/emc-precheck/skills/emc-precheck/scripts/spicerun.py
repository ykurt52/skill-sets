#!/usr/bin/env python
"""Run an ngspice deck without a window and read back the saved vectors.

Uses the ngspice program when it is on the path, otherwise the ngspice
shared library (KiCad installs one). The deck must write its results with
`wrdata` and `set wr_singlescale`.

As a program it runs one deck through the shared library:
  python spicerun.py DECK
"""
import ctypes
import ctypes.util
import glob
import os
import shutil
import subprocess
import sys


def find_library():
    """Path of an ngspice shared library, or None."""
    patterns = [os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'KiCad', '*', 'bin',
                             'ngspice.dll'),
                os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'), 'KiCad', '*', 'bin',
                             'libngspice*.dll'),
                '/Applications/KiCad/KiCad.app/Contents/Frameworks/libngspice*.dylib']
    found = [path for pattern in patterns for path in glob.glob(pattern)]
    if found:
        return sorted(found)[-1]
    return ctypes.util.find_library('ngspice')


def find_simulator():
    """('program', path), ('library', path) or (None, None)."""
    program = shutil.which('ngspice') or shutil.which('ngspice_con')
    if program:
        return 'program', program
    library = find_library()
    return ('library', library) if library else (None, None)


def run_in_library(deck, library):
    """Source a deck in the shared library. Returns the simulator messages."""
    if hasattr(os, 'add_dll_directory') and os.path.isabs(library):
        os.add_dll_directory(os.path.dirname(library))
    ngspice = ctypes.CDLL(library)
    messages = []
    text_callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p)
    exit_callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_bool, ctypes.c_bool, ctypes.c_int,
                                     ctypes.c_void_p)
    running_callback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_bool, ctypes.c_int, ctypes.c_void_p)

    def on_text(text, _ident, _user):
        messages.append(text.decode('latin-1'))
        return 0

    keep = (text_callback(on_text), text_callback(lambda *_: 0), exit_callback(lambda *_: 0),
            running_callback(lambda *_: 0))
    ngspice.ngSpice_Init(keep[0], keep[1], keep[2], None, None, keep[3], None)
    ngspice.ngSpice_Command(('source ' + deck.replace('\\', '/')).encode())
    return messages


def run(deck):
    """Run a deck. Returns (ok, messages). Raises RuntimeError when no simulator is installed."""
    kind, path = find_simulator()
    if kind is None:
        raise RuntimeError('No ngspice found. Install ngspice, or KiCad, which ships its library.')
    if kind == 'program':
        result = subprocess.run([path, '-b', deck], capture_output=True, text=True, errors='replace')
    else:
        # a fresh process per run keeps the library state from leaking between decks
        result = subprocess.run([sys.executable, os.path.abspath(__file__), deck], capture_output=True,
                                text=True, errors='replace')
    messages = (result.stdout + result.stderr).splitlines()
    return result.returncode == 0, messages


def read_table(path):
    """Columns of a wrdata file written with wr_singlescale: first column is time."""
    columns = None
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if not parts:
                continue
            values = [float(item) for item in parts]
            if columns is None:
                columns = [[] for _ in values]
            for column, item in zip(columns, values):
                column.append(item)
    return columns or []


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    library_path = find_library()
    if library_path is None:
        sys.exit('ngspice shared library not found')
    for message in run_in_library(sys.argv[1], library_path):
        print(message)
