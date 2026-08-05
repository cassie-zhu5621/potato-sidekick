"""No undefined names anywhere in the runkit.

WHY THIS EXISTS. Two NameErrors reached the robot on 2026-08-05 -- `dt` and
`provider_name` -- both introduced by adding a call inside a function without
adding its import at module level. Every check in use at the time passed:

    python3 -m py_compile <file>     syntax only; a missing name is legal syntax
    import <module>                  runs module top level only, not function
                                     bodies, so a name used inside a function is
                                     never resolved
    python3 -m pytest                the failing paths need a camera and a robot

So the file compiled, the module imported, the suite was green, and the planner
died on its first real call. Both were found by a participant-facing failure,
which is the most expensive place to find them.

pyflakes resolves names WITHOUT executing anything, which is exactly the gap.

`motion/blender/` is excluded: those scripts run inside Blender and reference
`bpy`, which is not importable here and would be reported as undefined.
"""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _python_files():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                         capture_output=True, text=True)
    files = [f for f in out.stdout.split() if f]
    return [f for f in files if not f.startswith("motion/blender/")]


def test_no_undefined_names():
    pytest.importorskip("pyflakes", reason="pip install pyflakes")
    files = _python_files()
    assert files, "git ls-files found no python files"
    res = subprocess.run([sys.executable, "-m", "pyflakes", *files],
                         cwd=ROOT, capture_output=True, text=True)
    bad = [l for l in (res.stdout + res.stderr).splitlines()
           if "undefined name" in l]
    assert not bad, ("undefined names -- these compile, import and test green, "
                     "and fail at runtime:\n  " + "\n  ".join(bad))
