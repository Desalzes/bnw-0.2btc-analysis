# Independent review of the btc-02 GPU engine (2026-09-29, Fable session)

Scope: the engine built on 2026-09-28/29 (`harness/gpu_setperm.py`, `sp_*.py`, `kernels/sp_*.cl`) and its one real
run (E4). Everything here used DUMMY targets; the prize address was never loaded and nothing was searched.
No file under git was changed. Run everything from `puzzles/btc-02-picture/harness/` with

    PYX="uv run --offline --no-project --python 3.12 --with coincurve --with numpy --with pyopencl --with bip-utils --with ecdsa python"

| Check | Script | Result |
|---|---|---|
| Engine suite | `gpu_setperm_test.py` | 302 tests OK, 55.6 s (log: `suite_full.log`) |
| E4 checkpoint signature vs sources in git | `indep_check.py` | SIG 23c1929f9c49c51e == signature of HEAD sources |
| E4 checkpoint arithmetic | `indep_check.py` | 83/83 blocks, tested == space, valid 330,317,316 (z +0.88), per-block z in [-2.21, +2.49] |
| Index space == the .conf's candidate set | `indep_templates.py` | 62 templates, ~1,500 indices each inverted by an independent encoder; 4 `t21_*` unsupported |
| Oracle with no shared code | `oracle.py` | anchored to published vectors; equals the engine's CPU reference on 480 hash160s |
| K1 survivor set, real E4 space | `indep_gpu.py templates/e4_pins_only.conf 32 20260929` | 134,217,728 candidates: 524,332 survivors, identical to the CPU set |
| Pipeline finds oracle targets, E4 | same | 40 of 40, all 8 (path, key form) combinations, none unexpected |
| Same on 6 more templates | `indep_gpu2.py CONF WIDTH SEED` | p1, q8_l6_p1e, p1n, e13a, q14_r3_e13, e1_pp: sets identical, 60 of 60 each |
| Fault injection (scratch copies) | `run_mut.py CONF 8` | 9 of 10 effective faults end VOID; 1 survives (below). An 11th was a no-op on these configs (my design error) and is not counted |

Fault that survived, as README residual risk (b2) predicts: the K3 hit kernel dropping hits at target slot 1 for
threads with `gid % 64 == 7`. The self-test scans 1-wide windows and the window audit reads the dump kernel, so only
a block's single plant can see it (1 in 64 per even block).

Doc inaccuracy found: README says a refused run never touches the GPU. A real (non dry-run) run measures its rate
on the GPU, with a dummy target, BEFORE the cost guard decides. The prize is not loaded at that point.

## Fixes made the same day (operator: "yes fix them")

Committed on master (engine e10cda8, records 02e0793): `harness/gpu_setperm.py`, `harness/README.md`, `harness/sp_test_run.py`,
`HANDOFF.md`, `STATE.md`, the spec's "As built" addendum.

| Finding | Fix | Evidence |
|---|---|---|
| Sign-off only by convention | `--signoff TEXT` required for every real run; refused (exit 3) before GPU or disk; `_search` checks again | `SignoffNoGpu` (7 tests) |
| One plant per block | 64 plants per block (`PLANTS_PER_BLOCK`) | `PlantsPerBlockNoGpu`, `ManyPlantsInRunNoGpu`, `ManyPlantsGpu` |
| Slot-confined fault could lose the prize | prize at two K3 slots (`PRIZE_COPIES`), slots move with block and plant seed; slot reach in the self-test | `PrizeSlotNoGpu`, `SlotReachNoGpu`, real-kernel test in `ManyPlantsGpu` |
| README wrong about refused runs | corrected; behaviour pinned by a test | `ReviewDocsNoGpu`, `SignoffNoGpu` |

Suite: 336 tests OK with the GPU (80.6 s), with `SP_NO_GPU=1` and without pyopencl (108 skipped each).

Fault injection against the fixed engine (p1 template, 8 blocks, 3 plant seeds each, dummy prize):

| Injected K3 fault | Result |
|---|---|
| none | NO MATCH, 512 plants found, 3 of 3 |
| loses 1 hit in 64 by thread place, any slot | VOID 3 of 3 |
| loses 1 hit in 1,024 by thread place, any slot | VOID 2 of 3 (expected about 0.39 per run) |
| one slot dead (slot 40) | VOID 3 of 3, at the slot reach, before block 0 |
| loses 1 hit in 64 by thread place, at slot 1 only | NO MATCH 3 of 3: still rarely seen, but it can no longer lose the prize |
