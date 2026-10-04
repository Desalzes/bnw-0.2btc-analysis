# gpu_solve — BIP39 GPU permutation sweep

READ-ONLY: on a hit, prints + writes `FOUND.txt` and stops.
**Never constructs, signs, or broadcasts a transaction.**

## Dependencies

```
pip install pyopencl bip-utils numpy
```

NVIDIA GPU required. The OpenCL kernels require `-cl-std=CL1.2` (already
hard-coded in `build_program()`). On Blackwell (RTX 5000 series) this flag
is necessary for compilation.

## Selftest

```
python gpu_solve.py --selftest
# expected: SELFTEST PASS
```

The selftest generates two random 12-word BIP39 mnemonics (one with empty
passphrase, one with `rainyday1865`), derives the target address via CPU
(bip-utils), and confirms the GPU kernel finds the same mnemonic via a
1-batch permutation search.

## Usage

```
# Known 12-word set — sweep all 479M orderings, optional passphrase list:
python gpu_solve.py --words words.txt [--passphrases passphrases.txt] --target 1KfZ...

# Pool mode — try every choose-12 combination from the pool:
python gpu_solve.py --pool pool.txt --target 1KfZ...
```

`words.txt` / `pool.txt` / `passphrases.txt` are one token per line; lines
starting with `#` are comments.

## File layout

- `gpu_solve.py` — main script; loads `english.txt` and `kernels/*.cl` from
  its own directory via `HERE = os.path.dirname(os.path.abspath(__file__))`.
- `kernels/` — OpenCL crypto: SHA-256, RIPEMD-160, secp256k1, P2PKH address
  derivation, BIP39 mnemonic constants, permutation sweep kernel.
- `english.txt` — BIP39 2048-word wordlist.
- `passphrases.txt`, `pool.txt` — example input fixtures.

## Safety note

This tool is COMPARE-ONLY. It derives addresses and compares to a target.
It does not know your private key, does not write a wallet, and does not
have network access. Do not add broadcast/sweep code.

---

# gpu_setperm — GPU set+permutation search (Electrum + BIP39 + BIP39 raw text)

Same `.conf` files and enumeration order as `setperm_solve.py`, on the GPU.
READ-ONLY: on a hit it writes `FOUND_gpu.txt`, prints the STOP instruction and exits 2.

```
PY="uv run --offline --no-project --python 3.12 --with coincurve --with numpy --with pyopencl python"
$PY gpu_setperm_test.py                            # whole verification suite (GPU tests skip with SP_NO_GPU=1)
$PY gpu_setperm.py templates/e4_pins_only.conf --dry-run [--assume-rate R]
$PY gpu_setperm.py CONFIG --bench                  # dummy target + planted seed, prints the real rate
$PY gpu_setperm.py CONFIG --checkpoint scratch/x.ck --signoff "who approved what, and when"   # a real run
```

**Sign-off.** Every real run needs the operator's explicit yes (ADR-0001 rail 4), whatever its size, and the tool
asks for it in words: `--signoff TEXT` (`run_search(signoff=...)`). Without it a real run is REFUSED (exit 3)
before anything touches the GPU or the disk; the text is logged (`operator sign-off: ...`) and written to every
ledger record of the run. `--dry-run` and `--bench` need none (they never search for the prize). The step that
searches (`_search`) checks the sign-off itself as well, so a code path that loses `run_search`'s own checks
still searches nothing. The 1-hour cost guard alone used to let a run of 900 million derivations start from a
bare command line.

The space is scanned in checkpoint blocks, and **every block carries its own planted seeds: 64 plants**
(`PLANTS_PER_BLOCK`; fewer only where a block holds fewer seeds, none where the CPU proves it holds none).
Each is a filter-passing candidate inside the block with its own random (passphrase, path, key form), chosen
deterministically from the `PLANT` seed (checkpoint line 2, also printed and written to the ledger as a
`START` record at the start of every run, checkpoint or not) and the block number, and searched for
together with the prize. Their positions are uniformly random filter-passing candidates of the block (a small
block: the first passing ones of a random order), so they are spread over the block - neither confined to its
first 4096 candidates nor its first survivors (pinned by tests) - and all have different indices and different
hash160s (the twin index of a seed already planted with the same combination is not planted again). The K3
target list of a block has plants + 2 slots (66 for a full block), and **the prize sits at two slots** of it
(`PRIZE_COPIES`): slot `(block + plant seed) % (plants + 2)` and the one half a list further; the block's plants,
in their order, hold all the others. One slot failing cannot lose a prize that the other slot reports; the prize
takes every slot in turn from block to block, so a fault confined to one target slot cannot hide behind plants
that never sit there; and it starts where the plant seed says, so in a run of a single block it is not always
slot 0. Sixty-six targets cost nothing measurable (265k derivations/s with 2 targets, 261k/s with 65, 259k/s
with 129, on e4). A block is recorded `done` only after
all of its controls held:
every one of its plants was reported at its exact (index, passphrase, path, form) (a twin report of the same
seed inside the block is accepted only if the CPU confirms it; a missing plant is named in the VOID); K1 scanned exactly the block (thread count
AND an exact 64-bit index sum per launch, and again over the whole block against `run_search`'s own
`[lo, hi)`); every survivor K1 counted was handed to K2/K3 exactly once, and both wrappers RECORDED
receiving exactly the slice `scan_block` meant to hand them (a consumer-side `off+1` / `nsurv-1` is
refused); the block's survivor count is within 8 sigma of `tested x` the scheme's pass rate
(binomial check, for blocks of >= 10,000 tested: this sees the small systematic survivor loss a
statistical plant cannot; it fails safe, a false alarm is a VOID, never a false NO MATCH; for
`SCHEME bip39raw`, which has no filter, the count must equal `tested` exactly, in every block: see
below); and, where
the config has no DISTINCT filter, `tested == scanned` (nothing is filtered before `tested` is counted;
the binomial check sees only the RATIO valid/tested, so a K1 that skipped candidates after counting them,
`tested` and `valid` shrinking together, passed it). A block the CPU proves has no filter-passing
candidate at all has no plant and must report 0 valid.

**Window audit (per block).** After the controls above, `Engine.k1_spot_check(lo, hi, seed)` audits one
pseudo-random window of the block (`random.Random(f"{seed}:k1-spot:{lo}")`, so it is reproducible from
the plant seed and the block number; width `min(32,768, block size)`, i.e. `1 << 15`). K1 is run again as a
launch of its own over the window and the survivor SET it reports (a sorted list: an extra, a missing or a
duplicated index each differ) must equal the CPU's (`space.candidate_ok` over the window; on e4 the CPU
side costs about 0.1 s). Then a random contiguous slice of up to 16 of those survivors (fewer if their K2/K3
threads would pass 2^9 = 512, which bounds the audit's CPU time at about 1 s per block - the CPU re-derives every
thread at ~2 ms - so a config with hundreds of passphrases audits fewer survivors; the audit's CPU cost grows with
the passphrase count, and so does the start-of-run slot sweep) goes through the REAL K2 launch and the K3 dump path, and every (passphrase, path,
key form) hash160 must equal `sp_ref.hash160_forms`. Any difference is a VOID naming what differed (the
survivors missing/unexpected, or the survivor, passphrase, path and key form whose hash160 is wrong). It
uses launches of its own and touches none of the block's counters, K3 targets, launch sizers or checksums;
it runs after a confirmed prize hit has been judged (a FOUND is never lost to it) and before the block's
`done` line. A window whose survivors would overflow the K1 buffer is skipped with a log line (it cannot
happen at this width and the default buffer) and the banner counts the skipped audits. The NO MATCH banner
and the ledger say how many session blocks were audited.

**Combo self-test.** Before the first block of every run (also on a resume, unless every block is
already in the checkpoint), two CPU-verified survivors from different halves of the space (chosen from
the plant seed) are scanned alone, each searched for ALL of its (passphrase, path, key form)
hash160s computed on the CPU (up to 256 targets per round, split into rounds if there are more or
the hit buffer could not hold their hits): EXACTLY the expected hits at the exact (index, passphrase,
path, form, target) are required, otherwise VOID naming the missing combinations. Every dimension of
K2/K3 is thus a deterministic known-answer test at the start of every run, however small the template
(a 1-block template with a single plant would otherwise test one combination). `DeriveKernel.set_targets`
also reads the uploaded target list back and refuses a difference.

**Slot sweep.** The main rounds put every combination at one K3 target slot (its position in the round), but
a real block searches `[prize, plant]` or `[plant, prize]` (slots 0 and 1) or `[prize]` alone. So, for each
of the two survivors, one combination per distinct value of every dimension (the first combination that
shows a passphrase, a path or a key form not yet covered) is searched again with the targets `[h, h]` (the
value at slots 0 and 1) and `[h]` (alone), exact hits required at every slot: a kernel or wrapper fault
keyed on (K3 slot or target count, one value of one dimension) cannot hide. Its cost is 2 rounds per
covered value per survivor (20 rounds for a config with 3 passphrases and 2 paths), about 16 ms per round
measured: it grows with the number of passphrases (about 7 s at 100 passphrases, roughly 4 minutes at the
4096 limit). A combination whose `[h, h]` hits would not fit the hit buffer is refused, not overflowed.

**Slot reach.** A full block searches 66 targets (64 plants and the prize at two slots), but the main rounds reach
only as many K3 slots as the config has combinations (8 on e4) and the sweep slots 0 and 1. So each of the two
survivors is searched once more with 66 targets, its own combinations in turn (slot t holds combination t modulo their number), and exactly
one report per owner is required at every slot: every slot a block will use holds a known answer before block 0.
Where the hit buffer cannot hold all of those reports the slots are split over several rounds of 66 targets, the
other slots holding random fillers that nothing may hit.

NO MATCH (exit 0) needs the self-test (with its slot sweep and its slot reach), every block `done` (each session block also
past its window audit) and the blocks covering the space exactly; any failed control is VOID (exit 4), as
is any exception inside a block. A FOUND rests on the CPU
alone: the GPU's hit is re-derived with `sp_ref.confirm` (a malformed report is simply unconfirmed),
an unconfirmed report is VOID, never a claim, and all of that block's prize reports are logged with
their block, K1 chunk and K2/K3 batch coordinates (indices only, never a mnemonic) for a CPU
re-check. That holds when an exception interrupts a block too: the hits gathered before it are still
put through the same CPU confirmation (a confirmed prize hit is FOUND whatever failed; otherwise the
VOID logs the partial hits). `FOUND_gpu.txt` is appended to and fsynced, never overwritten; an
unwritable ledger never turns a FOUND into an error.

The NO MATCH banner says what was checked: blocks verified in this session versus taken from the
checkpoint (on trust), the self-test result (survivors x combinations = known answers, plus the slot-sweep
answers), the per-block controls, and how many session blocks were window-audited (and how many skipped).

### SCHEME bip39raw (alias NOCHECKSUM) and PASSFILE

`SCHEME bip39raw` searches what Electrum's "BIP39 seed" import derives from ANY text. Read in the Electrum 3.3.8
source (`keystore.bip39_to_seed`): `seed = PBKDF2-HMAC-SHA512(NFKD(' '.join(text.split())), 'mnemonic' +
NFKD(passphrase), 2048)` - no word-count, wordlist or checksum test, and no lowercasing. So:

* **No filter.** Every candidate that passes `DISTINCT` is derived (K1 runs in mode 0: `Space.mode == MODE_NONE`,
  `estimate()["valid_rate"] == 1`). A raw space costs 16x a BIP39-12 space of the same size and 256x an Electrum
  one; `--dry-run` prices it so.
* **Any `WORDS` from 1 to 24** (plain `bip39` still needs 12/15/18/21/24).
* **Tokens are free text, case kept** (FIX alternatives, REQUIRE, POOL): each is NFKD-normalised and nothing else,
  so `MOON`, `moon`, `breathe` and `чёрный` are all legal and `MOON` is not `moon`. A token that is empty or holds
  whitespace after NFKD is refused (Electrum would split the text there), and so is a FIX alternative that NFKD
  turns into one holding `|` (a fullwidth `｜`: it would split the alternative). NFKD per token equals NFKD of the
  joined text, because the joining space is a starter and canonical reordering never crosses one. `POOL @WORDLIST`
  still means the 2048 words; tokens stay <= 16 UTF-8 bytes after NFKD and the joined text <= 239 bytes on the GPU
  (`Space` also refuses a hand-built config whose raw tokens are not NFKD or hold whitespace). `FREEWORDS` is
  refused: it is Electrum's, bip39raw needs none.
* **Salt and passphrase** as BIP39: `"mnemonic"` + the NFKD passphrase, case kept.
* **`SCHEME bip39` + `NOCHECKSUM`**, in either order, IS `SCHEME bip39raw`: the parsed configs are equal, and so is
  the run signature. `NOCHECKSUM` with `SCHEME electrum` is refused.

**What replaces the binomial check.** At a pass rate of 1 the binomial sigma is 0, so `binomial_problem` judges a
raw block exactly instead: survivors == tested, in every block whatever its size (the 10,000-tested floor does not
apply), or the run is VOID (`implausible survivor count (exact check)`). It sees a K1 that loses or invents even one
survivor among the candidates it counted as tested, which a block under 10,000 tested hid from the binomial check.
With `DISTINCT` it does not see a K1 that drops candidates before counting them as tested (`tested` and `valid`
shrinking together): as for the other schemes, that rests on the window audit, the plants and the parity tests. The NO MATCH banner then says
`exact survivor-count check (no filter: survivors == tested in every block)` where the other schemes say
`binomial survivor-count check`. Every other control is unchanged (`tested == scanned` without DISTINCT, the
plants, the window audit, the self-test).

**K1 launch size.** With no filter every scanned candidate survives, so K1's launches are capped at the survivor
buffer, `min(K1_MAX_CHUNK, surv_cap)` (2^22 by default) instead of 2^25: a larger launch would always overflow and
be redone smaller. The window audit's 32,768-candidate window fits the default buffer; a run given a smaller
`surv_cap` skips its audits, and the banner counts them.

**Known answers.** `artifacts/external-reports-2026-10-01/finders/L5-files/known_answer_vectors.txt` (V0-V5),
re-derived by `kav_independent.py` with code that shares nothing with the harness. `sp_test_raw` holds the engine
to it on the CPU and through the real K1/K2/K3, and `setperm_solve.py --selftest` checks its compressed addresses.

**`PASSFILE <file>`** (any scheme, repeatable): one passphrase per line, added to the PASS list exactly as `PASS`
lines are (deduplicated; the empty passphrase is always tried). Lines are taken VERBATIM except the line terminator
(`\n` or `\r\n`): no `#` comments, and leading, trailing and inner spaces are kept (a line of spaces is a
passphrase); empty lines are skipped; a UTF-8 BOM at the start of the file is dropped. The path is relative to the
config file's directory unless absolute.

**A `#` on a FIX / REQUIRE / POOL / PASS / PASSFILE line is refused** (every scheme). A `#` starts a comment, so on
those lines it used to cut a token, a passphrase or a file name off silently: the run then searched a smaller space
than the config states and no control could see it (every control works on the parsed config). Comments go on a
line of their own; a passphrase holding `#` goes in a PASSFILE; a token cannot hold one. A missing or non-UTF-8 file is refused. Each passphrase is normalised by the scheme like a `PASS` line
(NFKD; Electrum also lowercases), and the limits stay: <= 4096 passphrases, <= 99 bytes each.

### Trust model: what NO MATCH rests on, and the residual risks

Checked at run time: the self-test, its slot sweep and its slot reach; per block every plant at its exact place, the exact
64-bit K1 index sum (per launch, per block, against `run_search`'s own range), K2/K3 survivor slices as
received, the survivor accounting (`processed == valid`), the binomial survivor count (exact for bip39raw), `tested == scanned`
(no DISTINCT), the window audit, and exact coverage. Not proven at run time (the residual risks the
reviewers established):

* (a) The exact K1 survivor SET (which candidates pass DISTINCT + checksum) is **sampled by a per-block
  window audit** (survivor set on one pseudo-random window of at most 32,768 candidates, plus K2/K3
  hash160 parity on up to 16 of its survivors), **not proven**: a fault that loses or adds survivors only
  outside the audited windows, or in a fraction of blocks too small for one window per block to see, is
  bounded by the `sp_test_k1` parity tests against the CPU tool, the plants (statistical) and the binomial
  check. The audit reads the K3 *dump* kernel, which shares `sp_walk` with the hit kernel but not its
  target-compare loop; that loop is covered by the self-test, the slot sweep and the plants.
* (b) Plants are statistical controls for partial loss, of survivors or of hits: a fault that loses a fraction
  q of them is missed by one plant with probability 1 - q, by a block's 64 plants with (1 - q)^64, and by a run
  of B full blocks with (1 - q)^(64 B). The independent review of 2026-09-29 injected a K3 fault that lost 1 hit
  in 64 (by the thread's place in a launch, at one target slot): with one plant per block, and the prize at that
  slot in every other block, it passed all 8 blocks of its run as NO MATCH. Two things answer it. A fault
  confined to ONE slot cannot lose the prize any more, whatever threads it takes: the prize sits at two slots
  (it is still rarely SEEN - one plant per block sits at the broken slot - but it cannot make a NO MATCH false).
  And a loss that is not confined to a slot is seen by the plants: 1 hit in 64 in a block with probability 0.63
  and in an e4-sized run (83 blocks) with certainty for practical purposes; 1 in 1,000 in such a run with
  probability 0.995, in a run of a single block only with 0.06. What is left is statistical: a fault that loses
  well under 1 hit in (64 x blocks) at both of the prize's slots. The window audit, the binomial check and the parity tests bound
  a small systematic loss of survivors besides.
* (c) `done` lines of a checkpoint are trusted: a forged or hand-edited checkpoint (same `SIG`) makes
  the run skip blocks and still print NO MATCH. The CPU tool trusts its own checkpoint the same way; the
  banner says how many blocks came from the checkpoint rather than from this session's controls.
* (b2) The main self-test rounds scan 1-wide windows, so K2/K3 always see survivor slot 0 and slice offset 0
  there; the window audit's K2 launch covers up to 16 survivors and random offsets for the seeds and
  hash160s, but a fault that shows only for a later slot or offset inside a batch of the hit kernel is
  seen only by the plants (statistical, 64 of them in every block: see (b)), the recorded-slice checks
  (which see what the consumer was handed) and the `sp_test_k2` / `sp_test_k3` kernel tests. Detection floor of the binomial check: about
  0.4% survivor loss on e4-sized blocks, about 2.6% (BIP39) to 10% (Electrum) on blocks of 1e6-1e7 candidates.
* (b3) A K3 hit-buffer overflow (more than `hit_cap` hits in one batch) discards that batch's stored
  records: a genuine prize hit in that batch ends as VOID, never as NO MATCH.
* (b4) The slot sweep covers every value of every dimension at slots 0 and 1 and alone, and the slot reach a
  known answer at every slot, not every joint (slot, exact combination): a K3 fault keyed on a slot together
  with one exact (passphrase, path, key form) is seen only by the plants. Within one block the prize's own two
  slots hold no plant: a fault confined to threads the self-test does not run (it scans 1-wide windows) and to
  BOTH of those slots, and to no other, is seen by the plants that take those slots in the other blocks, and in
  a run of a single block by none. The slots depend on the plant seed and lie half a list apart. A wrapper that lies about the slice it recorded (`last_slice`) *and* faults
  only for `nsurv > 1` is invisible by design: the slice checks trust what the wrapper records, the
  self-test and sweep run 1-wide windows, and the audit (the only control that puts several survivors
  through one K2/K3 launch) reads K2's seeds and K3's dump kernel, not the hit kernel.
* (d) `--dry-run` and NO MATCH both exit 0: the status line (`DRY RUN (estimate only)` versus `NO MATCH (verified)`)
  tells them apart, so a script must read it, not just the exit code. So does a successful `--bench`: it
  exits 0 like a verified NO MATCH and prints a JSON object (`plant_found`, `coverage_exact`, the rate) instead
  of a status line; a bench whose plant or coverage failed exits 4. Exit 5 (`max_blocks`) is never a
  result.

Runs projected over `--max-hours` (default 1, at most 168) are refused unless `--force`. `--dry-run` and a
run refused for a missing sign-off never touch the GPU. A real run measures its rate on the GPU before the cost
guard decides (the rate probe: a dummy target and a planted seed, never the prize), so a run refused by the cost
guard has run the rate probe and nothing else: no search engine is built, no checkpoint, ledger or FOUND file
is written. `--assume-rate` is accepted only with `--dry-run` (a real run
measures its own rate; `run_search` refuses `assume_rate` outside a dry run too); `--max-hours` /
`--assume-rate` must be finite and > 0; `--dry-run` and `--bench` are mutually exclusive. A checkpoint
resumes only into the same prize, config, block size and engine sources (`sig_gpu` hashes
`kernels/sp_*.cl`, the base kernels, the engine's `.py` files, the CPU reference `setperm_solve.py` /
`template_solve.py` and `english.txt`); an empty checkpoint file counts as new, one with `done` lines
but no `PLANT` line is refused. An unreadable config or an unusable checkpoint / ledger path is a
`cannot run` message with exit 1, not a traceback. Exit codes: 0 no match (or a dry run, or a successful
`--bench`), 1 cannot run (config/engine/file), 2 found, 3 refused (no sign-off, or the cost guard), 4 void,
5 stopped early (`max_blocks`: not a result), 64 usage error. Both compressed and uncompressed keys are checked. The prize address is only
the default of `run_search(prize_addr=...)`; tests and `--bench` use self-generated targets.
Limits: passphrase <= 99 bytes, <= 4096 passphrases, tokens <= 16 bytes, mnemonic <= 239 bytes,
<= 8 paths of depth <= 8. K1 launches are capped at 2^25 candidates and every launch is followed
by the 5-second heartbeat / thermal check (pause above 85 C).
