# Independent adversarial review of the raw scheme: the Opus reviewer's final report, 2026-10-01

Saved by the leads session from the agent's own final message(s) (transcript `agent-a513f0daa8a4d736a.jsonl`), unedited.

---

_Final message of 2026-10-01T19:08:19.437Z_

**Verdict:** The engine is safe to run on the configs committed today. It derives exactly what Electrum 3.3.8 derives, and its new controls catch every fault I injected. It is not yet safe as a free-text tool: a `#` inside any token is silently read as a comment, so a run can search a smaller space than the config states and still report `NO MATCH (verified)`. Fix that before writing new bip39raw configs.

Master already contains this change: the operator merged it as 9a1a1f7 at 14:39, while this review was running. Master's engine files are identical to 0b5e219, so everything below applies to master. I did not commit or edit anything.

## Findings

| # | Sev | Where | What | Evidence | Smallest fix |
|---|---|---|---|---|---|
| F1 | **CRITICAL** (not triggered yet: needs a `#` in a token) | `setperm_solve.py:163` (`raw.split("#",1)`), lines 173–179 | A `#` inside a FIX, REQUIRE or POOL token cuts the rest of the line, with no warning. | Config `FIX 1 MOON / FIX 2 breathe / POOL zoo C# F# / PASS #moon` parses to the space `['MOON breathe zoo','MOON breathe C']` with passphrases `['']`. The stated phrase is not in it, and `--dry-run` only shows "raw 2". `REQUIRE #1 breathe` gives REQUIRE `[]`. `FIX 1 MOON\|M#N\|moon` gives `'MOON\|M'`. Plants, self-test and window audit all work on the parsed space, so none of them can see this. Over all code points, `#` is the only character the parser silently changes inside a token. None of the 736 committed configs has a `#` on a FIX/REQUIRE/POOL/PASS line. | Refuse any `#` on FIX/REQUIRE/POOL/PASS lines; comments go on their own line. Failing test: `cfg_from_text(make_text("bip39raw",3,[(1,"MOON")],["breathe"],["zoo C#"]))` must raise SystemExit (today it returns pool `['zoo','C']`). |
| F2 | MINOR | `setperm_solve.py:179` | Only an exact upper-case `POOL @WORDLIST` expands. Under bip39raw, `POOL @wordlist`, `REQUIRE @WORDLIST` and `FIX n @WORDLIST` become literal tokens; plain bip39 used to refuse them. | `POOL @wordlist` gives pool `['@wordlist']`, total 2. | Refuse raw tokens starting with `@`, except `POOL @WORDLIST`. |
| F3 | MINOR (older grammar, easier to hit now) | lines 172–174, 180–182 | A FIX slot given twice: the last one silently wins. PASS lines: `#…` is cut off, trailing spaces are dropped, an extra leading space is kept. | Passphrases come out as `['', 'my', ' two-spaces', 'trailing']`. | Refuse duplicate FIX slots. F1's refusal also covers PASS lines; point people to PASSFILE. |
| F4 | MINOR | line 148 | A PASSFILE with lone-CR line endings becomes one passphrase `a\rb\rc` instead of being refused. This is documented and a test pins it. | — | Refuse lines holding `\r`, `\x0b`, `\x0c`, `\x1c`–`\x1e`, `\x85`, U+2028 or U+2029. |
| F5 | MINOR (README claim too strong) | `README.md:193-197` | "It sees a K1 that loses or invents even one survivor" is only true without DISTINCT. With DISTINCT, nothing checks `tested`. | The new test runs config M (546 of 867 candidates pass DISTINCT) with a stand-in reporting tested = valid = 864, and the run ends `NO_MATCH`. | Correct the README. Or make it exact: the DISTINCT count per unit does not depend on where the REQUIRE words are placed, so `tested` must equal units-in-block × that per-unit count. |
| F6 | MINOR (cheap improvement) | `gpu_setperm.py:252-294` | For bip39raw without DISTINCT, every K1 chunk's survivor set is exactly `[cur, cur+n1)`. | — | A bitmap check of the read-back survivor buffer (milliseconds per 2^22 chunk) would turn residual risk (a) into a proof for raw. |
| F7 | MINOR | line 207 | The scheme check is an `assert`. Under `python -O`, `SCHEME bip39_raw` + NOCHECKSUM loads, and the CPU tool would run it as checksum-filtered bip39 (Space refuses it, so the GPU path is safe). `sp_test_raw.py:214` passes without the assert. | `opt_scheme.py`; mutant P21 survives. | Use `sys.exit`, and test with a body that contains only wordlist words. |
| F8 | MINOR (test gaps) | `sp_space.py:24`, `setperm_solve.py:129`, `gpu_setperm.py:257`, `sp_test_raw.py:644` | No test checks that a 240-byte text is refused (it is; I checked on the GPU). Space's whitespace refusal is only tested with an ASCII space. No test covers an overflow at the minimum chunk. The K2 test's docstring claims 128/129 coverage, but its texts are 71–124 and 129–191 bytes, never exactly 128. | Mutants S6, P7, G16 and KM3 below. | Add those cases. |
| F9 | Operational | `gpu_setperm.py:704` | The engine hash changes (4e905055… → e9afbdfd…), so every checkpoint written before the merge will refuse to resume. That is safe but means re-runs. PASSFILE de-duplication slows quadratically (40k lines: 4.3 s; the L5 lead plans over 10^5 lines, which the 4,096 cap refuses anyway). | — | Plan the queue's re-runs. Use a set for the de-duplication. |

## Mutation results

**CPU suite:** run on copies, in an env without pyopencl, with `SP_NO_GPU=1`. Baseline: 378 tests, 115 skipped, OK.

| Mutant | Result |
|---|---|
| **binomial_problem / call site** | |
| G1 `rate>=1`→`>1` | killed (ExactSurvivorCheck; 2 RawRunNoGpu tests) |
| G2 accepts lost survivors | killed (same 3) |
| G3 accepts invented survivors | killed (2) |
| G4 exact check removed | killed (3) |
| G5 old 10,000 floor | killed (3) |
| G6 call site judges raw at rate 0.5 | killed (2) |
| G7 call site passes tested:=valid | killed (6) |
| G8 banner/VOID text says "binomial" for raw | killed (3) |
| **K1 sizer** | |
| G9 cap removed | killed (RawK1Sizer ×2) |
| G10 `lo=64` | killed (RawK1Sizer ×1) |
| G11 MODE_NONE branch off | killed (RawK1Sizer ×2) |
| G12 start not capped | killed (RawK1Sizer ×2) |
| **Overflow-redo path** | |
| G13 redo without shrinking | TIMEOUT: hangs in the older `ControlsWithoutGpu` slow-launch test |
| G14 overflow never redone | killed only by that same older test |
| G15 redo off by one | killed only by that same older test |
| G16 `n1<=lo`→`<` | **SURVIVED**. Turns an EngineError into an endless loop. Cannot happen for raw, where lo and hi are at most the buffer size. |
| **sp_space** | |
| S1 raw runs in BIP39 mode | killed (6) |
| S2 estimate rate 0.5 | killed (4) |
| S3 candidate_ok applies the checksum | killed (9) |
| S4 / S5 Space's token / scheme refusals removed | killed |
| S6 `SP_MAXMSG=240` | **SURVIVED** |
| **setperm_solve / PASSFILE / sp_ref** | |
| P1 NOCHECKSUM ignored | killed (7) |
| P2 NOCHECKSUM+electrum not refused | killed |
| P3 tokens not NFKD-normalised | killed (9) |
| P5 tokens lowercased | killed (7) |
| P6 `\|` check removed | killed |
| P7 only an ASCII space counts as whitespace | **SURVIVED**. Cannot happen through load_config (NFKD only ever introduces U+0020); possible for hand-built configs. |
| P8 NFKD test removed | killed |
| P9 Electrum normalisation applied to the passphrase | killed (7) |
| P10 salt `electrum` | killed (4) |
| P11 CPU solver filters raw | killed (5) |
| P12–P17, P22 PASSFILE: relative to cwd, all CRs stripped, BOM kept, `#` comments, no de-dup, all-space lines dropped, splitlines | all killed |
| P18–P20 FREEWORDS message, whitespace test removed, POOL unchecked | killed |
| P21 scheme assert removed | **SURVIVED** |
| R1 ref_survivors filters raw | killed (5) |

**GPU kernel mutants:** kernel-level tests only (`sp_test_raw.RawKernels`, `sp_test_k1`, `sp_test_k2`); no `run_search`. Baseline: 38 OK.

| Mutant | Result |
|---|---|
| KM1 mode 0 gets the Electrum filter | killed (RawKernels vector test + 5 K1 tests) |
| KM2 POOL token ids truncated to 11 bits | killed |
| KM3 a 128-byte key gets pre-hashed | killed **only** by the older `sp_test_k2.test_hmac_key_length_boundaries_exact` |
| KM4 16-byte tokens packed as 15 | killed |
| KM5 FIX token ids truncated to 11 bits | killed |

The five most important tests each fail when the thing they guard is broken:
- `ExactSurvivorCheck` (G1–G5)
- `RawRunNoGpu.survivors_other_than_tested` (G1–G8)
- `RawK1Sizer.caps` (G9–G12)
- `KnownAnswers.vectors_come_out_of_a_bip39raw_config` (S1, S3, P1, P5, P9, P10)
- `RawKernels.vector_file_through_k1_k2_and_k3` (KM1, KM5)

The only nearly empty new test is `test_an_unknown_scheme_is_still_refused` (P21). No existing test was edited or weakened.

## Checked and found sound
- **Electrum 3.3.8 source** (`bip39_to_seed`, `from_bip39_seed`, `get_seed`, the BIP39 toggle, `line_dialog`): the seed text is joined on whitespace then NFKD; the passphrase is `line.text()` with NFKD only. This matches the change's model.
- **Item 1, per-token NFKD vs NFKD of the joined text:** I fed all 1.11M code points through the real parser in 5 positions (5.56M tokens), plus 447k pairs of edge-case tokens (combining marks at either end, Hangul jamo, decomposing characters). Zero mismatches against Electrum's function. A counter-example would need canonical reordering to move a mark across the joining space, which cannot happen because the space is a starter that never moves; none exists among accepted tokens.
- **Item 2, GPU parity (unmodified engine):**
  - K1 mode 0 counts and survivor sets are exact, with and without DISTINCT (8190 of 8192).
  - Token ids up to 6,143 work (a 4,096-token pool); no 11-bit truncation anywhere.
  - K2 seeds equal Electrum's derivation of the text as typed (NFC, compatibility forms, Hangul, a token starting with a combining mark) at 1, 16, 17, 111, 112, 127, 128, 129, 130, 200, 238 and 239 bytes, with a 99-byte passphrase.
  - The K3 dump matches `sp_ref`, and a 240-byte text is refused.
- **Item 6, existing schemes:** 752 of 756 configs (all 736 master templates, configs A–K, plus CRLF, lone CR, tab, BOM and lower-case-key edge cases) come out identical in old and new code. Identical means the parsed config, every Space field, descriptor, token tables, salts, path table, run signature, decode samples and refusals. The 4 differences are the new raw configs. The A–K hash matches the old `sp_testutil`. The kernels and wrappers are unchanged.
- **Item 5, resume signature:**
  - Different signature: raw vs bip39, a token's case changed, different PASSFILE contents, DISTINCT added.
  - Same signature, correctly, because the search is the same: the NOCHECKSUM alias, an NFC vs NFD token, and a PASSFILE vs the equivalent PASS line.
- **Item 3 exact check:** exact at any block size, no division by zero, and filtered schemes give the same answers as before. An honest K1 in mode 0 cannot overflow (chunk ≤ hi ≤ buffer and valid ≤ chunk), so the exact check cannot raise a false VOID.
- **PASSFILE:** caps refuse loudly (4096 → SpaceError/EngineError, 99 bytes → SpaceError). Missing file, directory or non-UTF-8 → refused. BOM, CRLF, spaces and relative paths all behave as documented. Nothing is silently dropped except the lone-CR merge (F4).

## Not checked
- No `run_search` with `--signoff`; the GPU tests `RawRunGpu` were not re-run; the 30 known-answer addresses were not re-derived (you did that).
- Unicode version: Electrum binaries of 2019 used Python 3.6 (Unicode 9), the harness uses Unicode 15. Characters added since then that decompose under NFKD (e.g. U+1FBF0–9) would differ. This is theoretical.
- Qt input handling beyond what the source shows.
- K3, plants and window audit internals under kernel mutants.
- The CPU tool's checkpoint resume for raw, beyond the signature.
- Master's newleads configs and passlists, beyond scanning them for `#`, `@` and duplicate FIX slots.

Evidence scripts are in `/tmp/claude-1000/-home-the operator-projects-puzzlebox/3c7348a2-7a63-4551-8306-e9998bdb7fd1/scratchpad/` (`hash_e2e.py`, `unicode_check.py`, `gpu_raw_check.py`, `sig_check.py`, `opt_scheme.py`). The mutation drivers, logs and results are in `.claude/worktrees/agent-a513f0daa8a4d736a/scratch/mut/` (`results.jsonl`, `kresults.jsonl`). The old-vs-new comparison is in `.claude/worktrees/agent-a513f0daa8a4d736a/scratch/diff/`.
