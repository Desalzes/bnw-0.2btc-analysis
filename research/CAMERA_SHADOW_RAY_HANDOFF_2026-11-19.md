# BNW camera geometry, apparent shadows, and text-ray tests — 2026-11-19

Status: **RESEARCH TODO / NOT VERIFIED**. Requested by user for Claude and subsequent agents. Priority: **high for image-forensic work**, but **do not enqueue seed-search configurations from these ideas until independently discriminating geometry is demonstrated**.

## Context and source discipline

- Canonical image: `puzzles/btc-02-picture/artifacts/puzzle.png`, 1600×1200; SHA-256 `d0b04378f75d63997b8034ec2ef1bdd108178e4546de78237bd35abf4189a782` per repo README.
- User supplied an annotated **720×706 crop** on 2026-11-19 depicting two apparent surveillance cameras, an overlaid large black center dot, three thick black arrows, and colored dots/word labels. **The dot, arrow strokes, colored labels, and their directions are added annotations, not established original-image features**. Avoid treating them as independent puzzle clues.
- Visual estimates in *crop coordinates* (not yet registered to canonical image): camera lens centers L≈(215,153), R≈(518,180), annotation dot≈(419,327), arrowheads roughly left≈(145,351), southwest≈(228,627), south≈(523,640). Coordinates and corresponding angle estimates are rough and **must be remeasured**.
- Initial direction-to-center angles clockwise from image-up (from center dot toward targets): left camera≈310.5°, right camera≈34°, left arrow≈262°, southwest arrow≈212°, south arrow≈162°. Angles to arrowheads are a description of user's drawing, **not evidence of authored geometry**.
- In the crop, dark diagonal elements below the cameras appear to converge toward the center and lighter dark/gray strips continue downward. They may represent camera mounting brackets, Space Needle architectural supports, cast shadows, composited image fragments, or unrelated overlaps. **No classification has been established.**

## Claude assignment: complete ALL tracks, not just the favored one

### P0. Register crop and classify the camera 'shadows' (NEW)

1. Match the annotated crop to a rectangle in the untouched canonical image using several *unaltered* landmarks (camera lens centers, housing corners, text baseline, right border). Save pixel-to-pixel mapping, uncertainty, and comparison panels. Never use upscaling as original evidence.
2. In the original crop, trace separately for left and right camera: **housing major axis; lens optical-axis estimate; physical mounting/support rod; each dark diagonal line; each potential cast-shadow edge; and any underlying architectural strut**. Report endpoints, orientations, widths, contrast, occlusion order and which classifications are ambiguous.
3. Test **shadow consistency**: Do the left/right apparent shadows imply a common illumination direction? Are the lines parallel as cast shadows of common structures would require under distant lighting? Do they instead physically connect to the mounts (suggesting supports)? Is there a plausible casting surface? Account for the composited-art nature of the image: lighting rules may not hold across source layers.
4. Extend axes of (a) camera housings, (b) likely optical directions, (c) mounting rods, and (d) apparent shadow edges **separately**. Find pairwise intersection locations, angular mismatch and confidence/uncertainty. Never conflate a support rod with the camera field of view.
5. Compare convergences against **held-out** known anchors (clock hub, capstone/eye, Space Needle apex, mirrored fold axis, cipher rows) rather than selecting a hub after seeing the intersections. Record misses, not just hits.

### P1. Text-ray intersections (user's favorite; must run)

- Lock ray origins/directions using geometry **alone**, before reading text; compare rays from each lens, camera housing, support/shadow axes, and any independently-derived center.
- Predefine target text regions, including left `POLICE`/`BRUTALITY`, lower-left `TOP`/`NOT`, right Russian cipher column, the known `camera`, `black`, `tower`, `moon`, `real` and `eye` features. Use bounding polygons, glyph centers, baselines, and intersection residuals instead of subjective "looks aimed at" judgments.
- Evaluate ray directions in both forward and reverse senses where visually justified, and penalize added degrees of freedom. Compare 12/15/24-sector assignments **only after** registration. Never infer a word from an arrow annotation.
- Run negative controls: rotate locked rays ±5°/±10°/±15°, mirror rays about deliberately wrong axes, randomize origins over equivalent nearby image structures, and score matched word regions against all comparable visible text, not just desired words.
- Strong finding requires reproducible geometric hits under fixed parameters, multiple independent targets, and clear better-than-control precision. Otherwise classify speculative/negative.

### P2. Camera convergence

- Fit optical-axis estimates to the original **camera housing/lens structure**, without using the drawn annotation dot as a target.
- Compare apparent intersection(s) against the annotation dot *after* estimation and against independent original-image landmarks; show signed pixel residuals and uncertainty bands.
- Record separately if **mount rods** converge but actual camera optical axes do not. Such convergence may be structural artwork, not a cipher.

### P3. Camera mirror-axis test

- Estimate a vertical or oblique reflection axis from unaltered camera geometry (lens centers, body corners, support axes) using a preregistered least-squares correspondence.
- Assess whether reflected left/right shapes are actually homologous; the two camera housings look different, so compare lens/support anchors first and explicitly score failed shape correspondences.
- Test overlap with pre-existing capstone fold axis x≈650.96 **in canonical coordinates only**, and with known clock geometry. Do not freely translate or scale for a visual hit.

### P4. Camera-as-clock-hands test

- Use geometrically measured center(s) and camera rays as proposed dial hands; compare angular separations to existing painted clock hand angles and ~73° rotation, including mirrored alternatives.
- The original clock center per README is (472.0,940.2), radius ~183 px; existing image geometry is **not** license to choose a new dial center ad hoc.
- Report residuals for all hypotheses and a null distribution from randomized paired features. Reject if any fit needs unlocked rotation + center + mirror + slot assignment.

### P5. Five-direction / radial-word sequence test

- Ali's three arrows plus two lens bearings suggest a five-direction sketch. Because the arrows were drawn by an analyst, **do not claim five native rays exist**.
- Compare *original-image-derived* directional lines with the ongoing `eye → pyramid → tower → moon → real` radial sequence and Space Needle cone constraints; preserve prior verified and negative findings.
- Pre-register a 12- or 15-word ordering proposal only if the geometry uniquely determines an orientation and word-placement rule. Keep 21/24-slot alternatives exploratory.
- Test against a held-out clue and randomized sectors. Record number of adjustable parameters / multiple-testing exposure.

## Suggested artifacts and execution order

1. `artifacts/camera-geometry-2026-11-19/`: untouched registered crop; crop alignment overlay; annotated line classifications; intersection and text-ray overlay; threshold/contrast variants **from original pixels only**, not AI upscale.
2. `research/camera-shadow-ray-results-2026-11-19.md`: coordinates in canonical frame, method, equations, residual table, negative controls, classification and explicit pass/fail of P0–P5.
3. Run **P0 → P2 → P1 → P3 → P4 → P5**, with P1 prioritized for thoroughness. It is fine to work in parallel, but avoid reusing exploratory targets as held-out validation.
4. State clearly whether anything was committed/tested vs proposed. Do not add generated seed configs to the GPU queue unless a reproducible improvement over controls supports it.

## Existing constraints and cautions

- Repo README lists `camera` among weak guesses for slot 2; do not promote its slot certainty based on resemblance alone.
- Preserve the 2026-11-18 capstone T/A hypothesis and known 21-dot and clock-circle work; verify their exact coordinates in their own primary reports before comparing.
- Keep `TESTED.md`, verified no-match ledger and historical analyses intact. This note is a **new investigation ticket**, not a result or solution.
- No claim that the camera elements or their shadows deliberately encode directions has been established.
