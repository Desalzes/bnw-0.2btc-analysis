# 0.2 BTC "Brave New World" puzzle: GPU search code and test record

Target: [`1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ`](https://mempool.space/address/1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ) · still unsolved. The prize (20,107,284 sat in 5 outputs) has never been spent; read from the chain on 2026-10-03.

This repository holds the GPU code that searched candidate seed phrases for the puzzle, with its tests and independent checks, and a
record of what it tested, so that a "NO MATCH" can be checked and not just believed. It contains no solution. Record cutoff
**2026-10-02 07:12** (local time); later runs are not in this export.

**[What was tested (TESTED.md)](TESTED.md):** 718 verified GPU runs, 1.48 × 10¹² candidates (1,479,268,494,119), every finished run NO
MATCH, with what each family of runs excludes and what it does not. The figures are recomputed from the run ledger by `python3 tools/tally.py`.

## What is here

    TESTED.md                   what was tested, family by family, and what a NO MATCH means
    tools/                      tally.py (the totals), check_target.py, fetch_kernels.py
    expected_output/            what each tool prints, the engine hash, the CPU test result
    docs/ADR-0001.md            the rails: read-only, never a transaction, a candidate key stops everything
    puzzles/btc-02-picture/
        harness/                the GPU engine (gpu_setperm.py, sp_*), its tests, the independent verifiers, the
                                earlier solvers, and the committed configs that were run (templates/)
        ledger/                 the run ledger (runs.jsonl) and the queue logs, which name each config as it finishes
        lanes/                  independent reviews and audits of the engine (agent-written)
        artifacts/              the puzzle image, the clock and rune crops, and four small test inputs the code reads

Paths in the documents are relative to `puzzles/btc-02-picture/`. The reviews in `lanes/` cite `scratch/...`, `HANDOFF*.md` and other
working files of the private tree; those are **not published** (`PUBLICATION.md` counts what was done to produce this copy). A few
configs that were run from scratch directories, the `gd_*` and `wh_x10_*` families, were never committed and are not here either.

## Why the runs can be trusted

A NO MATCH here is a claim about one config, and the engine shows it can see: every block carries 64 planted seeds that must be found in
place (one per block in the earliest run, E4); the engine went through independent agent reviews and mutation tests; and TESTED.md
spells out what a NO MATCH does *not* exclude. It is sampled evidence, not proof.

## Reproducing

The CPU-only checks need Python 3 and nothing else:

    python3 tools/check_target.py       # the target decodes to HASH160 ccbd031e…
    python3 tools/tally.py              # must equal expected_output/tally.txt
    python3 tools/tally.py --windows    # the windows table of TESTED.md
    sha256sum -c MANIFEST.sha256

The engine needs three Python packages and 14 third-party OpenCL kernels. The kernels are **not** in this repository (their upstream has
no licence), so fetch them: pinned to one commit and checked file by file.

    python3 tools/fetch_kernels.py
    cd puzzles/btc-02-picture/harness
    uv run --no-project --python 3.12 --with coincurve --with numpy --with pyopencl python -c "import gpu_setperm as g; print(g.engine_hash())"   # 9cb088b6d185f485
    SP_NO_GPU=1 uv run --no-project --python 3.12 --with coincurve --with numpy --with pyopencl python gpu_setperm_test.py   # Ran 383 tests ... OK (skipped=115)
    uv run --no-project --python 3.12 --with coincurve --with numpy --with pyopencl python gpu_setperm.py templates/e4_pins_only.conf --dry-run   # prices a search, runs none

`9cb088b6d185f485` is the engine this tree holds. The ledger spans three engine eras (`4e905055f3f5fdaf`, `e9afbdfd40803095` and
`9cb088b6d185f485`), and a run never straddles two. The first two engines are not in this repository, so a run of those eras cannot be
reproduced from here.

A real search needs a written sign-off, a deliberate speed bump that is logged with the run:
`gpu_setperm.py CONFIG --checkpoint scratch/x.ck --signoff "who approved what, and when"`. `--bench` runs a dummy target with a planted seed
and prints the real rate. The engine only compares addresses: it never builds, signs or broadcasts a transaction, and on a hit it writes a
file and stops (`docs/ADR-0001.md`).

## Prior work

The same puzzle is analysed in [rarsn4/blm-0.2btc-analysis](https://github.com/rarsn4/blm-0.2btc-analysis); this repository's layout follows it.

## Contact

Questions, corrections, or a lead to test: open an issue on this repository.

## Support

If this helped you figure it out and you feel generous:

- **BTC:** `bc1qptzpgk0z33nku9xrh8duay4jgt654uh65cc9rr`
- **ETH:** `0xd2C2408E370293aC7ac116512C1c57071B9FA5B6`

Optional. Everything here is public either way.

## Provenance

A working record kept by the operator together with AI coding agents (Claude). Code is MIT (`LICENSE`); text and data are CC BY 4.0
(`LICENSE-DATA`); the puzzle image is the artist's, not ours, and the third-party kernels are fetched, not redistributed
(`THIRD-PARTY.md`). `PUBLICATION.md` says, by count, what was done to the working tree to produce this copy.
