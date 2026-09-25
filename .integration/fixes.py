#!/usr/bin/env python3
"""Small verified corrections found by the integrated macOS build/tests."""
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
p = ROOT / 'Sources/Search/Browser.swift'
s = p.read_text()
old = 'row.tabs.prefix(while: { $0.pinned }).count'
new = 'row.tabs.prefix(while: { $0.pin != nil }).count'
if old in s:
    assert s.count(old) == 1
    p.write_text(s.replace(old, new))
else:
    assert new in s
p = ROOT / '.gitignore'
s = p.read_text()
if '/.build-personal-cross/' not in s:
    p.write_text(s.rstrip() + '\n/.build-personal-cross/\n__pycache__/\n')
