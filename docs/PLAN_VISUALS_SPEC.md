# Render agent: plan visuals stage, model choice and memory (requirements locked 26 Oct 2026, updated 29 Oct 2026)

Full design for Claude Code. Build it one screen at a time with `plan-visuals-steps.md` (PV0 to
PV15); each step prompt names the sections it uses. Lives in the repo at
`docs/PLAN_VISUALS_SPEC.md`.

## 0. Locked requirements (owner Q&A, 25, 26 and 29 Oct 2026)

| # | Requirement |
|---|---|
| R1 | Input: one Revit PDF set (site plan, floor plan, elevations, colour schedule) |
| R2 | Floor plan readout, Green / Orange / Red, loop until all Green. Then the app asks "Do the elevation readout, or skip it?" |
| R3 | Elevation readout (if chosen), loop until all Green. Then "Choose plan styles" |
| R4a | Readout model chosen on the first screen ("Start the plan check") with an ⓘ explanation: Claude (default), OpenAI, or both; both = independent readings, disagreements become Orange, about double the reading cost (shown); also used for the furniture proposal |
| R4b | All plans and cutaways are drawn by code from the approved readout; no AI, no model choice |
| R4c | "Choose plan styles" comes after the readouts are all Green and replaces "Shall I build?": picking styles, outputs and formats and pressing Build is the owner's yes |
| R5 | Outputs: Sketch (V2A), Essential (V1), Showcase (V2B) 2D plans plus a 3D cutaway still per chosen style, PNG / JPG / PDF; static files for listings, no interactive 3D |
| R5a | Sketch (V2A) is mandatory on every plan job and is built first; Essential (V1, finishes plan + colour-zoned plan) and Showcase (V2B) are optional and built after it, in that order. Names and codes stay as they are |
| R5b | Render reference crops (internal, never delivered): one image per interior view, drawn by code from the V2A data with floor-finish tints, no text, a camera marker and view cone, rotated so the wall the camera faces is at the top, cropped to what the camera sees plus about 1 m |
| R5c | Photoreal enhance (optional, Showcase only): the code-drawn V2B plan is sent to the OpenAI image model with a "same layout, only improve materials and lighting" instruction; code overlays the approved walls and openings on the result and rejects it below a match threshold, keeping the code-drawn version |
| R6 | Plans are always built and approved before any render is chosen. "Plans are ready": Finish here (plans only) or Choose renders. A plans-only job can be pulled back from Completed later |
| R6a | "Exterior renders only (no plans)" stays as an option on the first screen ("Start the plan check"); it skips every plan stage and runs today's path |
| R7 | Choose renders = today's Choose views screen: exterior front day, rear (only when a deck, alfresco or pergola is drawn, as decided 14 Oct 2026), front night; each interior room; photoreal cutaway; references per view |
| R8 | Interior renders placed from the locked plan (walls, openings, fixtures, furniture) |
| R9 | "Indicative Image Only" on every output, plans and renders, every format (§11) |
| R10 | Diagonal-hatched square beside a WC = liveable-housing toilet clearance; never drawn or rendered |
| R12 | Wet-area floor tile: mid-tone contrasting with white fixtures and walls, in plans and renders, with a code contrast check (§11) |
| R13 | Photoreal images: image provider on screen, OpenAI default; MuAPI listed; MuAPI without a key stops with "add your MuAPI key in Settings" |
| R14 | Render references per view: render reference crop (R5b, default for interiors), original drawing crop, readout geometry, approved V1 / V2A / V2B plan crop. Colour-zoned images are never sent to the image model |
| R15 | Photoreal cutaway: optional render view repainting the engine cutaway still, checked against the plan |
| R16 | Project memory: flags and fixes remembered for the drawing set and reused on later regenerations and re-runs |
| R17 | Agent memory: "Remember for all projects?" on each flag; pending until the owner approves it |
| R18 | A shower not drawn is never added; asked as an Orange row; drawn or confirmed showers get a glass screen |
| R19 | Render scene prompts by Claude and/or OpenAI (each can be off). Default: both write, the designer picks one per view, only that one renders; "Use the suggested prompt for every view" button; "Render both" per view to compare (double image cost); Copy / Download prompts per view for use outside the app; drawing check by one chosen model (OpenAI default) |
| R20 | Shortcut buttons: "Accept all suggested furniture" (furniture check), "Approve all" (plan review and render review), with per-file flagging still available |
| R21 | Render quality flow unchanged: thumbnails and drafts at draft quality with the DRAFT mark; finals made locally from the approved draft (existing behaviour). "Render both" comparisons use draft quality |
| Out | Traced 2D elevation images as an output; interactive 3D (orbit, dollhouse, tour pages) in the render agent |

## 1. Pipeline order (pipeline/job.py STAGES)

```
s1_ingest → s2_sheets → s3_evidence → s4_spec
→ gate_plan_start       Start the plan check: readout model (or "Exterior renders only, no plans")
→ p1_trace              floor plan trace + overlay + readout
→ gate_plan_check       floor plan questions until all Green
→ gate_elevation_choice "Do the elevation readout, or skip it?"
→ p1e_elevations        elevation readout (skipped if chosen)
→ gate_elevation_check  elevation questions until all Green
→ gate_styles           Choose plan styles (Sketch always ticked), outputs, formats, then Build
→ p2_furnish            furniture proposal (every plan job, because Sketch is mandatory)
→ gate_furnish_check    furniture until all Green ("Accept all suggested furniture" available)
→ p3_plan_build         code draws Sketch (V2A) first, then Essential (V1), then Showcase (V2B), the
                        cutaways, and the internal render reference crops
→ p3e_enhance           optional Photoreal enhance of the Showcase plan (OpenAI image + overlay check)
→ gate_plan_review      approve / flag each file ("Approve all" available); approval locks the plan
→ gate_continue         Plans are ready: Finish here | Choose renders
→ gate_views → s5_checklist → gate_approval → s6_prompts → s7_render → s8_check → s9_review
```

Stages that do not apply are "skipped" with a reason. "Exterior renders only" skips every plan stage.
Jobs started before this change mark new stages "skipped: added after this job started".

## 2. Start the plan check and Choose plan styles

**Start the plan check** (gate_plan_start, status AWAITING_PLAN_START; right after the drawings are read):
- **Readout model** Claude (default) | OpenAI | Both, with an ⓘ: "Claude, OpenAI or both read your
  drawing and list what they find. Both: each model reads separately and any disagreement becomes an
  Orange question for you, for about double the reading cost. This does not change how the plans are
  drawn." Estimated reading cost per option shown from `pricing`: `plans.readout_estimate_tokens` (input
  and output tokens per floor-plan sheet) × the floor plan and mixed sheets found in stage 2 (at least
  1) × the model's price; Both = the sum of the two; "price not set" while a price is empty.
- **"Exterior renders only (no plans)"** option (R6a).
- Directives: `@readout_llm=claude|openai|both @plans_mode=plans|renders_only`. They pre-select the
  screen; the choice is saved to `plan/outputs_chosen.json` and written back as these directives. A job
  run from the CLI has no screen: it uses the directives and defaults to `renders_only`, so the CLI path
  is unchanged (PV1, 29 Oct 2026).

**Choose plan styles** (gate_styles, status AWAITING_STYLES; after the floor plan, and the elevations if
chosen, are all Green). This screen is the build confirmation (R4c):
- The final readout summary ("All items are Green") with a link to the full tables.
- Style cards with thumbnails (`private/knowledge/plan_styles/*.jpg`), in build order: Sketch (V2A)
  furnished line-art, ticked and locked, labelled "Always included (used for renders)"; Essential (V1)
  unfurnished finishes + colour-zoned plan, optional; Showcase (V2B) furnished photoreal top-down,
  optional, with a "Photoreal enhance" tick (R5c, image cost shown). Per style ticks: 2D plan, 3D cutaway
  still. Formats: PNG, JPG, PDF.
- Build always goes to the furniture check first (Sketch needs furniture).
- Directives: `@plans=V1,V2B @plan_views=2d,cutaway @plan_formats=png,jpg,pdf` pre-tick the screen.
- Both screens save to `plan/outputs_chosen.json`. Thumbnails are examples only, never sent to any model.
- Styles can be added later: from "Plans are ready" or a Completed job, "Add plan styles" reopens this
  screen; the lock is reused (furniture check runs first if a furnished style is new).

## 3. Floor plan trace (p1_trace)

- Vector PDF: the black wall cuts via PyMuPDF at the scale read in stage 2, else the "1:N" in the page
  text (local, free). Built in PV2 (29 Oct 2026): the page is drawn as printed without its text, near-black
  pixels thicker than a wall (`walls.min_wall_mm`) are kept and split into wall rectangles in mm; dimension
  dots, title-block marks and logos are dropped. Raster or scan, or a page with no vector walls: the
  readout model (or both) traces each wall outline vertex by vertex. The floor plan page is the sheet
  stage 2 typed floor_plan or mixed, else the page whose text names a floor plan (`floor_plan_words`).
- Rooms, openings, recesses, fixtures, clearance zones and the dwellings (with any repeat or mirror) are
  read by the readout model(s) from the page image and its text (one `plan_trace` call per model per
  floor plan page, cached in `plan/trace_<model>_page-N.json`); code converts them to mm, snaps corners
  onto wall faces (`snap_mm`), hosts each opening on its nearest wall and checks mirrors on the traced
  walls. With Both, the first model that answered builds the geometry; the other reading is kept for §4.
  A Practice run has no model reading: walls, overlay and the traced total only. Rules and limits are
  data in `private/knowledge/plan_trace.yaml`.
- Rules (from `private/knowledge/plan_output_spec.md`): trace walls, not rooms; follow every bend, jog,
  step and return; rooms may be non-rectangular and flow into each other; pockets classified by size and
  position (deep narrow along a bedroom wall = robe; small square in a wet area = shower recess; shallow
  off a hallway = linen or nib); X symbols are evidence only; every break in the black line is an
  opening (swing arc or leaf = door; dashed or parallel lines = cavity slider or arch; nothing across =
  open walkway; thin double lines in the wall = window; codes like 15-18 = height-width in hundreds of
  mm); ignore annotations, grid marks, title blocks, red revision markup.
- Diagonal-hatched square beside a WC = accessibility clearance zone, recorded, never drawn (R10).
- Showers: a shower symbol is a shower; a wet area with a bath and no shower symbol becomes an Orange
  row "bath only, or bath with shower over?" (R18). A centre floor waste is optional detail.
- Stage 2 room layouts and `house_specification.json` ROOMS are evidence; disagreements keep both and
  make the row Orange. Area check against the drawing's schedule (`areas.tolerance` 0.03; over = Red),
  read from the page text per "DWELLING n" block. Measured as schedules are: each wall belongs to the
  nearest room (exterior walls to their outer face plus `veneer_mm`, shared walls to their centre);
  unlabelled space (halls) counts to living; alfresco and porch are measured as outlined; living is the
  footprint less garage, alfresco and porch.
- Outputs, one folder per dwelling `plan/<dwelling>/`: `geometry.json` (walls in mm, rooms, openings
  with host wall and position, recesses, windows with codes, fixed fixtures, clearance zones, scale,
  method, model, area check), `overlay.png` (traced walls at 50% over the page, room outlines and names,
  clearance zones labelled "accessibility clearance (not drawn)", with the disclaimer) and `readout.json`
  (rows code writes in PV2: scale, areas, shower rule, clearance zones, same-as-another-dwelling).
  `plan/trace_summary.json` lists them. Check the plan (PV2) shows them read-only; Continue records
  `plan/trace_seen.json` and the questions come in PV3.
- Multi-dwelling: one geometry per dwelling; a dwelling whose walls match another's (repeated or
  mirrored, at least `mirror_min_match` of the walls both ways) gets `mirror_of` and an Orange
  confirmation row ("Same as DWELLING 2, repeated"); its plan is reused once confirmed (PV3).

## 4. Readout table and questions (gate_plan_check, gate_elevation_check)

- Rows: scale, each room, each connection, each recess or niche, each window, each fixed fixture, each
  clearance zone (floor plan); per face for elevations (§5). Columns: # | Item | What I read |
  Evidence | Confidence | Question for you.
- Scoring: GREEN clearly drawn, matches the overlay, one sensible reading. ORANGE partly clear; best
  guess and the alternative. RED cannot interpret; exact description and a specific question. Unsure
  between two → the lower. Overlay off by more than `overlay_tolerance_mm` (100) cannot be Green. Every
  Orange / Red row has a question; Green rows none (validated in code).
- Both models: each reads independently; code merges row by row; same reading and both Green → Green;
  any difference → Orange showing both readings ("Claude: door with swing arc; OpenAI: cavity
  slider") with a question; per-model cost in the report.
- Screen: summary "X Green, Y Orange, Z Red", overlay beside the table, row click highlights it,
  questions in batches of up to 5, Red first. Answers go to `corrections.json`, `feedback.jsonl` and
  project memory (§13), override the reading, and re-read affected rows (overlay redrawn).
- Gate in code: nothing moves on while any row is Orange or Red (API-level). Floor plan all Green →
  `plan/readout_approved.json` (geometry hash) → elevation choice.

## 5. Elevation choice and readout (gate_elevation_choice, p1e_elevations)

- After the floor plan is all Green: "Do the elevation readout, or skip it?" with a one-line ⓘ: "Reads
  each elevation and checks every window and door against the plan; used for the 3D cutaway heights and
  the exterior renders."
- Readout per face (front, rear, each side; per dwelling where they differ): storeys; roof form and
  pitch; eaves; wall materials and colours per the colour schedule; each window and door matched to its
  plan opening (size from the plan code, look from the elevation, per `drawing_reading_rules.md`) with
  head and sill heights; garage door; entry feature; differences between attached dwellings.
- Cross-checks: an elevation window with no plan opening, or a plan opening missing on its face, is
  Red. Panel and sash counts are styling and never block. NOT FOUND heights stay NOT FOUND.
- All Green → `plan/elevations_approved.json`. Skipped → exteriors use today's stage 4 and 5 facts.
- Then: Choose plan styles (§2).

## 6. Furniture (p2_furnish, gate_furnish_check)

- Runs on every plan job (Sketch is mandatory). The readout model (or both) proposes
  per room from `private/knowledge/furnishing.yaml`; code places pieces using each asset's footprint
  and "front" from `private/assets/lib/ph_asset_index.json` and the existing V2B assets, and checks door
  swings, walkways and openings against `geometry.json`.
- Furnishing list: garage cars (double = both, single = one; V2A garage empty); family wall TV, low
  unit, sofa facing the TV, coffee table, rug, plant; meals 8-seat dining set off the long window;
  kitchen 3 island stools on the living side; lounge corner sofa + armchair + coffee table + rug, sofa
  colour different from the family sofa; master king bed, side tables, lamps, rug; other bedrooms queen
  bed; WIR rails with clothes; robes with sliding-door lines; pantry shelves + bench; laundry steel tub,
  washer, dryer, cleaning shelf; bath / ensuite: bath, shower with glass screen, drain, rose (only where
  a shower is drawn or confirmed); alfresco dining for six + BBQ; entry plant.
- `plan/furniture.json` per item: room, type, host wall, position, rotation, footprint, asset id, styles,
  "visualisation choice, not in the drawings". Rows per room start Orange; clashes Red; swaps from
  same-category thumbnails. **"Accept all suggested furniture"** turns every clash-free Orange row Green
  in one click (Red clashes still need an answer). All Green → `plan/furniture_approved.json` → build.

## 7. Plan build (p3_plan_build; code only)

- `src/render_agent/plan/gen_data.py` writes `data.js` from `geometry.json` + `furniture.json` only,
  filtered per style (V1 fixed fixtures; V2A furniture without cars; V2B everything). Anything not in
  the approved readouts stops the build and adds an Orange row.
- Build order: Sketch (V2A) first, then Essential (V1) if chosen, then Showcase (V2B) if chosen; cutaways
  per chosen style after each 2D plan; render reference crops last.
- 2D V2A and V1: `private/engine/plan.js` (V2A line-art; V1 finishes + zoned with legend).
- **Render reference crops (R5b):** generated by `plan.js` from the same V2A data, one per interior view
  in the view set (each room's default camera from `view_set.yaml`; open-plan views, hallways and kitchens
  cropped to what the camera sees, not the room outline; stairs use the plan of the floor the stair
  starts on). Line-art with floor-finish tints (timber living and bedrooms, wet-area tile per §11, concrete
  garage), no labels, dimensions or codes, a camera marker with view cone, rotated so the wall the camera
  faces is at the top, plus about 1 m margin. Saved in `plan/render_refs/<view_id>.png`, rebuilt when the
  view's camera changes on Choose renders. Internal only: no disclaimer, never in Output.
- `private/engine/still.js` derived from `tour_reference.js`: scene build kept; OrbitControls, tweens,
  UI and render loop removed; `renderStill({ style, shot, widthPx })`; the two project-specific lines
  in tour_reference.js come from data.
  - shot `top` for V2B: photo tops from `assets/lib` (incl. `ph_*`), glTF models from `engine/models`
    (incl. `ph_*`), wet-area tile per §11, showers with glass screens, door leaves and swing arcs, lintels
    hidden, no shadows, white label pills.
  - shot `cutaway` per chosen style (`plan_styles.yaml`): orthographic, 58° down (55 to 60 allowed), 45°
    azimuth from the street/entry corner, walls cut at 1000 mm, windows as glazing lines on the cut,
    plain exterior faces, no roof / posts / landscaping, white background, no labels. V1 soft sun
    shadows; V2A white clay; V2B textured with 3D stand-ins.
- Local headless Chromium (Playwright; SwiftShader fallback), no network. PNG 4000 px; JPG q92 on white;
  PDF vector for V1 and V2A, image for V2B and cutaways. Deterministic: same lock → identical files.
- Every file: "Indicative Image Only" (§11), drawing number, scale bar on 2D, DRAFT mark until approved.
- Credits on V2B files and report.html (wording from the project's `assets/asset-models.md`): "3D
  models: Glam Velvet Sofa © 2021 Wayfair, CC BY 4.0; Diffuse Transmission Plant © 2024 Darmstadt
  Graphics Group GmbH, changes by Eric Chadwick, original by Rico Cilliers, CC BY 4.0; Sheen Chair and
  Glass Vase with Flowers, CC0. Via Khronos glTF Sample Assets. Furniture models by Poly Haven, CC0."
- Names `<design>_<Name>-Plan_<Code>[_zones].<ext>`, `<design>_<Name>-Cutaway_<Code>.<ext>`; folders per
  dwelling (and Common): `Plans\<Name> Plan (<Code>)\Draft` and `FINAL APPROVED`.

### 7a. Photoreal enhance (p3e_enhance, optional, R5c)

- Runs only when ticked for Showcase. Sends the approved-geometry V2B 2D image to the image provider
  (OpenAI default; `images.enhance_model` in config, e.g. gpt-image-2 or gpt-image-2.5-sunburst) with a
  fixed instruction: same walls, openings, fixtures and furniture in the same places; only improve
  materials, textures and lighting; no text; no shadows.
- Code check: the approved walls and openings are rendered as a mask at the same size and compared with
  the wall pixels detected in the result; below `plans.enhance_min_match` (proposed 0.95, tuned at PV8) the
  result is rejected and the code-drawn V2B is kept, with the reason shown. The drawing check then
  compares furniture positions. Two attempts at most; cost goes through `images/budget.py` and is shown
  before the run.
- The enhanced file sits beside the code-drawn one on Review the plans; the designer approves either or
  both. It gets the disclaimer and V2B credits.

## 8. Plan review and lock (gate_plan_review)

- Per file approve / flag (wrong wall, wrong opening, wrong fixture, wrong furniture, wrong style, wrong
  cutaway angle, other + note); **"Approve all"** approves every unflagged file. Geometry or furniture
  flags reopen the matching check with that row Orange; style or angle flags rebuild that file only.
  Versions `_v1`, `_v2`. Every flag offers "Remember for all projects?" (§13).
- All approved: `plan/plan_lock.json` (hashes of geometry, furniture, approved readouts, inputs; styles;
  cutaway settings; date; designer). FINAL APPROVED copies without DRAFT, with the disclaimer.

## 9. Plans are ready (gate_continue)

- Finish here: PLAN_COMPLETE ("Finished: plans only"); Output folder moves to `Output\Completed\`.
- Choose renders: opens Choose views (§10).
- Add plan styles: reopens Choose plan styles (§2) with the approved readouts; only new styles are built.
- Pull back: Home → Completed → "Choose renders": lock hash check (mismatch reopens the plan check with
  changed rows Orange), Output folder moved back, Choose views opens. Nothing before it re-runs or is
  paid again. A SUCCEEDED exterior-only job can be pulled back to add views.

## 10. Renders (s5 to s9 changes)

- **Choose views** (today's screen, extended): exterior front day, rear (only with a drawn deck,
  alfresco or pergola), front night; each interior room as today; photoreal cutaway per approved cutaway
  still (R15); per view a reference picker (render reference crop | drawing crop | readout geometry | approved
  plan crop, within the 3-reference limit; defaults: interiors = render reference crop (§7) + drawing
  crop; exteriors = elevation crop + drawing crop; colour-zoned images are never offered). Interiors need the plan lock (code check: NOT READY "plan not locked").
- **Approval** (today's screen, extended): materials and NOT FOUND fills as today; prompt writers Claude ☑
  OpenAI ☑ (either can be off); drawing check model (OpenAI default); image provider (OpenAI default,
  MuAPI listed; no MuAPI key → PAUSED "add your MuAPI key in Settings").
- **Prompt pick (R19):** with both writers on, each view shows both scene prompts side by side; the
  designer picks one (suggested: the last model picked for that view type); **"Use the suggested prompt
  for every view"** picks all in one click; only picks are rendered; "Render both" per view renders both
  (cost shown); "Copy prompt" and "Download prompts" per view; picks logged in `feedback.jsonl`.
- Prompts: ROOM LAYOUT gains a FURNITURE section from the lock (code-written); camera never inside a
  furniture footprint; exteriors use the elevation readout when done; V2B finishes are the interior
  default (wet-area tile wording per §11); photoreal cutaway prompt forbids moving, adding or removing
  anything.
- **Render flow unchanged (R21):** thumbnails and drafts at draft quality with DRAFT; finals made locally
  from the approved draft.
- **Review** (today's screen, extended): **"Approve all"** approves every unflagged render; per-render
  flag as today plus "Remember for all projects?"; `s8_check` adds `furniture_positions` for interiors and
  photoreal cutaways; "Render both" pairs shown side by side.
- Every render (DRAFT and FINAL APPROVED) carries "Indicative Image Only".

## 11. Shared visual rules

- **Disclaimer (R9, from the project's `claude/disclaimer-spec.md`):** exact text "Indicative Image
  Only" on every image and every PDF page. Default rotated 90° bottom-to-top on the left edge, vertically
  centred, inset about 2%; if the left edge holds key content, the clearest margin (bottom-left or
  bottom-right, horizontal). Never over a label, door, window, furniture, facade or dimension; on plans
  outside the wall footprint. Cap height about 1.5 to 2% of the short side (min 10 pt on A3/A4 PDF). White
  on a charcoal (#1F1F1F, ~60%) pill; on light backgrounds charcoal on a white pill (~85%) with a thin
  outline. Code checks presence and contrast ≥ 4.5:1 on every file and page; missing = not deliverable.
  One implementation (`output/disclaimer.py`) used by plans and renders.
- **Wet-area tile (R12):** one setting in `plan_styles.yaml` used by plans, cutaways and render prompts:
  a mid-tone grey lighter than charcoal and clearly darker than white fixtures and walls; contrast
  against white within `wet_tile_contrast_min`..`max` (proposed 2.5 to 5.0, tuned at PV7). Render prompts:
  "mid-grey large-format floor tile that contrasts with the white fixtures".
- Plain white 3D background; no landscaping, roof or standalone posts in plan stills.

## 12. Configuration and keys

- `llm.choices` already has `gpt` (openai, gpt-5.6-sol) and `sonnet` (anthropic, claude-sonnet-5-5).
  New: `plans.readout_llm_default: sonnet`,
  `renders.prompt_writers_default: [sonnet, gpt]`, `renders.check_llm_default: gpt`,
  `images.offered_providers: [openai, muapi]`. Model IDs only in config.
  `plans.readout_estimate_tokens: {input: 30000, output: 6000}` per floor-plan sheet, for the cost on
  Start the plan check (tuned at PV2 / PV15).
- `pricing` entries for gpt-5.6-sol, claude-sonnet-5-5, gpt-image-2 (owner fills the values).
- New: `images.enhance_model` (default gpt-image-2), `plans.enhance_min_match` (0.95),
  `plans.render_ref_margin_m` (1.0).
- A chosen provider without a valid key → PAUSED naming the provider (replaces the rule that hides
  providers without a key; SPEC §8 / §10 update).

## 13. Memory (R16, R17)

- **Project memory** (working root, `memory/projects/<project key>.json`). Key: drawing number from the
  title block plus dwelling; a new revision of the same drawing number links after the designer confirms
  "same project, new revision". Holds readout answers, flags and their fixes. Shown on the job as
  "Remembered for this project" (items removable); quoted as a labelled data block into later readouts,
  prompts and regenerations for that project.
- **Agent memory** (working root, `memory/agent_rules.json`). "Remember for all projects?" on every flag
  and answer → pending list on Help → Agent memory; the owner approves, edits or rejects; only approved
  rules are quoted, as a labelled data block, into every job. Seeded as approved (owner, 26 Oct 2026):
  1. Diagonal-hatched square beside a WC = liveable-housing toilet clearance; never draw or render it.
  2. A floor waste in the centre of an ensuite or bathroom may be shown or left out.
  3. Every shower that is drawn or confirmed gets a frameless glass screen.
- Memory is data, never instructions: it cannot change directives, limits, providers or drawing facts and
  never adds architecture. Both memories are included in Export feedback; promoting an approved rule into
  `private/knowledge/` stays a developer step with the regression.

## 14. Human decision points

| # | Screen | Decision | When |
|---|---|---|---|
| 1 | Home | Pick the PDF, Start | Always |
| 2 | Start the plan check | Readout model (or Exterior renders only) | Always |
| 3 | Check the plan: floor | Answer question batches | Only if anything is not Green |
| 4 | Elevation choice | Do the elevation readout or skip | Always on the plan path |
| 5 | Check the plan: elevations | Answer question batches | Only if not skipped and anything is not Green |
| 5a | Choose plan styles | Optional Essential / Showcase (Sketch always on), Photoreal enhance, 2D/cutaway, formats, Build | Always on the plan path |
| 6 | Check the furniture | Confirm, swap, or "Accept all suggested furniture" | Always on the plan path |
| 7 | Review the plans | Approve / flag, or "Approve all" | Always on the plan path |
| 8 | Plans are ready | Finish here or Choose renders | Always on the plan path |
| 9 | Choose renders | Views, photoreal cutaway, references | Only if continuing |
| 10 | Approval | Materials, NOT FOUND fills, prompt writers, prompt picks ("Use the suggested prompt for every view"), image provider | Only if continuing |
| 11 | Review renders | Approve / flag, or "Approve all" | Only if continuing |
| 12 | Remember for all projects? | Tick on a flag | Optional |
| 13 | Agent memory | Approve / reject pending rules | Optional, owner |

Best case (everything Green first time, shortcuts used): plans only, elevation skipped = 7 actions
(1, 2, 4, 5a, 6, 7, 8); plans then renders = 10. Question batches and
flag rounds come on top.

## 15. Error table rows (SPEC §9), each with a fault-injection test

- No vector walls on a vector page → raster trace; said on the check screen.
- Area off by more than tolerance → Red row.
- Both-model readout: one model fails → its rows "not read", the other's kept as Orange; Retry re-runs only
  the failed model.
- Build finds an item not in the readout → stage stops, Orange row added.
- Headless browser missing or no WebGL → plain error with the fix; V1/V2A 2D still build.
- Disclaimer missing or contrast below 4.5:1 → file not deliverable, rebuilt.
- Wet tile outside the contrast range → config error named by `doctor`.
- Lock hash mismatch on pull-back → plan check reopens with changed rows Orange.
- Move back from Completed fails → folder left in place, plain warning, job not continued.
- Interior view without a lock → NOT READY "plan not locked".
- Chosen provider (Claude, OpenAI or MuAPI) without a valid key → PAUSED naming the provider.
- Memory file unreadable → job continues without it, warning on the job.
- Photoreal enhance below the match threshold twice → code-drawn Showcase kept, reason shown, no further
  paid attempts.
- Render reference crop missing for an interior view → rebuilt from the lock before s6; if that fails the
  view falls back to the drawing crop with the reason in prompts/index.json.

## 16. Tests

- FakeLLM + mock images end to end through the API: plans-only finish and pull-back (stage files
  byte-identical); exterior-renders-only path unchanged apart from the disclaimer; both-model readout with a
  seeded disagreement → Orange; elevation choice do / skip; Build refused until a style is ticked; Add plan
styles later reuses the lock; nothing moves on while any
  row is Orange/Red (API level); Accept all furniture leaves Red clashes; Approve all leaves flagged files;
  prompt pick renders only picks; "Use the suggested prompt" picks every view; "Render both" renders two;
  MuAPI without key pauses; disclaimer present with contrast ≥ 4.5:1 on every file and PDF page; wet tile
  contrast check; project memory reused in the next regeneration; agent rule used only after approval;
  Sketch always built first and furniture always checked; render reference crops have no text, a camera
  marker and the facing wall at the top; Photoreal enhance rejected below the threshold keeps the code-drawn
  file; colour-zoned images never reach the image model.
- Determinism: same lock → identical PNGs. `still.js` has no OrbitControls, animation loop or pointer
  handlers. `check_no_fixture_refs` covers `private/engine/` (except `*_reference.*`) and new knowledge files.
