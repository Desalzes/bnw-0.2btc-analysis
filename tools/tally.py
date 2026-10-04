#!/usr/bin/env python3
"""Recompute the run-ledger totals (stdlib only).

    python3 tools/tally.py                              # the whole ledger
    python3 tools/tally.py --until 2026-10-02T07:12     # a cut-off; minute precision includes the whole minute
    python3 tools/tally.py --since 2026-10-01T13:51 --until 2026-10-01T23:00
    python3 tools/tally.py --windows                    # the windows table of TESTED.md

A "run" is a NO_MATCH record. Exit status 2, with a loud message, if the ledger holds a FOUND or VOID record.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEDGER = os.path.join(HERE, "..", "puzzles", "btc-02-picture", "ledger", "runs.jsonl")

# The windows table of TESTED.md, in ledger time; both ends are minute-inclusive.
WINDOWS = (
    ("to 09-29 17:12 (first day of the GPU engine)", None, "2026-09-29T17:12"),
    ("09-29 17:13 - 24:00", "2026-09-29T17:13", "2026-09-29T23:59"),
    ("09-30", "2026-09-30T00:00", "2026-09-30T23:59"),
    ("10-01 00:00 - 13:50", "2026-10-01T00:00", "2026-10-01T13:50"),
    ("10-01 13:50 - 23:00 (new-leads queue, before the released block)", "2026-10-01T13:51", "2026-10-01T23:00"),
    ("10-01 23:00 - 10-02 07:10 (the released block)", "2026-10-01T23:01", "2026-10-02T07:12"),
)


def _norm(ts, end):
    """'YYYY-MM-DDTHH:MM' -> first (or, for end=True, last) second of that minute; a full timestamp is kept."""
    if ts is None:
        return None
    if len(ts) == 16:
        return ts + (":59" if end else ":00")
    if len(ts) == 19:
        return ts
    raise ValueError(f"timestamp {ts!r}: use YYYY-MM-DDTHH:MM or YYYY-MM-DDTHH:MM:SS")


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{n}: not JSON ({e})")
    return rows


def select(rows, since=None, until=None):
    lo, hi = _norm(since, False), _norm(until, True)
    return [r for r in rows if (lo is None or r["ts"] >= lo) and (hi is None or r["ts"] <= hi)]


def summarize(rows):
    done = [r for r in rows if r["status"] == "NO_MATCH"]
    first = {}
    for r in done:
        first.setdefault(r["sig"], r)
    return {
        "records": len(rows),
        "starts": sum(1 for r in rows if r["status"] == "START"),
        "runs": len(done),
        "tested": sum(r["tested"] for r in done),
        "valid": sum(r["valid"] for r in done),
        "distinct_sigs": len(first),
        "tested_once": sum(r["tested"] for r in first.values()),
        "plants": sum(r.get("plants_found", 0) for r in done),
        "first_ts": min((r["ts"] for r in rows), default=None),
        "last_ts": max((r["ts"] for r in rows), default=None),
        "other": sorted({r["status"] for r in rows} - {"START", "NO_MATCH"}),
    }


def format_summary(s):
    return "\n".join([
        f"records           {s['records']:,}  (START {s['starts']:,}, NO_MATCH {s['runs']:,})",
        f"first / last      {s['first_ts']} / {s['last_ts']}",
        f"runs (NO_MATCH)   {s['runs']:,}",
        f"tested            {s['tested']:,}",
        f"valid             {s['valid']:,}",
        f"distinct sigs     {s['distinct_sigs']:,}  (tested, summed once per signature: {s['tested_once']:,})",
        f"planted seeds     {s['plants']:,} found",
    ])


def format_windows(rows):
    out = [f"{'window (ledger time)':68s} {'runs':>5s} {'tested':>17s} {'valid':>15s}"]
    for name, lo, hi in WINDOWS:
        s = summarize(select(rows, lo, hi))
        out.append(f"{name:68s} {s['runs']:5d} {s['tested']:17,d} {s['valid']:15,d}")
    s = summarize(select(rows, None, WINDOWS[-1][2]))
    out.append(f"{'all (to ' + WINDOWS[-1][2] + ')':68s} {s['runs']:5d} {s['tested']:17,d} {s['valid']:15,d}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--windows", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.ledger):
        print(f"no ledger at {args.ledger}", file=sys.stderr)
        return 1
    rows = load(args.ledger)
    bad = sorted({r["status"] for r in rows} - {"START", "NO_MATCH"})
    if bad:
        print(f"*** the ledger holds {', '.join(bad)} record(s): stop and read STATE.md before anything else ***",
              file=sys.stderr)
        return 2
    print(f"ledger            {os.path.relpath(args.ledger)}")
    print(format_windows(rows) if args.windows else format_summary(summarize(select(rows, args.since, args.until))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
