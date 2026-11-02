# PV3 Fix round: trace parity with the Claude project

Separate fix round for PV3 (written 29 Oct 2026). Run it in Claude Code **plan mode** (Shift+Tab until
it shows "plan mode") before writing `PV3 approved` in `docs/PLAN_VISUALS_LOG.md`.

**Outcome:** the agent and the "2D 3D render marketing" Claude project read a floor plan the same way
and produce the same readout table: walls traced from the drawing, an overlay check against the drawing,
one scored table (# | Item | What I read | Evidence | Confidence | Question for you), questions in
batches of 5 (Red first), and nothing built until every row is Green. Geometry and classification move
from AI reading to deterministic Python wherever the drawing allows it; the AI model becomes a second
reader whose disagreements turn rows Orange. Both sides then run the same tracer code, so the same PDF
gives (almost) the same table.

**You approve when:** on fixture_03 (the SATL-0005 drawing the project built from) the code-only table
matches the project's approved geometry (walls, rooms, openings, window codes, robes) within 100 mm,
the table looks like the project's, and the final all-Green table is shown before moving on.

**Gaps this round closes (found 29 Oct 2026):**
1. Scanned pages: model-traced wall outlines are collapsed to bounding boxes (`_rect` in
   `p1_trace.py`), so L-shaped and stepped walls become solid blocks.
2. Walls are never checked against the drawing; rooms and openings are only checked against the traced
   walls, so on a scan the model's walls check the model's own rooms.
3. Recess depth bands in `plan_trace.yaml` only reach the prompt; code never measures pocket depth.
4. Rooms, openings and windows are geometry from the model (page fractions), not from the drawing.
5. All Green on the first reading skips the table; the project always shows the final table.
6. The table carries extra row kinds (fixtures, areas, shower, clearance, same-as) mixed into the
   project's row order.

```
SHARED RULES (PV3 Fix round: trace parity):
- Read CLAUDE.md, docs/PLAN_VISUALS_SPEC.md §0, §3, §4, §14, docs/PLAN_VISUALS_LOG.md, this file, the
  project rules in private/knowledge/plan_output_spec.md and plan_trace.yaml, and the reference engine
  private/engine/gen_data_reference.py (the approved SATL-0005 geometry from the Claude project).
- Gate: if docs/PLAN_VISUALS_LOG.md does not contain "PV2 approved", stop and tell me. If it already
  contains "PV3 approved", stop and ask before changing anything.
- Plan mode: propose the change list first; wait for my approval before writing code.
- Change only the trace, overlay check, readout and gate (p1_trace, plan/*, plan_check, the PV2/PV3
  screens). Later screens keep their "coming in PV<n>" placeholders.
- Never run git commands that change state. No paid API calls (FakeLLM and mock images only) unless I
  approve the estimated cost in this session.
- Rules are data: every threshold, word list, label and question template goes in
  private/knowledge/plan_trace.yaml, never in Python. No fixture leakage (check_no_fixture_refs green):
  SATL-0005 values live only under private/fixtures/fixture_03/.
- Model IDs only in config.yaml. All LLM calls through llm.call_json. Atomic writes, plain-English
  error.json, pathlib, bash and PowerShell commands in docs. "Indicative Image Only" on every image.
- Finish with: files changed grouped "public repo" / "private submodule (private/)", tests and results,
  the parity report for fixture_03, this round's BROWSER TEST added to docs/PLAN_VISUALS_LOG.md under
  "## PV3" as "Fix round: trace parity" with blank Tested/Result lines, and SPEC.md sections touched.
  Then stop and wait for "PV3 approved".

STEP PV3 FIX ROUND: trace parity. Shared rules above apply.

PRINCIPLE
Code reads the geometry from the drawing; the model confirms and names. A row is Green only when code
and the model(s) agree and it passes the overlay check against the DRAWING (not against our own trace),
or when the designer confirmed it. Same rules, same table, same gate as the Claude project.

A. WALLS (vector and scan use the same code)
1. Vector page: keep vector_walls.extract (raster of the page without text, near-black, opening
   filter). Also build each connected wall group's OUTLINE as an orthogonal polygon (contour of the
   wall mask, simplified to axis-aligned edges, every jog and return kept as a vertex) and store it in
   geometry.json as `wall_outlines` next to the rectangles.
2. Scan or image page: run the SAME mask pipeline on the page image (threshold `walls.dark_below`,
   opening filter `walls.min_wall_mm` at the scale read from text, the scale bar or the model). The
   model's traced wall outlines become a cross-check, not the geometry. If the model's walls are used
   (no usable mask), keep them as polygons, rasterised into the wall mask; never reduce an outline to
   its bounding box (removes `_rect` from p1_trace).
3. Scale row: from the "1:N" text and checked against one dimension string on the plan (code reads a
   dimension's value and its extension-line length); disagreement > 2% -> Orange.

B. OVERLAY CHECK AGAINST THE DRAWING (Step 2 of the project)
1. Draw the traced walls at 50% over the page (as now).
2. Code compares the traced wall mask with the drawing's own dark mask (before the opening filter):
   any blob of drawing wall-band not covered by the trace, or traced wall not on the drawing, larger
   than `readout.overlay_tolerance_mm` (100) in any direction becomes a WALL row (Orange; Red if it
   changes room enclosure), with its box as target_mm and a question ("The drawing shows a wall band
   here that was not traced. Is it a wall?").
3. Openings, rooms and recesses are measured against the drawing mask as well as the trace; > 100 mm
   off either cannot be Green.

C. OPENINGS BY CODE
1. Every break in the wall line: vector_walls.closures (facing free wall ends, gap up to
   `openings.max_gap_mm`; garage doors up to `areas.footprint_max_gap_mm`). Each gap is one opening
   with its host wall, width and position.
2. Classify from the vector drawing inside and beside the gap (pymupdf page.get_drawings(), text
   removed; scans: line and arc detection on the page image), rules in plan_trace.yaml `openings`:
   - arc whose radius is about the gap width, centred on a gap end -> hinged door (hinge end and swing
     side from the arc centre and sweep); leaf line confirms;
   - dashed line, or two parallel lines along the wall across the gap, or "csd" text nearby ->
     cavity slider (arch when no leaf and a dashed head line);
   - two or three thin parallel lines inside the wall thickness across the gap -> window;
   - evenly spaced lines across a wide exterior gap, or "panel lift" text -> garage door;
   - nothing across the gap -> open walkway.
   Any gap that matches none, or more than one, is Orange with both candidates as the alternative.
3. Window codes: the nearest text matching `openings.code_pattern` (e.g. 15-18, 21-09 awn) within
   `openings.code_search_mm` on the outside of the window; two candidates or none -> Orange.
4. Connections are the door, slider, arch and walkway openings between two rooms, named
   "<room A> -> <room B>".

D. ROOMS BY CODE
1. Close every opening gap (doors, sliders, windows, walkways, garage door) on the wall mask; each
   enclosed free-space region is a space. Its outline is the region contour, orthogonal, corners on the
   wall faces (no snapping needed: they are the faces).
2. Name each region from the room-name text inside it (page words; labels grouped by line). A region
   with several labels is one room named as labelled (e.g. "Family, meals & kitchen"); a region with no
   label is Orange ("Unlabelled space between X and Y: hall, or part of X?"). Outdoor spaces (alfresco,
   porch) from the slab line or posts and the roof line drawn, as now.
3. Room dimensions (W x D) from the size text in the label if drawn, else from the outline; area from
   the outline (the area check stays as built).

E. RECESSES BY CODE (deduced from the wall shape, X symbols are evidence only)
1. Walk each room outline: a pocket is an inset of the outline with an opening width and a depth
   (distance from the room's main wall line). Classify with `recesses` in plan_trace.yaml by depth AND
   position: robe (450-750 deep, along a bedroom wall), shower recess (800-1400, square, in a wet area),
   linen (300-650, off a hall), nib (< 300). An X symbol (two crossing diagonals) inside the pocket is
   supporting evidence and is quoted in Evidence.
2. Depth outside every band, or a band that does not fit the room type -> Orange with the nearest two
   kinds as reading and alternative.

F. THE MODEL AS SECOND READER
1. plan_trace prompt: give the model the code's items (ids, kinds, positions as page fractions) as a
   quoted data block and the page image; it returns per item: agree / different reading / not drawn,
   plus anything code missed (rooms, openings, fixtures, clearance zones), with evidence.
2. Scoring per row (data in `readout.scoring`): code clear + every model agrees -> Green; code clear,
   a model differs -> Orange "Code: cavity slider; Claude: door with swing arc"; code ambiguous, model
   decides -> Orange with the model's reading as best guess; an item only the model found -> Orange
   ("Only Claude found ...; is it on the drawing?"); code and models cannot read it -> Red with an
   exact description (location, line type, shape). Unsure between two -> the lower.
3. Practice run: code-only reading (no model) with Practice canned readings as the second reader when
   present, so a Practice run of fixture_03 shows the full table at no cost.

G. READOUT TABLE IDENTICAL TO THE PROJECT
1. Main table rows in the project's order and only these kinds: scale, each room, each connection,
   each recess or niche, each window, then any wall rows from the overlay check. Columns exactly:
   # | Item | What I read | Evidence | Confidence | Question for you (plus one column per model only in
   Both mode).
2. Item names and "What I read" / Evidence wording from templates in plan_trace.yaml `readout.templates`,
   written like the project: Item "Bed 3", "Bed 3 -> Hall", "Robe in Bed 3", "Window, Bed 3 top wall";
   What I read "Door with swing arc, hinge at the left end, opens into Bed 3"; Evidence "Swing arc and
   leaf line across a 950 mm gap in the hall wall". Confidence shown as 🟢 Green / 🟠 Orange / 🔴 Red.
3. Summary line above the table exactly "X Green, Y Orange, Z Red".
4. Extra checks the project does not list (fixtures, area check, shower rule, clearance zones,
   same-as-another-dwelling) move to a second table "Other checks" below, same columns, own summary
   line. Both tables gate.
5. Export: plan/<dwelling>/readout.md, the same table as markdown, so it can be compared side by side
   with the project's table.

H. GATE (Step 4 and the Gate of the project)
1. Questions in batches of up to 5, Red first, then Orange, across both tables. Answers override the
   reading; geometry changes re-run the overlay check and redraw the overlay; the updated table shows
   "was: ..." on changed rows (as built).
2. A row is Green only when the designer confirmed it, or it passes the overlay check and every reader
   agrees.
3. When every row is Green (including first time), the screen shows the final tables with "All items
   are Green." and one button "Continue"; only that press writes plan/readout_approved.json. Choose plan
   styles + Build stays the yes to build (R4c). This changes SPEC §14 point 3 from "only if anything is
   not Green" to "always": update §4 and §14.
4. API refuses to continue while any row in either table is Orange or Red (as built). Anything that
   turns up later in the build stops the build and is added as an Orange row (§7, unchanged).

I. SHARED TRACER FOR THE CLAUDE PROJECT
1. scripts/make_trace_kit.py bundles the trace code (A to E plus the table writer, no app, no model
   calls; pymupdf + Pillow only) into one file private/engine/trace_kit.py, generated from src so there
   is one source. Usage:
   bash:       python private/engine/trace_kit.py plan.pdf --page 2 --out trace/
   PowerShell: python private\engine\trace_kit.py plan.pdf --page 2 --out trace\
   Output: walls.json (rects + wall_outlines, mm), geometry.json, overlay.png, readout.md (code-only
   reading, same templates). A test checks the bundle is up to date.
2. Do NOT upload anything to the Claude project; I will upload trace_kit.py and update the project
   instructions so the project runs the same code and then reads as second reader.

J. PARITY TEST (the exit check)
1. private/fixtures/fixture_03/plan_answer_key.yaml, drafted by a script from
   private/engine/gen_data_reference.py (walls, ROOMS, OPEN incl. codes and kinds, robe fixtures as
   recesses) and shown to me for confirmation before it is used.
2. tests/integration/test_trace_parity.py (Practice, no cost): align the code trace to the key by the
   best wall-to-wall offset, then require every key wall within 100 mm, every room matched by name with
   corners within 100 mm, every opening matched by kind, host wall and position within 100 mm, every
   window code exact, every robe found; report extra items (allowed only as Orange rows).
3. scripts/parity_report.py writes docs/parity/fixture_03.md: matched / missing / extra per kind and
   both readout tables side by side. Target: 100% of key rooms, openings, windows and robes matched.
4. fixture_01 (three dwellings) still traces, with one table per dwelling and the same-as rows.

BROWSER TEST:
1. Practice run fixture_03, Claude: overlay shows traced walls on the wall bands; robe pockets and the
   shower recess follow the jogs; any wall row from the overlay check has a highlight box.
2. Table columns and order match the project (scale, rooms, connections, recesses, windows); summary
   line "X Green, Y Orange, Z Red"; "Other checks" table below.
3. Click an opening row: Evidence describes the drawing (arc, dashed line, double lines, nothing).
4. Answer batches (Red first, up to 5) until all Green: the final tables show with "All items are
   Green." and Continue; nothing moves on before Continue.
5. Readout model Both: the seeded disagreement shows "Code: ... ; Claude: ... ; OpenAI: ..." as Orange.
6. Open docs/parity/fixture_03.md: every key room, opening, window and robe matched.
7. Practice run fixture_01: one table per dwelling, same-as rows, no errors.
```
