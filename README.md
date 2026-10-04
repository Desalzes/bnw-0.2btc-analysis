# 0.2 BTC "Brave New World" puzzle: GPU search code and test record

Target: [`1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ`](https://mempool.space/address/1KfZGvwZxsvSmemoCmEV75uqcNzYBHjkHZ) · still unsolved. The prize (20,107,284 sat in 5 outputs) has never been spent; read from the chain on 2026-10-03.

This repository holds the GPU code that searched candidate seed phrases for the puzzle, with its tests and independent checks, and a
record of what it tested, so that a "NO MATCH" can be checked and not just believed. It contains no solution. Record cutoff
**2026-10-02 07:12** (local time); later runs are not in this export.

**[What was tested (TESTED.md)](TESTED.md):** 718 verified GPU runs, 1.48 × 10¹² candidates (1,479,268,494,119), every finished run NO
MATCH, with what each family of runs excludes and what it does not. The figures are recomputed from the run ledger by `python3 tools/tally.py`.

## What we've tried, at a glance

Condensed from TESTED.md and the configs so you can see what is covered before you spend compute. It is cut at **2026-10-02 07:12**
and counts only searches that finished NO MATCH by then; a NO MATCH covers only its own family of candidates, never more. Names
like E3, E4, p6 and p7e are the named searches defined in TESTED.md. Everything else is in the repository: pull it, the configs are
in `puzzles/btc-02-picture/harness/templates/`.

### Positions: what the art pins, and what we tried at each slot

<!-- words:start -->
| Slot | The art's pin | Other words tried at this slot (most-used first, not complete) | Opened over the whole 2048-word list? |
|---|---|---|---|
| 1 | `subject` | `moon` · `this` · `tower` · `vote` · `pyramid` · `section` · `base` · `model` · `food` · `ubi` · `lunar` · `lunch` | yes, one slot at a time, in all three models; `subject` dropped in E13 and E12. Not in model P, not at two unknowns |
| 2, 4 to 8 | none (free) | `camera` · `mask` · `police` · `liberty` · `hand` · `rifle` · `twin` · `face` · `head` | yes: one at a time in every tight shape, in pairs in E3, E4, p6e, p7e, p6 and p7; never three at once |
| 3 | `tower` | `eye` · `minute` · `food` · `black` · `pyramid` · `clock` · `all` · `order` · `vote` · `ibi` | `tower` dropped in E13 and E12 (and `food` at 3 with `tower` loose). Not in model P |
| 9 | `eye` | `know` · `learn` · `tower` · `discover` · `pyramid` · `where` · `sight` · `witness` · `glory` · `provide` · `protect` · `trade` · `two` · `top` · `view` · `spy` · `observe` · `monitor` · `guard` · `light` · `shine` | one slot at a time with the other kept (Electrum two unknowns; model P as one-unknown tiles). **Never 9 and 11 together** |
| 10 | `black` | `day` · `twenty` · `fortune` · `wheel` · `one` · `need` · `pyramid` · `abuse` · `access` · `number` · `rain` · `ten` · `tower` · `eye` · `system` · `food` | `black` free (E4, p7e); in model P `black`@10 and the seven slices of p7; `black` absent in all three models |
| 11 | `pyramid` | `world` · `just` · `peace` · `one` · `vote` · `build` · `step` · `level` · `know` · `cause` · `good` · `home` · `country` · `moon` · `there` · `food` · `stamp` · `circle` · `coin` · `brick` · `stone` | as slot 9. **Never 9 and 11 together** |
| 12 | `vote` | `moon` · `swap` · `switch` · `exchange` · `change` · `flip` · `trade` · `debate` · `blue` · `twelve` · `second` · `accident` · `account` · `black` · `subject` · `pyramid` · `enemy` · `face` · `match` · `oppose` · `party` | released to any word in E3, E4, p6, p6e, p7 and p7e |
| 13 | `moon` (Electrum-13's 13th word) | `subject` · `month` · `second` · `pyramid` · `world` · `plunge`, and the fifteen generator words `ability` to `acid` | slot 13 over the whole list in Electrum-13. Never the passphrase over a whole list |

### The words, by how much of the search is built on them

| Tier | Words | How they were used |
|---|---|---|
| 1: the core | `tower` · `subject` · `black` · `food` · `real` · `order` · `this` · `one` · `eye` · `pyramid` · `vote` · `moon` | pinned to a slot or required in the largest share of the searches |
| 2: the marked tokens | `breathe` · `bitcoin` · `blm` · `tuesday` · `lun` · `stop` · `freedom` · `white` · `death` · `kill` · `money` · `war` · `hate` · `trusted` · `slave` · `slavery` · `niger` · `russia` | each pinned, one at a time, at every slot from 1 to 13 (raw-text and Electrum-native searches) |
| 2: other widely tried words | `ten` · `world` · `know` · `second` · `day` · `camera` · `just` · `peace` · `mask` · `country` · `glory` · `home` · `good` · `gold` · `cause` · `liberty` · `police` · `face` · `rifle` · `time` · `two` · `party` · `hand` | placed in many different searches |

Everything else (single leads and one-off pools) is in the configs. The whole 2048-word list was opened at one or two slots in most searches.

### Passphrases and extensions tried

| Group | Strings (casing variants were tried for most) |
|---|---|
| the moon casings | `moon` · `MOON` · `Moon`: the largest share of the searches (Electrum folds them into one extension) |
| breathe | `breathe` |
| numbers and dates | `2020` · `20` · `xx` · `xxi` · `twenty` · `twenty one` · `21` · `2021` · `1865` · `1865-2020` · `18652020` · `1865-2021` · `1776` · `mdcclxxvi` · `july iv mdcclxxvi` · `05252020` · `05.25.20` · `052520` · `11032020` · `11.03.20` · `110320` · `21000000` · `1628` · `xxxx` · `moon2020` · `двадцать` · `двадцать один` |
| tuesday | `tuesday` · `вторник` |
| blm | `blm` · `blm xx` · `blmxx` · `blm 2020` |
| words and names | `bitcoin` · `russia` · `niger` · `lun` · `plunge` · `miranda` · `rosalind` · `biden` · `trump` · `байден` · `трамп` · `bill cipher` · `gravity falls` · `blood moon` · `mundus` |
| single words | `food` · `this` · `subject` · `world` · `month` · `needle` · `space` · `death` · `des` · `еда` |
| phrases | `brave new world` · `welcome to the brave new world` · `new world order` · `novus ordo seclorum` · `nwo` · `order and stability` · `порядок и стабильность` · `fuck this shit` · `sht` |
| bulk lists | the 1,318-string tier-0 list (BIP39, Electrum and raw text) and the 818 further tier-1 strings (BIP39 and Electrum; raw tier 1 was not run) |
<!-- words:end -->

### Families of search, and what is NOT covered

| Family | What was tried | NOT covered |
|---|---|---|
| One unknown word (the "tight" shapes) | all three models; `black` free, `vote` pinned or freed, `moon`@1, a lower or upper numeral, `eye` as word 13 or as passphrase or free, `peace`@11, `this` or `section`@1 | a second unknown word; any pin wrong |
| Two unknown words (E3, E4, p6, p6e, p7, p7e) | subject@1 tower@3 eye@9 pyramid@11 held, food, this, real, one, order present, **any** two other words over the whole list, empty passphrase (plus MOON, moon, Moon in P and "moon" in E12) | any pin wrong; three or more unknowns (about 29 h per word in E13, 170 days for three in P) |
| Slots 9 and 11 | object-name pairs, themed and meme pairs, the Bush-speech pool (Electrum only), Latin-motto words, the dial strip's words; either slot over the whole list | both over the whole list (three unknowns: about 26 h in E13, 2 days in E12, 2 months in P) |
| Leave one out, and drops | each of food, this, real, one, order replaced by any word plus one more; one of eye, pyramid, black, tower, subject dropped and replaced (E13 and E12; in P `black`, and 9 or 11 one at a time) | two wrong words at once; model-P drops of subject, tower and the floaters at 9 and 11 |
| Moon readings | moon@1 (mod 12), @2, @12 with vote@13, moon an ordinary word; lunar, lunch, plunge, LUN at 13; month, second, hour, world and more | other spellings; non-English moon words beyond луна, месяц, lundi |
| Passphrases and extensions | the table above, the 1,318 and 818 string lists, 55 date forms | strings nobody listed; the date extensions at two unknowns in E12 (interrupted at 379 of 630 blocks); the same strings at two unknowns in P (about 84.6 billion) |
| Positions | slot 1 open with subject anywhere; subject@13; food@3 with tower loose; vote@2 or 11; swap words at 12; count pins; the mirrored dial and reversed pins | the two-unknown forms of the dial, the pins and slot 1; this@1 at two unknowns in P |
| Other lengths and tables | the 18-word and 21-word community tables, rarsn4's variants (CPU) | raw 21 or 24-word seeds (about 17 unknowns); BIP39-15 with the pins (about 170 days) |
| Derivation paths | BIP39 `m/44'/0'/0'/0/{0,1,10}`, `m/0'/0/0`, `m/0/0`; Electrum `m/0/{0,1,2}`, `m/1/0`; many more receive, change and account indices on the tight shapes | E4, p7e and p6 at 40 paths (about 3.5 h each in Electrum, about a day in P) |
| Raw text (no checksum) | the tight tiers; the twelve device words in every order; conventions; pin pairs; the clock triple and quad; tokens in the phrase | the whole-list raw two-unknown tiers; raw tier 1 |
| Community sets and rules | the void-CUDA set; date-index words; a sum-60 rule; the WELCOME matrix; May and June 12! sweeps | unfinished June runs; every thematic set nobody listed |

Not searched at all: address types other than legacy P2PKH, any wordlist other than BIP39 (and Electrum's `01` seeds), derivation
paths and passphrases outside the lists above. The full tables, with every family's exclusions, are in [TESTED.md](TESTED.md).

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
