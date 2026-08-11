#!/usr/bin/env python3
"""Rebuild review pages from session_feed.

    python3 tools/build_review.py                 # the most recent session
    python3 tools/build_review.py e2e_20260808_2  # one session, prefix is enough
    python3 tools/build_review.py --all           # every session that has reports
    python3 tools/build_review.py --all --index   # ...and an index over them

Normally you do not need this: `session.feed.publish` rebuilds a session's page
on every report, so it is already current while the robot is running. Use this
after editing the template, or to build pages for sessions recorded before that
hook existed.

The page itself, and the reasoning about what is on it, is in session/review.py.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from session.review import FEED, build, build_index, _records  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("session", nargs="?", default="",
                    help="session folder name or a unique prefix of one")
    ap.add_argument("--all", action="store_true", help="build every session")
    ap.add_argument("--index", action="store_true",
                    help="also write session_feed/index.html (researcher only)")
    a = ap.parse_args()

    every = sorted(d for d in glob.glob(os.path.join(FEED, "e2e_*"))
                   if os.path.isdir(d))
    if a.all:
        dirs = every
    elif a.session:
        dirs = [d for d in every if os.path.basename(d).startswith(a.session)]
        if not dirs:
            sys.exit(f"no session matching {a.session!r} under {FEED}")
    else:
        dirs = [d for d in every if _records(d)][-1:]
        if not dirs:
            sys.exit("no session has any reports yet")

    built = 0
    for d in dirs:
        if build(d):
            built += 1
            print(f"  {len(_records(d)):2} report(s)  ->  {d}/review.html")
    if not built:
        print("  nothing to build: no reports in the selected session(s)")
    if a.index:
        print(f"  index          ->  {build_index(every)}")


if __name__ == "__main__":
    main()
