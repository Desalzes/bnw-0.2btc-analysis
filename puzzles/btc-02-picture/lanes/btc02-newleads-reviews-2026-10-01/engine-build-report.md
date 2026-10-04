# Engine change (SCHEME bip39raw / NOCHECKSUM / PASSFILE): the Opus build agent's final report, 2026-10-01

Saved by the leads session from the agent's own final message(s) (transcript `agent-abab0597ac466a344.jsonl`), unedited.

---

_Final message of 2026-10-01T18:33:56.607Z_

I finished all of it test-first on branch `worktree-agent-abab0597ac466a344`. The final full suite ran 378 tests with no failures, errors or skips in 173.4 s, and `--selftest` ends `SELFTEST PASS`. Nothing under `kernels/` changed, and no existing test was edited.

## 1. Branch, worktree, log

Worktree: `.claude/worktrees/agent-abab0597ac466a344`
```
0b5e219 harness(btc02): non-ASCII string literals of the bip39raw change written as \u escapes
c658577 harness(btc02): the GPU rate probe (bench, dummy target + plant) on a bip39raw config, and a run that measures it
6ade8df harness(btc02): README - SCHEME bip39raw / NOCHECKSUM / PASSFILE, and the exact check that replaces the binomial one
4dcb12c harness(btc02): setperm_solve --selftest checks bip39raw - 10 L5 known answers and a 13-word find / case miss
7526cbf harness(btc02): bip39raw known answers and long non-ASCII tokens through the real K1/K2/K3
d2517f9 harness(btc02): cap K1's launch sizer at the survivor buffer for a no-filter (bip39raw) space
279d97b harness(btc02): exact survivor-count check for bip39raw (rate 1) in place of the binomial one
1b6503e harness(btc02): PASSFILE <file> - one passphrase per line, verbatim, added exactly as PASS lines are
03b37ad harness(btc02): SCHEME bip39raw (+ NOCHECKSUM alias) in the grammar, the CPU solver, sp_ref and Space
```

## 2. Diff stat

Master has 6 newer commits (new-leads templates, STATE.md, `exact_strings.py`). So `master..HEAD` lists them as deletions; they touch none of my files and the merge is clean. Against the merge base (`git diff --stat master...HEAD`):
```
 README.md 55 +-   gpu_setperm.py 31 +-   setperm_solve.py 158 ++-   sp_ref.py 4 +-   sp_space.py 15 +-
 sp_test_raw.py 737 + (new)   sp_test_run.py 170 +-   sp_testutil.py 34 +-
 8 files changed, 1179 insertions(+), 25 deletions(-)
```
There are 42 new tests: 34 in `sp_test_raw.py` and 8 in `sp_test_run.py`; 7 of them need the GPU.

## 3. Each behaviour: failing run, then what made it pass

- **Grammar, CPU solver, `sp_ref`, `Space` (03b37ad).**
  - Red: 14 of 19 new tests failed at `setperm_solve.py:121 assert cfg["scheme"] in ("bip39","electrum")` or on `unknown directive 'NOCHECKSUM'`.
  - Appending configs L–O to `small_config_texts()` made 5 existing CPU tests fail at that same assert.
  - The 4 known-answer tests through `sp_ref` passed at once. That is expected: `seed_of`, `normalize_pass` and `salt_block_words` already treat any non-electrum scheme as BIP39, and these tests now pin it.
  - Green: changes to `load_config`, `_work`, `run`, `ref_survivors` and `Space`. The 32 GPU tests that iterate the configs (K1 parity in mode 0, decode/pack probes, binomial, window audit) pass on the raw configs. A test pins configs A–K byte-identical to master's (sha256).
- **PASSFILE (1b6503e).** Red: all 6 tests failed on `unknown directive 'PASSFILE'`. Green: `read_passfile()` and the new directive.
- **Exact survivor-count check (279d97b).**
  - Red, pure function: `binomial_problem(0, 1, 1)` returned None.
  - Red, run level (the real gap): a 3,600-candidate raw block reporting one survivor too many or too few came out NO_MATCH.
  - Red, banner: it said "binomial".
  - Red, GPU tests run against an extract of the pre-change commit: a K1 that dropped a survivor was caught only by the window audit, because the sampled window happened to cover the whole block.
  - I also confirmed the old function raises `ZeroDivisionError` at 10^6 tested with one survivor missing.
  - Green: the `rate >= 1` branch, the `(exact check)` VOID label and the banner `exact survivor-count check (no filter: survivors == tested in every block)`. The filtered schemes' answers match a verbatim copy of the old function on a grid of more than 200 cases, and their banner and VOID text are byte-identical.
- **K1 sizer cap (d2517f9).**
  - Red: `s1.hi` was 33554432 instead of 4194304. At a buffer of 1000, launches of 4096, 2048, then 1024 over and over each overflowed and were redone. On the GPU, a buffer of 32 gave VOID `survivor buffer too small even for the minimum chunk`.
  - Green: for a no-filter space the sizer is `Sizer(min(4096,hi), lo=min(64,hi), hi=min(K1_MAX_CHUNK, k1.cap))`. The filtered schemes keep the original line verbatim, and a test pins it.
  - The CPU-only test uses the real `Engine.__init__` over stand-in kernels; a GPU run at buffers 32 and 1000 covers the real path.
- **Known answers and long tokens through the real K1/K2/K3 (7526cbf).** These passed on their first run because the behaviour was already in place. To show they bite, I ran them against two broken scratch copies:
  - an `electrum` salt prefix for bip39raw: both tests fail at K2;
  - BIP39 mode for raw: K1 drops V1, `(1, 1, 0) != (1, 1, 1)`.
- **`--selftest` (4dcb12c).** Red: `AttributeError: RAW_KNOWN_ANSWERS`, and no raw lines in the output. Green: the constant plus two selftest blocks.
- **README (6ade8df).** Red on a missing README phrase; green once written.
- **Rate probe on raw (c658577).** Passed on its first run; it covers the unchanged `bench`/`probe_rate` path for the new scheme.
- **0b5e219.** My edits had turned `\u` escapes into literal characters. The worst cases were an invisible BOM in `read_passfile` and two visually identical `café` strings in different Unicode forms. I rewrote them as escapes via `tokenize`; the runtime strings are identical, and the suite gave the same result before and after.

## 4. Verification

The full suite at HEAD 0b5e219 ran 378 tests: 0 failures, 0 errors, 0 skipped, 173.4 s. A production run was holding the GPU at 100% the whole time. The baseline before my change was 336 OK in 76.9 s on an idle GPU.

`setperm_solve.py --selftest` (exit 0, about 2 s; the earlier lines are unchanged):
```
  bip39raw known answers (10 L5 vector addresses: 13 words, upper case, a non-BIP39 token, no checksum, passphrase case kept, an NFC passphrase NFKD-normalised) OK
  find bip39raw 13w (MOON + breathe, no checksum) on path 2 of 2 / miss with Moon OK
SELFTEST PASS
```

## 5. Deviations and judgment calls

- **Raw test configs kept under 10,000 tested.** `MutationReviewGapsNoGpu.test_small_configs_big_enough_to_be_judged...` asserts that `binomial_problem(tested, tested, rate)` is a problem, which is false for a no-filter scheme. Staying under the floor avoids editing it. Cost if wrong: the run-level raw tests use small blocks, though the pure function is tested up to 4e9.
- **NOCHECKSUM variants.** NOCHECKSUM with no SCHEME line becomes bip39raw (bip39 is the default). `SCHEME bip39raw` plus `NOCHECKSUM` is accepted as redundant. No flag is kept, so the parsed configs are identical.
- **Extra refusal.** A raw FIX alternative whose NFKD contains `|` is refused, because re-joining would silently split it; U+FF5C is the only such character. A `|` inside REQUIRE or POOL tokens is accepted as a plain character.
- **Extra guards in `Space`, beyond the brief.**
  - An unknown scheme raises `SpaceError`; before, a mistyped scheme silently became BIP39 mode.
  - A bip39raw token that is not NFKD or holds whitespace raises `SpaceError`; `load_config` output always passes this.
- **Sizer.** Besides capping `hi`, I lowered `lo` and the starting size to fit under it, so a buffer under 64 works.
- **PASSFILE details.**
  - A line of only spaces is a passphrase.
  - Only `\n` splits lines: a lone `\r`, `\x0c` or U+2028 stays inside the passphrase.
  - Only a BOM at the very start is dropped.
  - A non-UTF-8 file, a directory, or a missing file name is refused.
  - There is no `~` expansion, and `#` on the PASSFILE line itself still starts a comment.
- **Small changes in passing.** `load_config` now reads the file inside `with open`; line handling is the same and the ResourceWarnings on refusal paths are gone. The selftest literals live in `setperm_solve.RAW_KNOWN_ANSWERS` so a test can hold them to the vector file.
- **GPU end-to-end config.** I read "2 passphrases" as two PASS lines (MOON, moon), so three in all with the empty one.
- **Test added outside the brief:** a test that pins the README text.
- **Commit order.** The small-buffer GPU test was moved into the sizer commit so every commit is green.

## 6. Unverified or risky

1. **Checkpoints stop resuming.** Merging changes `engine_hash()`, because all four engine `.py` files changed. Any checkpoint written by the current engine will be refused on resume. A production run was live during my session (`nl2_z_d12_p.conf`, checkpoint under `ledger/newleads/`). Merge between runs, or let the queue finish on the old code.
2. **`ck_stats.py` (untouched) crashes at rate 1.** It divides by `sqrt(exp*(1-p))`, which is zero for a raw run. For raw checkpoints the right check is valid == tested in every block.
3. **CR-only passphrase files.** Following the brief, a CR-only (classic Mac) file becomes one passphrase: refused if over 99 bytes, otherwise silently wrong. Master's new `exact_strings.py --passfile` does split on a lone `\r`, so the two tools read such a file differently. Refusing any PASSFILE line that still contains `\r` would close this.
4. **Per-token NFKD.** I normalise each token rather than the joined text. That is equivalent because the joining space is a starter that canonical reordering never crosses; I argued it and tested specific tokens, not every character. Python 3.12 here uses Unicode 15.0, while Electrum 3.3.8 users ran Unicode 9–11. Recently assigned characters could normalise differently, which is an exotic case.
5. **Electrum semantics are not independently re-checked.** I took them from the brief and did not re-read Electrum's source. The L5 vectors and `kav_independent.py` use the same reading, so a misreading of the wallet would be shared by both.
6. **Texts that cannot be expressed.** A raw token cannot contain `#` or whitespace, and a FIX alternative cannot contain `|`. 52 non-whitespace characters (such as `¨` and `´`) gain a space under NFKD and are refused; that blocks such texts outright rather than giving a false NO MATCH. On the GPU a token is at most 16 bytes after NFKD (8 Cyrillic letters) and the joined text at most 239 bytes.
7. **Limits of the exact check.** It sees only K1's own counters. With DISTINCT, a K1 that skips candidates before counting `tested` is invisible to it, just as it was to the binomial check. A K1 that reports correct counts but stores wrong indices is caught only by the sampled window audit and the plants; that residual is unchanged.
8. **Cost.** Every raw candidate is derived: 16× a BIP39-12 space of the same size, 256× an Electrum one. Blocks are at least one unit, so they can be coarse: a dry run of a 13-word raw config gave 12 blocks of about 42M derivations each.
9. **Window audit with a small buffer.** It is skipped and counted when the survivor buffer is smaller than its window. That only happens with a non-default buffer.

Scratch logs, the mutant copies and probe scripts are in the worktree's gitignored `scratch/`.
