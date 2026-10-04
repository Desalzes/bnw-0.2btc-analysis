# Third-party material

## OpenCL kernels: fetched, not redistributed

`puzzles/btc-02-picture/harness/` needs 14 kernels from
[johncantrell97/bip39-solver-gpu](https://github.com/johncantrell97/bip39-solver-gpu) (`cl/`, commit
`c820aff3e16e80839ce24c53734a8b02daadd138`). That repository has no licence file, so no right to redistribute them is
granted, and they are not in this repository. `python3 tools/fetch_kernels.py` downloads them from the author's own
repository at that commit, checks each file against `harness/kernels/UPSTREAM.sha256`, inserts one function of ours
(`harness/kernels/address_p2pkh.part`: 20 lines of standard base58check construction, written against the upstream's
helper API) into `address.cl`, and checks the assembled file too. 13 of the 14 files are byte-identical to upstream;
`address.cl` is upstream plus that function. If the upstream disappears, the hash file still identifies the exact
bytes of the current engine (hash `9cb088b6d185f485`). The two earlier engine eras of the ledger cannot be rebuilt from
this repository (README).

The engine's own kernels, `harness/kernels/sp_*.cl`, are the project's code: a line comparison against the fetched kernels
(2026-10-03, every line of 30 or more characters) found that they share no line of 30 or more characters with them (0 of 220).

`puzzles/btc-02-picture/harness/kernels/solve_perm.cl` is withheld: 18 of its 82 lines of 30 or more characters are also in the
upstream's `int_to_address.cl` (the PBKDF2 and HMAC loop), so it is not treated as the project's own. Only the legacy `gpu_solve.py` loads
it, and it is not part of the engine hash. `gpu_solve.py` is published for the record and does not run without it.

## BIP-0039 English wordlist

`puzzles/btc-02-picture/harness/english.txt` is the BIP-0039 English wordlist (bitcoin/bips, BIP-0039).

## The puzzle image

`puzzles/btc-02-picture/artifacts/puzzle.png` is the puzzle's artwork, posted to Reddit by u/stsh_n on 2020-10-08
(sha256 `d0b04378f75d63997b8034ec2ef1bdd108178e4546de78237bd35abf4189a782`). It is not ours and is not covered by
LICENSE or LICENSE-DATA. It is here so the forensics in the report reproduce; if you own it and want it removed, open
an issue. Crops and other derived images under `artifacts/` are analysis of that artwork.

## Agent-written material

`puzzles/btc-02-picture/lanes/` holds reviews and audits of the engine written by AI agents for the operator. They may quote third
parties.

## Python packages (not included)

coincurve, numpy and pyopencl are installed by the commands in README.md, under their own licences.
