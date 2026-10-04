# What has been tested

Record to **2026-10-02 07:12** (local time). Every search here looked for a seed phrase for the "Brave New World" puzzle's prize address
`1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ`. They were run by the GPU code in this repository (`puzzles/btc-02-picture/harness/`; the earlier ones by the older solvers, below)
and the GPU runs are logged in `puzzles/btc-02-picture/ledger/`. The totals below are recomputed from `ledger/runs.jsonl` by `python3 tools/tally.py --windows`.
**Every finished run ended NO MATCH** (the ledger has no FOUND or VOID record; a few interrupted runs did not finish and are marked in
the tables). This is a record of what was ruled out; it is not a solution, and a NO MATCH covers only the candidates its own config
describes (see "What a NO MATCH means").

## What a run is

- **tested:** raw candidates enumerated. **valid:** candidates passing the BIP39 checksum (1 in 16 for 12 words) or Electrum's version
  prefix (1 in 256); under the raw scheme every candidate is valid. **derived:** full PBKDF2 to BIP32 to address, counted per
  passphrase, path and key form.
- **Three models:** E13 (Electrum, 13 words, moon the 13th, empty extension), E12 (Electrum, 12 words, extension "moon") and P
  (BIP39, 12 words, passphrase MOON / moon / Moon). The empty passphrase is always tried too. Models M (mod 12, moon first), H and U
  (a lower or upper numeral) ran early.
- **Shapes.** A config pins some words to slots (`FIX`), requires others to be present somewhere (`REQUIRE`) and leaves slots open over
  the full 2048-word list. The workhorse "tight" shape pins subject@1 tower@3 eye@9 black@10 pyramid@11 vote@12 (and moon@13 in E13),
  requires food, this, real, one, order anywhere in the six free slots, and leaves one slot open: 6! x 2048 = 1,474,560 candidates.
  "Two-open" shapes leave two words unknown over the full list: 7!/2! x 2048^2 = 10,569,646,080 candidates, or 84,557,168,640 when black
  is also free. Repeated words are allowed except in two configs. The directives are documented in `puzzles/btc-02-picture/harness/README.md`.

## What was derived

- **BIP39, 12 words** to `m/44'/0'/0'/0/0` to legacy P2PKH is the community standard, and the one the verified `harness.py` reproduces
  (Trezor test vector). **Electrum "standard" seeds** (version prefix `01`, 12 or 13 words) stay in scope because model P and E13 both
  need a 13th element; Electrum's segwit and 2FA seed types cannot give a `1...` address and are out by design. **Raw text** (the
  `bip39raw` scheme) derives what Electrum's "BIP39 seed" import derives from any text: no checksum, any length, case kept.
- **Paths searched.** BIP39: `m/44'/0'/0'/0/{0,1,10}`, `m/0'/0/0`, `m/0/0`. Electrum: `m/0/{0,1,2}`, `m/1/0`. Then, on the tight shapes only:
  one lead batch added account-0 receive 2-9, change 0-3, account-1 receive 0-3, Electrum receive 3-9 and change 1 (`ld74_*`); the run
  order's stage 2d pushed the two tight model-P shapes to receive 19, change 19 and account-1 receive 9 (`ro_s2d_*`); the new-leads queue
  ran 11 batches of `paths_L5.txt`, 12 batches for receive 11-99, change 4-18 and accounts 2-19 on the p1 and c01a shapes, Electrum
  `m/0/10-21` and `m/1/2-5` on the tight Electrum shapes, and, in the raw scheme, 82 further paths; a word hunt ran Electrum `m/0/10` and
  `m/1/10` and BIP44 accounts 9' and 10'. June's sets ran 28 legacy paths (hardened variants included) x both key forms x 6
  passphrases, on their own candidate sets only. E4, p7e and p6 at 40 paths are priced and not run.
- **Key forms.** The GPU engine checks compressed and uncompressed keys. The September CPU runs were compressed only and were re-run
  on the GPU (T0, 09-29), except the four CPU-only `t21_*` runs.
- **Not assumed:** that the phrase came from a wallet; that the passphrase is empty or MOON; that the 12 words are distinct.

## Totals

| Window (ledger time) | Runs | Tested | Valid |
|---|---:|---:|---:|
| to 09-29 17:12 (first day of the GPU engine) | 73 | 463,732,283,520 | 2,509,700,068 |
| 09-29 17:13 - 24:00 | 11 | 84,663,336,961 | 1,575,014,803 |
| 09-30 | 251 | 412,758,914,638 | 5,055,076,684 |
| 10-01 00:00 - 13:50 | 27 | 243,881,902,080 | 3,740,288,717 |
| 10-01 13:50 - 23:00 (new-leads queue, before the released block) | 271 | 59,674,179,240 | 862,693,794 |
| 10-01 23:00 - 10-02 07:10 (the released block) | 85 | 214,557,877,680 | 4,056,722,542 |
| **All** | **718** | **1,479,268,494,119** | **17,799,496,608** |

Of the 718 runs, 716 have distinct signatures; summed once per signature the total tested is 1,468,697,373,479. The families
overlap, so the total overstates distinct coverage. Every record has `blocks_verified + blocks_from_checkpoint >= blocks`, and
1,013,496 planted seeds were found and none lost.

## By family, in the order it ran

The ledger has no config-name field: it keys a run by `sig` (a hash of the config and the engine) and records the operator's sign-off
text, so runs group by sign-off. The words are the operator's, shortened. Times are ledger time; several queues shared the GPU, so
windows overlap. Every row is NO_MATCH, and the ledger has no FOUND or VOID record.

| Family | Sign-off (the operator's words, shortened) | Runs | Tested | First - last |
|---|---|---:|---:|---|
| E4 (pre-hardening engine, 1 plant per block) | none recorded | 1 | 84,557,168,640 | 09-29 05:19 |
| Stage 1: p6e, p7e, p6 | "Stage 1 now (~3.7 h)" | 3 | 105,696,460,800 | 09-29 11:24 - 09-29 14:46 |
| T0: the 61 earlier templates, both key forms | "can you test all the quick stuff first?" | 61 | 15,088,556,160 | 09-29 14:48 - 09-29 15:14 |
| Q16: Bush-speech pool, Electrum tiles | "Bush Electrum first (~1.7 h)" | 8 | 258,390,097,920 | 09-29 15:47 - 09-29 17:12 |
| Q15 stage 2: the seven p7 slices | "step 2 is good to go when ready" | 7 | 73,987,522,560 | 09-29 19:32 - 10-01 08:01 |
| Q17: Latin mottoes at slots 9 and 11 | "All quick Q17 runs (~30 min)" | 9 | 63,524,044,801 | 09-29 19:45 - 09-29 20:36 |
| Broad-search leads (16 signed off, 15 finished; `c06b` interrupted) | "youre good to run them" | 15 | 122,784,439,408 | 09-30 09:53 - 10-01 12:19 |
| `bnw-new` quick pass (LUN / plunge, ranked 9/11 pairs) | "can you run these through real quick" | 24 | 35,389,440 | 09-30 13:06 - 09-30 13:08 |
| Q18: second-pass word readings | "All 17 Q18 runs now (~30-45 min)" | 17 | 32,465,387,520 | 09-30 13:49 - 09-30 14:40 |
| Q19: replacement readings (leave-one-outs etc.) | "Everything, quick set first (~2.5 h shared)" | 23 | 44,217,630,720 | 09-30 14:40 - 10-01 04:32 |
| Readings (`rd_*`) | "yes run the quick ones first" | 15 | 30,275,665,920 | 09-30 15:55 - 10-01 08:47 |
| Q20: Electrum single-word drops (`rv_drop_*_{e13,e12}`) | "i want them run, yes" | 10 | 105,696,460,800 | 09-30 16:43 - 10-01 06:00 |
| Q21: the 75 leads of `LEADS-2026-09-30.md` | "i want these ran too, quickest first" | 94 | 37,908,712,470 | 09-30 16:51 - 10-01 10:26 |
| Q21 addendum: `passphrases.txt` on five shapes | "yes i want them" | 5 | 25,067,520 | 09-30 16:56 - 09-30 17:09 |
| Combinations (`cb_*`) | "yes" | 7 | 44,528,814,720 | 09-30 18:02 - 10-01 07:33 |
| Run order of 2026-09-30, 52 drafts | "i stopped everything else so you could start it. you have the go ahead" | 52 | 154,118,062,200 | 09-30 19:01 - 10-01 03:23 |
| Run order, 3 configs added after the 19:40 correction | the same go-ahead | 3 | 10,571,121,360 | 09-30 19:30 - 09-30 19:36 |
| Guardian passphrase (mak51mu5), six shapes | "yes good to run them" | 6 | 10,594,713,600 | 10-01 09:39 - 10-01 09:50 |
| Word hunt WH-P1 ("номер X" as a path number) | "e12,e13,p tight" | 2 | 10,571,120,640 | 10-01 13:30 - 10-01 13:35 |
| New-leads queue before the break (rows 1-9 of the second lead hunt's run order, in part; rows 10 and 11 had not run) | "...manage running all these in a logical order, you dont need to ask me permission as you have my permission now to run them in an order you decide" | 271 | 59,674,179,240 | 10-01 13:53 - 10-01 18:47 |
| New-leads queue, the released block (six rows, 85 configs) | "when the limit resets, run everything you listed." / "it has reset now. Please continue from where you left off." | 85 | 214,557,877,680 | 10-01 23:52 - 10-02 07:10 |
| **Total** | | **718** | **1,479,268,494,119** | 09-29 04:58 - 10-02 07:10 |

## Before the ledger: May to September

The ledger starts on 2026-09-29. Earlier work was done with the older solvers in `harness/` (`gpu_solve.py`, `template_solve.py`,
`setperm_solve.py`, `orderfirst_solve.py`) and is not in it:

- **May 2026** (`gpu_solve.py`, full 12! orderings of a fixed set): the two community candidate sets, and a "best-12" set (all 479,001,600
  orderings per passphrase, about 50 min each) for the passphrases empty, 10, ten, 11, eleven, X (about 94% complete when recorded),
  Guardian, одиннадцать, x and десять: all NO MATCH. Also 231 brainwallet variants (no match), and 0 of 54 Electrum-new combinations
  are even valid Electrum seeds.
- **June 2026:** finished NO MATCH: `set12` (all 12! orders of one set), `pool13` (13 sweeps), Russian-passphrase "hedges" on `set12`, and
  one more set. Not finished, and not counted as tests: one set stopped by the operator, one set about 71% done and another never started,
  a 90-passphrase run with no surviving result, one list of passphrases never run, and two partial sets. Other checks: 28 legacy paths x
  both key forms x 6 passphrases on June's sets, an acrostic (907k seeds), sliding windows over two texts, typo passphrases.
- **September CPU campaign** (`template_solve.py` / `setperm_solve.py`, compressed keys only): 65 runs, all NO MATCH, 15,088,556,160 tested
  and about 385.7M derived. Every checkpoint's unit lines sum exactly to its run total, every checksum-valid count is within 2.5 sd of its
  binomial expectation, and selftests passed before and after each solver change. Every run except the four `t21_*` runs was re-run on the
  GPU with both key forms in T0 or E4.

## What each family excludes, and what it does not

"Excludes" is exactly the family its config describes. Prefixes are config names under `harness/templates/`; every row is NO MATCH.
"Held" means a config is written and priced but was not run at the cutoff.

| Family | What ran | Excludes | Does NOT exclude |
|---|---|---|---|
| **Tight, 1 open word, full list** (`e1_*`, `e2_*`, `e1n`, `p1*`, `p2*`, `m1*`, `h1`, `u1`, `rd_*`, `q8_*`, `q12_*`, `q14_*`, `bnw-new`) | every model; black free, vote pinned or freed, moon@1, lower or upper numeral, eye as word 13 / passphrase / free, peace@11, this or section @1 | the device structure with exactly one wrong or unknown word; E1/E2 also with the extensions breathe, 10, ten, x, guardian | any second unknown; the pins wrong |
| **Two open words, full list** (E3, E4 and `c02b`, `p6e`, `p7e`, `p6`, `p7_black_at_*`) | E3: E13 black@10 (CPU). E4: E13 black anywhere. p6e / p7e: E12. p6: P black@10; the seven slices: P with black at each other free slot | for E13, E12 and P: subject@1 tower@3 eye@9 pyramid@11 hold, food / this / real / one / order all present, ANY two other words (repeats allowed), empty passphrase (+ MOON / moon / Moon in P, "moon" in E12); vote@12 is not assumed | any pin wrong; three or more unknowns (about 29 h per word in E13, 170 days for three full-list unknowns); passphrases other than those listed |
| **Slots 9 / 11 alternatives** (`e1n`, `p1n*`, Q14, Q16, Q17, Q18, `bnw-new`, `rv_drop_eye/pyramid_*`, `ro_s3_*`, `ld54`, `ld36`, `ld43`, `ld44`, `ld47`, `ld13`, `ld48`) | 14 x 19 object pairs; 144 9/11-themed pairs; pupil x 19; "twin tower" as a date; 169 meme pairs; Bush pool (296 words, Electrum only, 2 x 129.2B); Latin-motto words; the 13 x 12 second-pass cross; where/there; slot 9 or slot 11 over the full list (Electrum two-open, model P one-open tiles); dial-strip words; camera / security @9; one / order / food / vote @11 | any single 9 or 11 word with the other kept: in Electrum (E13, E12) over the full list, in model P only as the one-open tiles (`ro_s3_*`); those named pairs in the tight shape | both 9 AND 11 over the full list (3 unknowns: 26 h E13, 2 days E12, 2 months P); the model-P two-open drops of the words at 9 and 11 (held); the Zeitgeist pool (dropped by the operator: "drop zeit"); Bush and Zeitgeist in P |
| **Leave-one-out and drops** (`p5*`, `q19_loo_*`, `rv_drop_*`) | each of food / this / real / one / order replaced by any word + one more (E13, E12, P: all 15 `q19_loo_*` runs; Q19 as a whole is 23 of 23 runs); one of eye / pyramid / black / tower / subject dropped and replaced by any word + one more (E13 and E12; in P black, and 9 / 11 one slot at a time) | "one of the five floaters is not a seed word"; one pin's word is wrong (every model for black; Electrum for all five) | two simultaneous wrong words; the held model-P drops of subject, tower and the floaters at 9 / 11; the stage-8.5 slot-1 leave-one-out forms (`s8_slot1_loo_*`, held) |
| **Moon readings** (`m*`, `h1`, `u1`, `q8_*`, `ld19`, `ld15`, `ld21`, `ld46`, `ld49`, `ro_alt_*`, `q18_d`, `q19_alt`) | moon@1 (mod 12), @2, @12 with vote@13, moon an ordinary word; lunar / lunch / plunge / LUN @13; month, second, hour, world, blood / pink / black, subject, minute, clock, three at 3 or 13; passphrase moon vs moon as a word | alternatives to "moon is word 13 or the passphrase" at the listed spellings | other spellings; non-English moon words beyond луна, месяц, lundi |
| **Passphrase and extension tokens** (`q12`, `c01*`, `c02*`, `c06*`, `h04`, `rd_xx_*`, `pt_*`, `ld_pass_*`, `ro_s5_*`, `q18_c`, `q14_r4`, `gd_*`, `nr*`, `nl*` tier 0 and 1) | BITCOIN, BREATHE, Tuesday, BLM, RUSSIA, dates (55 forms), XX / 20 / 2020 / twenty / двадцать, 21 / XXI, FOOD / THIS as passphrase, Order and stability, Brave New World / NWO, NIGER, LUN / PLUNGE, Miranda / Rosalind, Biden / Trump, FUCK THIS SHIT / SHT, blood moon, MUNDUS, Bill Cipher, subject, Guardian, CHARLEY and the signature forms, `passphrases.txt`, a 173-string batch, 116 run-order strings, the 1,318-string tier-0 list on the twelve device words (raw and Electrum, strong and weak pins), 818 more at tier 1 | those strings on the one-open shapes (BIP39 and Electrum), and at tier 0 in raw form (raw tier 1 and the raw tokens-in-phrase forms are held); the E3 shape for the first five tokens; the E4 shape (`c02b`); the E12 two-open shape for the first five (`c01e`) | strings never listed; `c06b_e12_date_ext` (the 55 date extensions at E12 two-open; interrupted at 379 of 630 blocks); the same strings at E4 / two-open in P (84.6B); the 144,644-string Russian frequency list (not downloaded); tier 2 (held) |
| **Position alternatives** (`ld05`, `ld02`, `ld11`, `ld20`, `ld42`, `ld36`, `ld33`, `ld38`, `ld08`, `ro_alt_*`, `rd_this_section1_*`) | slot 1 open with subject anywhere; subject@13; food@3 with tower loose; vote@2 / 11; swap words @12; count pins; twenty / one @10/11; mirrored dial and reversed pins (tight forms); this@1 / section@1 | single-slot changes to every pin in the tight shape, and several two-open forms in E13 and E12 | the stage-8 two-open forms of L33 / L36 / L38 and slot 1 at tier 2 (held); `h02` (this@1 in P two-open) |
| **Other lengths and tables** (`ld41_*`, `ld40`, `t21_*`, `e13a_marked`) | the 18-word and 21-word community tables, rarsn4's t21 variants (CPU) | those exact tables | raw 21 / 24-word seeds (about 17 unknowns); BIP39-15 with the pins (about 170 days) |
| **Paths** (`ld74_*`, `ro_s2d_*`, new-leads row 8, WH-P1, June `pathsweep`) | the paths listed under "What was derived" | the tight shapes at those indices | E4 / p7e / p6-scale reruns at 40 paths (priced); `wh_x10_e12_open2` (Electrum-12 two-open at `m/0/10` and `m/1/10`; interrupted at 12 of 21 blocks) |
| **Community sets and rules** (`c03`, `c04*`, `c05`, `c08`, `c09`, `h01`, `ld22`, `ld69`; May and June 12! sweeps) | the void-CUDA set 12!; date-index words; a sum-60 rule; the WELCOME matrix; an order with breathe at 13; a skeleton; colour words; May best-12 and the two community candidate sets x about 10 passphrases; June set12, pool13, hedges and one further set, and a community slot table x 31 passphrases | those exact sets in those orders and passphrases | unfinished June runs; every thematic set nobody listed |
| **New 10-01/02 structures** (`nl*`, `nr*`, `ec`, `gd_*`) | raw (no checksum) tight tiers, picture-pool two-open, the twelve device words in every order (filtered and raw), conventions, R2 pin pairs, the clock triple and quad, the non-clock trio, E-c, tokens in the phrase | the structures named in this row, on the paths and passphrases listed | the full-list raw two-open tiers and the R3 triples in Electrum (held); three or more unknowns |
| **Closed without compute** | brainwallets, Russian BIP39, Electrum-old, book / acrostic / sliding windows, file stego | settled by reasoning and CPU checks; no GPU run, nothing in the ledger | |

## Which slots have been opened over the full 2048-word list

| Slot(s) | Opened over the full list | Not opened |
|---|---|---|
| 2, 4-8 (no evidence) | singly in every tight shape; in pairs in E3, E4, p6e, p7e, p6, p7 (all three models) | three at once |
| 1 | `ld05_slot1open_*` (subject required anywhere; all three models); `ld02`; subject dropped: `rv_drop_subject_e13/e12` | `rv_drop_subject_p`; slot 1 at two-open (stage 8.5, held) |
| 3 | `rv_drop_tower_e13/e12`; `ld11` (food@3, tower loose) | `rv_drop_tower_p` (held) |
| 9, 11 | one slot with the other kept: `rv_drop_eye/pyramid_e13/e12` (2 open), `ro_s3_slot9/11open_p_t1..5` (model P, 1 open) | both at once (3 unknowns); the rest of the model-P two-open drops (floaters at 9 / 11, held) |
| 10 | black free in E4 and p7e and, in P, black@10 (p6) plus the seven p7 slices (black at each other free slot); black absent: `rv_drop_black_e13/e12/p` (all three models, P on 10-02) | |
| 12 | vote released in the E3, E4, p6, p6e, p7, p7e, Q17 and Q18 two-open forms; pinned at 12 in the Q19, Q20 and `ro_s3_*` runs | |
| 13 | moon@2 with slot 13 open (`ld19_moon2_e13`, `ro_alt_moon2_open2_e13`); slot 13 over the full list in Electrum-13 (E-a, E-b, 10-01); otherwise only named words | the passphrase over a full list in any model |

## What a NO MATCH means

- **A NO MATCH excludes exactly the family its config describes, and nothing wider.** A pinned slot, a pool or a passphrase list that
  leaves the true answer out cannot find it. It is not a statement about the puzzle, only about the config's family.
- **GPU runs:** a verified NO MATCH rests on the self-test, slot sweep and slot reach; 64 planted seeds per block found in place (1 per
  block for E4); exact index-sum and coverage checks; a binomial survivor count; a per-block window audit; and parity tests against
  the CPU tool. It is sampled evidence, not proof. With the prize at two target slots, a fault confined to one slot can no longer hide it.
- **E4 ran on the pre-hardening engine** (one plant per block, one prize slot) and was not repeated as such; `c02b` re-covered its space on
  the hardened engine on 10-01 (valid count 330,317,316, equal), so that gap is closed.
- **CPU runs:** a NO MATCH rests on the solver selftests, checkpoint reconciliation and binomial counts; they checked compressed keys
  only, which T0 has since covered.
- **Raw runs:** valid equals tested exactly (the checksum filter is off), so the binomial check does not apply; the same self-test,
  slot sweep, slot reach, 64 plants per block and window audit apply.
- **Unchecked by design:** three or more unknown words; any wordlist other than BIP39 (and Electrum's `01` seeds); passphrases outside the
  lists; address types other than legacy P2PKH (the target is P2PKH); paths and accounts beyond those listed above.

## Why the runs can be trusted

The engine's trust model is in `puzzles/btc-02-picture/harness/README.md` ("Trust model"). In short: the self-test, slot sweep and slot
reach; 64 planted seeds per block found in place; an exact 64-bit index sum and `tested == scanned`; a binomial survivor-count check
(a block beyond 8 sigma is VOID; the observed |z| stayed below 4; the lowest blocks of p6, p7e and c01e, and c01b in full, were recounted
exactly on the CPU by scripts sharing no engine code); a per-block window audit; and parity tests against `setperm_solve.py`. What is
left is a fault well under 1 hit in (64 x blocks) at both prize slots, an unaudited survivor set outside the per-block windows (bounded
by the parity tests and the binomial check), and trusted checkpoint `done` lines.

Independent checks, as reported (agent-written; the reports are in `puzzles/btc-02-picture/lanes/`):

- **Review of 09-29** (`lanes/btc02-gpu-review-2026-09-29/REPORT.md`): the committed code is what ran; a separately written encoder inverted the
  index space on about 1,500 indices of each of the 62 supported templates; an oracle sharing no code with the engine agreed with its
  CPU reference, and the pipeline found 40 of 40 oracle targets over all 8 (path, key form) combinations. Fault injection: 9 of 10
  effective faults ended VOID; the one that passed led to a fix (64 plants per block, the prize at two slots, a required `--signoff`).
- **Audit of 10-01** (`lanes/btc02-gpu-audit-2026-10-01/REPORT.md`): no defect found that could have caused a missed answer. An independent
  parser and decoder agreed with the engine on all 461 GPU configs (143,747 sampled indices plus 17.95M enumerated, 0 mismatches); all
  3,226 PASS lines x every path x both key forms equal an oracle (33,804 derivations, 0 mismatches); an exact CPU recount of tested and
  valid matched the checkpoints for 250 of 333 runs, and one sampled block for 25 more.
- **Review of the raw scheme** (`lanes/btc02-newleads-reviews-2026-10-01/`): the engine derives exactly what Electrum 3.3.8 does, checked
  on 1.11M code points and 447k token pairs through the real parser, 0 mismatches; 49 mutants of the new guards and kernels, 45 killed,
  4 survived and none reachable for a raw run. One real finding (a `#` inside a config line was read as a comment, so a run could search a
  smaller space than its config states) was fixed test-first; no config on disk had one, so no result is affected.
- **Ledger reconciliation** (`harness/ledger_recon.py`, as of 10-01 08:45): every ledger run matches an unchanged config on disk (E4 excepted); `tested`
  equals the candidate count everywhere; checkpoint `valid` equals ledger `valid` for all 350 checkpoints; binomial |z| < 4 throughout. It
  recomputes each run's signature under the engine it is run from, so it matches the runs of the engine this repository holds; runs of the
  two earlier engine eras show as NEVER, which is expected.
- **Gaps that remain, as the audit listed them:** a fault that is shared by the planted seeds and the real candidates is invisible to runtime
  controls and was checked only by the independent oracles; a survivor loss confined to a small sub-range passes about 9 runs in 10 (ruled
  out at count level only for the recounted runs); Electrum's old-seed rule and the CJK whitespace step are not modelled (no config is affected).

Run the engine's CPU checks yourself: `SP_NO_GPU=1 uv run --no-project --python 3.12 --with coincurve --with numpy --with pyopencl python gpu_setperm_test.py`
in `puzzles/btc-02-picture/harness/` (after `python3 tools/fetch_kernels.py`); it prints the result recorded in `expected_output/suite-cpu.txt`.
