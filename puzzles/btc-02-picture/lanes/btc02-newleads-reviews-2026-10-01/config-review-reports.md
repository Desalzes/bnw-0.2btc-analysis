# Config review against the lead documents: the Sonnet reviewer's two reports, 2026-10-01

Saved by the leads session from the agent's own final message(s) (transcript `agent-a34263785d5db0700.jsonl`), unedited.

---

_Final message of 2026-10-01T18:30:06.939Z_

**Method.** I read the three documents, the generator and the L5 files. I then wrote an independent parser and re-derived every convention from its rule. For each of the 289 `.conf` files I compared scheme, WORDS, FIX slot→alternatives, REQUIRE multiset, POOL, PASS set, PATH list and raw count. I also scanned every config against 456 record configs for containment. My scripts are in the session scratchpad only (`verify_nl.py`, `containment.py`); nothing was written to the repo.

## Findings

No config tests something other than what the documents propose. All 289 match my reading field by field. The findings below are coverage, duplication and inherited gaps.

| Sev | Config / family | Document says | Config has / issue |
|---|---|---|---|
| NOTE (missing from COVERED) | `nl7_r2_ep_{e13,e12,p}` | L4-04 4f: R2[eye,pyramid] "23.5% covered (E13/E12), 5.5% (P)" | Identical to record `templates/leads-2026-09-30/ld36_sealwords_anywhere_{e13,e12,p}`: same FIX 1 3 10 12 (+13), REQUIRE F5+eye+pyramid, 1 open, same PASS and PATH. Q21 ran it ("94 of 94"). `_e13`/`_e12` already re-ran in RESULTS.tsv. `nl7_r2_ep_p` (stage A3, 0.025 h) is still queued and can be dropped. |
| NOTE (missing from COVERED) | `nl3_ped_lines_b` (and `_e`) | L4-05: pedestal line numbers "never run" | All 120 BIP39 candidates lie inside record `ld05_slot1open_p` (open word = moon@2, subject@8, empty passphrase always tried) and inside `ld02_subjectpass_p`; both ran. `_e` also lies inside `ld05_slot1open_e12` and has 0/120 valid. `nl3_ped_lines_e13` is not covered. |
| NOTE | `nl2_zm_{x}_e` (11) | Z_M, no extension | Contained in `nl6_zmw_{x}_e12`, because the empty extension is always tried. About 8 s each. `nl8_e{1,2}_tight_e13` is likewise inside `nl8_e{1,2}_ea_e13`. |
| NOTE (silent change) | `nl4_ec_a15_voteopen` | LEAD 4: E-c 41 min | Adds `PASS moon` as in the L5 draft conf. Same raw count 158,544,691,200, but 82 min. |
| NOTE (silent change) | `nl2_zm_{x}_b` (11) | Z_M: no passphrase, P 22.6 min for 12 sets | Adds PASS X / x / Title (LEAD 6 item 10 folded in). 4× cost, 0.1446 h each. |
| NOTE (silent change) | `nl8_*_moonword_p`, `nl3_g2_minus1_e13` | Not specified (paths: "tight tiers") | The moonword shape is the record's c01a with vote released, a superset of N-0 (verified against `ld74_pathsA_moonword_p`). `nl3_g2_minus1_e13` is an extra Electrum-13 form that the document does not give. |
| NOTE (seal cross) | `nl7_seal_cross_{e13,e12,p}` | L4-04 4d: 33 × 39 pools, 1,897,758,720 | Nothing dropped. All 72 words are BIP39 and the lists are identical to the document, in the document's order. The count holds exactly. Slot 11 includes food, one and order, so some candidates duplicate a REQUIRED floater, as in the document's own count. |
| NOTE (self-referential assert) | `nl7_r*` | R2 82,575,360; R3 743,178,240; R4 7,431,782,400 | The generator's `expect` is computed from the config itself. I checked DRYRUN.tsv against the document's numbers: all R2, R3 and the R4 clock quad equal them. |
| PARTIAL | `nl2_z_d12_p`, `nl2_z_d12_e12` | LEAD 2 Z_P: PASS "" then MOON/moon/Moon "then the top 50 strings of LEAD 6" (L2 LENS-08 repeats it) | Only "" + MOON/moon/Moon (E12: "" + moon). The 50-string tier is in neither generator. |
| PARTIAL (inherited from `paths_L5.txt`) | `nl8_b8/b9/b10_*` | LEAD 8 table: `m/n` and `m/0'/n` for n in {1,2,3,10,13,21}; L5-06 adds `m/0'/1/n` | Absent: `m/3`; `m/0'/{1,2,3,10,13,21}`; `m/0'/1/{2,3,10,13,21}` (12 paths). |
| NOTE | `nl8_b1`, `nl8_b3`, `nl8_x01..x12` | LEAD 8: receive 11–13 and 20–99, change 4–18 "not run" | 24 paths (receive 11–19, change 4–18) also sit in record `runorder-2026-09-30/ro_s2d_paths{D,E,F,G}_{p1,moonword_p}`, the same shapes. The configs say DRAFT and STATE has no run line, but briefing §2.6 says stage 2d ran them. Check the ledger. The x-batches also add receive 14–19, outside the document's list. |
| NOTE (second generator) | `gen_newleads_raw_20261001.py:284` | L4 L11 has 39 words | Its L11 lacks "eternal" (38 words). It only feeds the T1a picture pool. The first generator's L9 and L11 are exact. |

My own derivations agree with both the document tables and the files. Derived and confirmed: G2 (slot−1), G1 (slot+1), G3b (12−p and 13−p), C3 ((s+1)/2), C2 lower/upper numerals, C1 (12 as 0), C2b, pedestal lines, G4 (list indices: slot 9 = absurd|abuse, … slot 12 = accident|account). Independent valid counts for the 11 small zero-unknown configs equal the `cpu_exact_valid` column: 51, 2, 14, 1, 22, 5, 57, 2, 4, 0, 38. `nl3_ped_lines_e` has 0 of 120 valid.

## COVERED.tsv verdicts

1. C1 12-word BIP39: TRUE. `m1v_moon1_vote12` is exactly the 720-candidate set. `m1_moon1_black10` is a superset with vote as the open word. All ran NO MATCH on 09-23. The document's premise that subject was kept at slot 1 is wrong: subject is REQUIRED in these configs.
2. C1 12-word Electrum: TRUE. `m1e_moon1_black10` is a superset and ran.
3. C2 lower, BIP39: TRUE. `h1_lower_numeral` (tower@1 eye@4 pyramid@5 black@10 moon@12, REQUIRE subject+F5, 1 open) contains the 5,040 sets with vote as the open word. It ran.
4. C2 upper, BIP39: TRUE. `u1_upper_numeral` is a superset and ran.
5. C4: TRUE. `ld33_mirror_p` is exact (PASS PYRAMID/pyramid/Pyramid, 5 BIP paths). `ld33_mirror_e12` (PASS pyramid) and `ld33_mirror_e13` (pyramid@13) are exact too. The document's "E13 only" is wrong. All three ran in Q21.
6. LEAD 7 duplicates, E13: TRUE. `ld33_dup_e13` has FIX 1 subject, 3 tower, 9 tower, 10 black, 11 moon, 12 vote, 13 moon, REQUIRE F5, 1 open: the exact 4g set. The document's "never run" is wrong. The P/E12 duplicate forms are new, as generated.
7. Z_M minus ten: TRUE (internal). `nl2_z_d12_p` and `nl2_z_d12_e12` contain it because the empty passphrase is always tried.
8. Not in COVERED but should be: the R2[eye,pyramid] and ped_lines_b overlaps in the table above.

## Completeness (no config and not in COVERED)

1. L4-09 G3 14-word form (p→15−p). Electrum-14: moon@2 vote@3 pyramid@4 black@5 eye@6 tower@12 subject@14, REQUIRE F5, 2 open, 10,569,646,080. The document says "queued (stage 8.4)", but the record's `stage8-2026-10-01/s8_l38_reversed_*` are 12/13-word forms and stage 8 never started.
2. The paths listed in the table's PARTIAL row (12 paths).
3. Z with the top-50 LEAD 6 strings (E12 about 6 min, P about 1.5 h).
4. L4-01 j-ladder, j=1..5, zero-unknown: 30,240 / 604,800 / 7,257,600 / 54,432,000 / 239,500,800 candidates summed over subsets. Also the optional "core10 + 2 of 12 weak-pool words", 66 sets.
5. Row 10 D1[black/subject/tower] in model P: not generated and not in COVERED or the repo `QUEUE-ORDER.txt`. They exist as `review-2026-09-30/rv_drop_{black,subject,tower}_p.conf`, which match the L4-08 spec (other five pins incl. V12, REQUIRE F5, 2 open, MOON/moon/Moon). STATE 14:20 says they are in the live order. The repo `QUEUE-ORDER.txt` still lists `nl3_ped_lines_e` (stage A), which the live order excludes.
6. For information: neither generator has Z_E13 raw (13!) or the S08–S14 raw 12!; both are raw items.

Confirmed intended omissions:
- R6 is absent.
- The M(1,1) cells are absent.
- R3 in model P: 18 of 20 absent; only the clock triple and the non-clock trio exist.
- 4c and 4e are absent.
- `exact_strings.py` covers S01–S18, E01, T01 and 21 art texts: native Electrum with extension ""/moon on `m/0/0-19` and `m/1/0-5`, and raw BIP39 with passphrases ""/MOON/moon/Moon.

## Verdict

No WRONG findings; the files I checked file by file are all of nl2, nl3, nl4, nl5, nl6, nl7, nl8 (B, E and x batches) and nl10, 289 in all. Two generated families duplicate record configs that ran (R2[eye,pyramid], ped_lines_b), and COVERED.tsv should list them. The gaps are the ones above.

---

_Final message of 2026-10-01T19:14:29.611Z_

**Result: no WRONG config.** I parsed all 221 `.conf` in `harness/templates/newleads-raw-2026-10-01/` with my own parser and compared each against my own derivation from the documents. The fields checked were scheme, WORDS, FIX slot→alternatives, REQUIRE, POOL (exact case), PASS/PASSFILE, PATH and raw count. 0 mismatches, and the DRYRUN.tsv raw and passphrase counts match mine.

The 22 `nr3_*` twins equal the record or `nl3_*` config they twin: FIX, REQUIRE, POOL and PASS are identical. Only PATH differs, because raw uses BIP paths where the Electrum twins use Electrum paths. I also compared the tier-1 and tier-2 shapes with the `pt_*`, `e3`, `p6` and `p6e` configs. Scripts are in the scratchpad only (`verify_raw.py`, `rawlib.py`); nothing was written to the repo and no engine or derivation was run.

## Findings

| Sev | Family / config | Document | Config |
|---|---|---|---|
| PARTIAL | `nr1_*_{upper,title,pic}_*` | L5-structure.md:207 (L5-07): N-1 "× 3" = ALL CAPS, Title, first-word capital; suffix, double-space and comma variants | upper and title are exact. `pic` is an addition. First-word-capital (`FIX 1 Subject`, rest lower, lower pool) is absent. Whitespace, suffix and comma forms need an engine directive; Electrum's import collapses them anyway. |
| PARTIAL | `nr6_t1_*` | LEAD 6 "tier 1 for the curated set (25 min in P)" = L2-russian.md:69,116-117, T1-P/T1-E12: drop one of {moon food this real one order}, 6×720×2048 = 8,847,360 | Only the drop-moon member and the `pt_*` shapes exist. The five drop-one-of-F5 shapes with the curated strings are absent. The record's `ro_s5_t1_loo_*` carry only the 116 stage-5 tokens, and the config headers say DRAFT. |
| PARTIAL | `nr6_t2_*`, `nr3_slot1open_*` | Lead doc l.655-662 (§8.1): CHARLEY/Charley/charley on tier 0, tight 1-open shapes, F1 slot-1-open, and the E3 two-open | Tier 0 and the tight shapes carry them. The F1 twins have MOON/moon/Moon only. Tier 2 has only the top six strings. |
| PARTIAL | `nr11_t2_np_raw12` | L5-structure.md:166: PASS MOON, then moon, then Moon, 10.7 h each | PASS MOON plus the always-tried empty pass, so DRYRUN says 25.5 h. moon and Moon are absent. The lead doc (LEAD 10) names only "P with MOON". |
| PARTIAL | `nr11_t1a_*` pool | L5-structure.md:162: L = DEPICTED 66 + marked + Bush pool + community slot words + tokens | 428 entries. DEPICTED 66/66, Bush 296/296, the 13 device words, and the 18 tokens (lower and UPPER) are all in. "Community slot words" is only the lead doc's own lists (slot-10, slot-12, swap words, L9, L11). The community slot tables are not in it. Missing BIP39 words include proof, receive, sword, glove and apple, plus 25 more from NEW-LEADS.md "Slot-word alternatives" and briefing §6.3 C8. Missing non-BIP39 words: wrinkle, lace, button, crown. 14 words of the digest strings S01–S18 are also absent (adapt agree air answer eight faith guide history inhale life proof six system win). |
| PARTIAL | `nr9_t3d_*` | L5-structure.md:148: token in the open slot of N-1 / N-2 / E-a (12,960) | N-1 (raw and Electrum), N-3 and N-2 (raw, `tok13`) exist. E-a in Electrum (13 words, moon required, slot 13 = token) is absent. |
| PARTIAL | `nr5_*`, `nr2_z_*` | L5-structure.md:180: raw 12! sets, "4 PASS strings multiply by 4 (2.1 h per set)" | `nr5_*` and `nr2_z_d12` carry the empty passphrase only; `nr2_z_u12t` has MOON. L4's `l4_z_p` asks for MOON/moon/Moon. |
| NOTE | `pic` mapping | Picture text: MOON, TOWER, FOOD labels; "THIS" in the header; real and one lower case; "Order and stability"; subject in the Amendment copy | The mapping fits all of it. `subject` is lower case in the running text, so lower is right. `black` → BLACK is an inference: REPORT-2026-09-29 §3.4 prints the Latin proverb as "NIGER" in capitals, lead doc l.505 says that line is lower case except its first letter, and the only printed English "BLACK" is in the BLM slogans. `eye`, `pyramid` and `vote` have no printed form. In upper/title all 2048 pool words are in the phrase's case; the `pic` pool is lower case. |
| NOTE | `nr9_t3c_*` | T3c: 5×18 configs, 132,710,400 raw | The restructure is exact. 30 configs per scheme sum to 132,710,400. It is an index-level bijection: 6P5 placements of the token and four floaters in 6 slots equals token slot × 5P4 placements of the four floaters in the other 5. A brute-force multiset comparison on a mini pool matched for all five floaters. |
| NOTE | `nr9_t3b_*` | T3b "19 alternatives" per slot, 6 pins, 159,252,480 raw | 18 alternatives per slot, which is what 6×18×1,474,560 = 159,252,480 requires (the draft conf's 19th is the original pin). Slot 13 is added (N-4 "as word 13"); raw forms use the 3 case forms. |
| NOTE | `nr9_t3d_tok13_raw13` | L5-03 T3d | Adds MOON and Moon as 13th-word alternatives, which no document asks for (40,320 vs 38,880). |
| NOTE | `nr6_j3_*` | L4-01 j-ladder; L4-10 row 1 | 20 configs × 9! = 7,257,600, the document's j=3 total; the j=1 and j=2 sets lie inside. BIP39 with the 116 tokens only. |

## Passphrase lists

- **`lead6_new.txt`:** it holds every backticked string of LEAD 6 items 1–10 (146 distinct, extracted programmatically from l.222-256), the three §8.1 strings, and 24 L4-10 sentence forms. Nothing is missing or mistyped. The single non-hit was the file name `picture_numbers.py`, which is not a passphrase.
- **`tier0_all.txt` (1,318):** it contains `lead6_new`, `l2_russian` (a byte-identical copy of the finder file), `june_russian`, and `record` minus one 100-byte `xxxx…` test string. It holds nothing from any other source, has no NFKD duplicates, and the longest string is 94 bytes.
- **`curated_new`, `*_electrum`, dictionaries:** `curated_new` equals (lead6 ∪ l2 ∪ june) − record up to NFKD. Twelve June "чёрный…" strings are absent only because they are NFC-equal to L2's; no loss. The Electrum lists equal the folded forms, and `stage5_tokens` equals the `ro_s5_t0_moonword_p` PASS lines. The BIP39 3-case (6,058) and digits (11,075) lists match my reconstruction exactly.

## Completeness (no config)

1. The items in the PARTIAL rows above: L5-07 first-word capital, the five T1-P/E12 drop-one-of-F5 shapes, CHARLEY on F1 and E3, T2-NP moon/Moon, T3d E-a, and the 4-PASS raw 12! sets.
2. L5-05 fixed tables raw (l.181). This is `ld41_18word{,_novote,_noliberty,_nopolice}_p` (120, then 3×1,474,560) and `ro_s2a_18word*_moonpass_p` (same, plus MOON passes). `ld40_21word_p` is 2.95M, and `e13a_marked` is 1.19e9 with DISTINCT (about 1.4 h). The `t21_*` configs are template-solver format. Only 12/13-word raw configs exist.
3. LEAD 1 N-5 (l.90): leave-one-out, two open, no filter, 7.55e9 per model. It is not in section 0.
4. L4-10 rows 2–10 and the ten device-linked pairs. Also the E12 form of row 1, the 1-open conventions with the 116 tokens (row 10), and the U13−vote ladder with j=4,5. Only row 1 in BIP39 exists.
5. Raw twins of the 2-open conventions (G1b `e14`/`e13ext`, G3b `e13ext`, G3 14-word): 10.57e9 raw each, about 11.7 h.
6. Electrum extension dictionaries for digits and the Russian list; only the BIP39-word list exists.
7. Row 11: T1a for the four structures and T2 per structure are present as designed; T1b is skipped, which is correct because it lies inside T2.

The known intended omissions (Russian frequency list, Z raw 13!, S08–S14 raw 12!, raw tier 1 on the moonword shape, T1b, brainwallet/WarpWallet/KEYFORM) are confirmed absent.

**Verdict:** no WRONG config. I checked all 221 files file by file: `nr1` (62), `nr2` (2), `nr3` (22), `nr4` (2), `nr5` (3), `nr6` (41), `nr9` (81) and `nr11` (8). The findings are coverage gaps (7 PARTIAL) and notes, none of them a transcription error.
