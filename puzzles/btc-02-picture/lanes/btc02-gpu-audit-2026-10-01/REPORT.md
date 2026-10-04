# btc-02 GPU engine (gpu_setperm): correctness audit, 2026-10-01

Independent audit by a subagent (Opus) of the engine at commit e10cda8. The subagent's harness refused its own
write of this file, so the main session saved its returned text here verbatim, apart from this header.
Scripts and logs: this folder (`scripts/`, `*.log`, and a byte-identical copy of the harness under `puzzles/`).
Spot-checked by the main session: the `F1` redefinition (`kernels/sha2.cl:1` vs `kernels/ripemd.cl:50`) and
`normalize_pass` (`setperm_solve.py:60-67`).

**Verdict:** no defect found that could have caused a missed answer in past runs. The engine enumerates exactly
each config's candidates; passphrase/path/key-form/target handling matches the wallets, including Electrum's
extension lowercasing; a prize hit reaches `FOUND_gpu.txt` and exit 2.

Constraints held: nothing under `puzzles/` or `lib/` changed (real engine files match by sha256); no git; no unit
or process touched; no search against the prize; fault injection only in a pyopencl-free env with self-generated
dummy prizes; GPU use under 4 minutes (the unmodified suite once, 158 s, plus a few compile-only builds).

Commands: `CPU = uv run --offline --no-project --python 3.12 --with coincurve --with numpy python`,
`ORACLE = CPU --with bip-utils --with ecdsa`.

## The compiler warning in every run log ("1 warning generated.") — cosmetic
`'F1' macro redefined`: `sha2.cl:1` redefines ripemd's `F1` (`ripemd.cl:50`), in the K3 program only (K1/K2 build
clean even with `-Werror`). The NVIDIA build log is empty; `-w` removes the line, and so does an `#undef F1` before
sha2's define (fresh and cached build). It appears alone because the driver cache hits on the K3 binary and only
preprocessing runs; all 343 log occurrences are this one line. A fresh compile prints 59 warnings; the other 58 are
pointer/sign/const mismatches in the base crypto (two in `sp_derive.cl:113-114`: `hash160(c33, 33, h1)` passes a
`uchar` array where `char *` is expected, sign only). Harmless: ripemd's uses of `F1` sit before the redefinition,
sha2 uses its own and `#undef`s it at `sha2.cl:365`, nothing later uses a ripemd macro name, and K3's hash160 is
checked against the CPU in every run (self-test, slot reach, plants, window audit). Reproduce:
`scripts/build_probe.py`, `build_log.py`, `build_werror.py`, `build_fulllog.py`; output `k3_full_build_log.txt`.

## Q1 Coverage — OK
- An independent parser agrees with `load_config` / `Space` on all 461 GPU configs (465 files; the 4 `t21_*`
  files hold all 84 SLOT lines, are template_solve configs, and the engine refuses them with exit 1): FIX,
  REQUIRE, POOL (with dedup), PASS, PATH, and the count P(m,r) x prod(alternatives) x npool^k. The block
  partition is exact for every config (`gpu_setperm.py:1042`; coverage check `:1151-1155`).
- `Space.decode` equals an independent decode (permutation unranking validated exhaustively against itertools):
  143,747 sampled indices including unit and boundary edges over all 461 configs, plus full literal itertools
  enumeration of 16 small configs and 22 representative shapes up to 3e6 (17.95M indices): 0 mismatches. Shapes:
  FIX alternatives, REQUIRE r = m and r < m, POOL @WORDLIST, DISTINCT, FREEWORDS, 12/13/18/21 words.
- Caps are refusals, never truncation: 513 alternative tokens, POOL > 4096, passphrase > 99 bytes, 9 paths, token
  > 16 bytes, mnemonic > 239 bytes, space >= 2^63 (`sp_space.py:92`, `:106-121`); 512 tokens is accepted
  (`refuse/*.conf`, dry runs).
- Tiles: `ro_s3_slot{9,11}open_p_t1..5` cover all 2048 words with no overlap; the `bush_*`/`zg_*` grids are
  complete products of partitioned lists.
- Resume: only fully verified blocks get a `done` line (`:1136`), torn lines are ignored; a stop after 2 blocks
  then a resume found a dummy prize placed in the last, partial block.
- Reproduce: `CPU scripts/q1_inventory.py`, `q1_decode.py 300000`, `q1_decode_full.py`, `q1_tiles.py`.

## Q2 Filters — OK
- BIP39 checksum equals bip_utils at 12/15/18/21/24 words (100k random phrases, ~2,400 valid, 0 mismatches).
  BIP39-13 is not valid BIP39 and is refused (`sp_space.py:69-70`).
- Electrum: only the `01` standard prefix is accepted (`sp_enum.cl:40`, `setperm_solve.py:57`). On 300k random
  12/13-word phrases: all 1,164 standard accepted; all 53 segwit, 84 2FA, 76 2FA-segwit rejected (0 mismatches).
  Correct, since those seed types cannot yield a `1...` P2PKH address. Documented in the `setperm_solve` docstring
  and the spec, not in the README.
- Electrum restores an all-old-wordlist 12/24-word phrase as an "old" seed before the prefix check; the engine
  does not model it, but none of the 140 Electrum-12 configs contains such a candidate.
- Reproduce: `ORACLE scripts/q234_parity.py`, `q2_oldseed.py`.

## Q3 Seed derivation — OK
- Electrum's `normalize_text` re-implemented (including the CJK step) and anchored to four of Electrum's own seed
  test vectors ("wild father..." with and without "Did you ever hear the tragedy...", the Spanish phrase with
  accents with and without "araña difícil..."; recalled from memory, matched to 128 bits). The passphrase vector
  matches only if the extension is lowercased.
- The engine's `normalize_pass` (`setperm_solve.py:60-67`) applies NFKD, lowercase, accent strip and whitespace
  collapse, so for Electrum MOON/Moon/moon are one extension, as in the wallet. BIP39 passphrases: NFKD only, case
  kept. PBKDF2 2048 iterations (`sp_pbkdf2.cl:46`), salts `mnemonic` / `electrum`.
- All 3,226 raw PASS lines (spaces, punctuation, Cyrillic) x every PATH x both key forms on real candidates of
  every config: oracle (raw string, wallet semantics) hash160 == engine reference, 33,804 derivations, 0
  mismatches. Note: bip_utils' own Electrum v2 seed generator does NOT lowercase the extension, so it is not a
  valid oracle for this.
- PASS parsing: `#` starts a comment, trailing whitespace stripped, a leading extra space kept; no config affected.

## Q4 BIP32 and addresses — OK
Hardened markers, Electrum `m/0/i` and `m/1/i` from the root, compressed and uncompressed keys all equal
bip_utils (python-ecdsa cross-checks the pubkeys; same 33,804 derivations). Target decodes to
`ccbd031e54cde2a3189fd59bc49f731367a1779e` (engine == bip_utils). All 2,087 PATH lines have the form m/N['].

## Q5 Hit reporting — OK
K3 records the hit (`sp_derive.cl:97-108`) -> the wrapper reads it (`sp_k3_derive.py:49-51`) -> kept even if the
block later fails a check (`gpu_setperm.py:271`) -> `_logical_tgt` maps both prize slots to the prize (`:463`) ->
`judge_prize` runs before any control (`:1077`): CPU confirm, print, append+fsync `FOUND_gpu.txt`, exit 2. Both
queue scripts stop on exit 2 or on that file. Exercised with dummy prizes: prize in the last partial block after a
resume -> FOUND; prize in a block whose plant hits are partly lost -> FOUND; hit-buffer overflow in the prize's
batch -> VOID, never NO MATCH (`scripts/q5_hits.py`). A K1 survivor-buffer overflow redoes the chunk smaller
(`:248-253`); nothing is lost.

## Q6 What "NO MATCH (verified)" certifies — controls OK for GPU faults; blind spots listed
- Caught every run: K3 dropping one path's uncompressed keys -> VOID 5/5 (combo self-test); K2 corrupting one
  passphrase on 1 in 5 survivors -> VOID 5/5.
- Shared-mode bugs (the same wrong CPU code used for plants and real candidates) are invisible to every control:
  an injected "Electrum extension not lowercased" bug gave NO MATCH (verified) 5/5. Only independent checks rule
  them out; the ones in Q1-Q4 are clean.
- K1 losing survivors in a small sub-range is mostly not seen at run time: on the real single-block shape
  `pair01_know_pyramid_e12` (1.47M), dropping one REQUIRE unit (4 survivors) gave NO MATCH 11 of 12
  (`q6_unit_skip_real.log`).
- Exact CPU recount of tested AND valid over the whole space with an independent parser and filter: 250 of 333
  completed runs all equal (4.69e9 candidates; `q7_recount*.log`); one random block of 25 of the remaining large
  runs all equal (`q7_block_recount.log`). Where counts match exactly, a K1 survivor loss did not happen.
- Campaign statistics: 746,273 plants found, 0 lost, 0 VOID records, 11,751 blocks: any target-independent
  hit/survivor loss has a rate below ~4e-6 (95%).
- Real configs never reach the rare kernel paths: Electrum mnemonics <= 104 bytes, none over 128 bytes (the
  21-word config's maximum is 126), so plants run the same code as every real candidate.
- The unmodified GPU suite passes on today's driver (336 tests, 0 skipped); the CPU suite passes (336, 108 GPU
  tests skipped).

## Q7 Accounting — OK
Dry-run raw == independent count for all 461 configs; the recounts above confirm tested and valid; ledger-level
totals were checked by the main session. A run can exit 0 with blocks skipped only via a hand-edited checkpoint
(`:1151-1155`).

## Ranked findings
### (a) Defects that could have caused a missed answer in past runs
None found.

### (b) Gaps in what the controls certify
1. Shared-mode semantics (decode, filter, passphrase normalisation, path parsing, target decode) are not checked
   at run time; verified clean here. Recommend adding Electrum's with-passphrase and Spanish vectors to the
   self-test (today it checks the Electrum extension only by a string comparison, "BREATHE" -> "breathe", not
   against a published seed).
2. A localized K1 loss passes the controls ~9 times in 10 per run. Excluded by the exact recount for 250 runs and
   sampled in 25; 58 large runs rest on statistics only.
3. E4 (sig `23c1929f...`) is the only pre-fix run: 1 plant per block, the prize at one slot. Its kernels and CPU
   reference are identical to every later run's (last changed at 6b09db2, 04:56; E4 started 04:58), but its own
   block-level evidence is weaker and it is too large to recount (~84.5B).
4. Checkpoint `done` lines are trusted (documented).
5. Scope, not defects: the Electrum old-seed rule is not modelled (no config affected); the CJK whitespace step is
   missing from `normalize_pass` (no CJK passphrases in any config); an Electrum-compatible library that keeps the
   extension's case would never be tested for MOON/Moon (the engine follows Electrum and folds them).

### (c) Cosmetic
1. The `F1` macro compiler warning (above).
2. `parse_path` silently reads `"0/0"` as `m/0` (`template_solve.py:65`); no config affected.
3. `load_config`: a duplicate FIX slot keeps only the last line; `#` truncates a PASS value; no config affected.
4. `ld42_countpins_{e12,e13,p}` list `one` twice at FIX 7: 51,450 raw instead of 44,100 distinct (double count,
   no loss).
5. Stale docstrings: "[prize, this block's plant]" in `Engine.set_targets` / `DeriveKernel.set_targets`; Ckpt's
   "its own plant".
6. The README does not state that segwit/2FA Electrum seeds are excluded.
7. The legacy `electrum_test.py` accepts the segwit `100` prefix and derives P2PKH for it, without normalising the
   extension; it is not part of the engine.
