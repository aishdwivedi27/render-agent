# Render agent: wall placement and image quality fix

Claude Code prompt. Run it in **plan mode** (Shift+Tab until the mode shows "plan mode"): Claude Code
reads and proposes only; nothing changes until you approve the plan. Commit the results yourself,
separately for the public repo and the private submodule.

Scope (agreed 20 Oct 2026): phase 1 only. A = correct walls and placement in interior renders.
B = image quality (1920x1080 delivery). Video and vertical formats are out of scope.

---

```
Wall-placement and image-quality fix (phase 1 only). Never run git commands that change state;
I do all git work myself. No paid API calls until I approve them in this session.

Read CLAUDE.md, docs/SPEC.md, private/prompts/sheet_analysis.md, private/prompts/render_prompts.md,
private/prompts/render_rules.md, private/prompts/drawing_check.md,
private/knowledge/drawing_reading_rules.md, private/knowledge/view_set.yaml,
schemas/sheet_analysis.json, schemas/house_spec.json, schemas/drawing_check.json,
src/render_agent/pipeline/stages/s2_sheets.py, s4_spec.py, s6_prompts.py, s7_render.py, s8_check.py,
config.example.yaml, and the most recent real job in jobs/ (its sheets/, house_specification.json,
prompts/ and checks/).

PROBLEM
Interior renders put windows, doors, robes and fixtures on the wrong walls. Root causes found in
review:
1. Sheet analysis records which rooms connect, never which wall an opening or fixture is on.
2. Fixtures drawn only as symbols (cooktop, sink, island, basin, WC, shower, bath, robe, washing
   machine) are not extracted; only text labels such as "F" and "DW PROV" survive.
3. house_spec.json FLOOR_PLAN holds free-text strings, so any layout would be flattened.
4. s6_prompts calls the render-prompt LLM with images=[]; it writes interior scenes blind.
5. The image model receives the whole floor plan and the site plan, not the room.
6. Image prompts are cluttered with size codes (870, 2100 x 900, 15-15) the image model cannot use.
7. The drawing check runs exterior checks (roof, storeys) on interiors and has no wall or fixture check.

PRINCIPLE
Renders are illustrations as near to the plan as possible. Sizes and opening codes do not matter.
What matters is that every opening and fixture is on the RIGHT WALL and in roughly the RIGHT PLACE
along it (left end, centre, right end, corner, beside X). Compass directions are secondary.

PART A: CORRECT WALLS AND PLACEMENT
A1. North arrow. In sheet analysis, read the north arrow visually on every sheet that shows one
    (it is usually a graphic, absent from the text layer). Store north_angle_deg per sheet
    (angle of north measured clockwise from page-up) or "not shown". Use it only for the report,
    sun direction in lighting, and checking elevations named by compass direction. Never use it
    as the primary wall label: on rotated sheets compass snapping is ambiguous.
A2. Room layouts in the plan's own frame. For every room on a floor plan, record:
    room name, dwelling, room bbox (normalised, same convention as view bboxes), and for each wall
    (top, bottom, left, right as drawn on the sheet; extra entries for angled or L-shaped rooms)
    the openings and fixtures on it, in order along the wall. Each item: kind (window, door,
    opening, garage door, cooktop, sink, island, fridge, basin, WC, shower, bath, robe, washing
    machine, other), what it leads to (for doors and openings), position along the wall
    (left_end | left_of_centre | centre | right_of_centre | right_end | corner), and an optional
    neighbour ("beside the window"). Islands and freestanding items get position "island" with the
    wall they face. Read fixtures from drawing symbols, not only text. Unclear items go to
    uncertainties; never guess.
A3. Schemas. Add the per-room structure to schemas/sheet_analysis.json and a matching ROOMS
    section to schemas/house_spec.json (strict mode: all properties required,
    additionalProperties false). Keep the existing FLOOR_PLAN fields so nothing downstream breaks.
    Update evidence/spec prompts so room layouts are carried through unchanged, contradictions kept
    and flagged as today.
A4. Camera choice and camera-relative layout, in code. Add camera rules per interior view to
    private/knowledge/view_set.yaml (data, not Python), for example: main bedroom = stand by the
    entry door, face the window wall; kitchen = stand in the living area, face the cooktop run.
    In s6_prompts, pick the camera wall from those rules and the room layout, then build a
    ROOM LAYOUT block in viewer terms: "Facing you", "Left wall", "Right wall",
    "Behind you (not in view)", each listing items with their position. This is the interior
    counterpart of the existing facade left-to-right block. When a room has no usable layout,
    fall back to today's behaviour and record it in the report.
A5. Room crops. In s6, crop each interior's room from the floor plan using the room bbox plus a
    margin (so connecting rooms show), and pass that crop to the image model instead of the whole
    floor plan and the site plan. Keep the whole-plan fallback when the bbox is invalid.
A6. Scene writer sees the room. Pass the room crops to the render-prompt LLM call (today
    images=[]), or let code write the interior layout entirely; propose which and why.
A7. No size codes in image prompts. Strip opening codes and dimensions from the scene text,
    must_preserve lines and layout block that go to the image model. Keep them in the spec and
    report for traceability. Update render_prompts.md so the LLM describes placement, not sizes.
A8. Interior drawing check. For interior views, replace roof_form and storeys with
    opening_walls and fixture_positions, and pass the ROOM LAYOUT block into the check prompt.
    Fail only when an opening or fixture is on the wrong wall, at the opposite end of the right
    wall, missing, or extra. Sizes and small shifts along a wall pass. Exterior checks unchanged.
A9. Answer keys. Extend the answer-key format with room layouts for the main bedroom, kitchen and
    ensuite (wall + position per item), add them to fixture_01's answer key marked "unverified",
    and extend grade_extraction to score wall placement. I verify the values.

PART B: IMAGE QUALITY
B1. Delivery size 1920x1080 landscape for every approved final render. Generate at the largest
    native 16:9 size gpt-image-2 supports and scale down with a high-quality resampler; no AI
    upscaler. Check the allowed sizes in OpenAI's documentation (a third-party source says the
    maximum is about 2048 px wide; do not rely on it) and put the generation size and delivery
    size in config, not code. This replaces images.openai.size: 1536x1024 (3:2).
B2. Drafts stay as generated. Only approved finals get the 1920x1080 delivery file: lossless PNG,
    sRGB, no DRAFT overlay.

TESTS AND CHECKS
- Unit tests with FakeLLM: room-layout schema round trip; camera-relative block for a hand-built
  room (window centred on the far wall, robe opening left wall far end, ensuite door behind right
  corner); a room with no layout falls back; size codes absent from the assembled image prompt;
  interior check uses opening_walls and fixture_positions; 1920x1080 scaling from a 16:9 source.
- check_no_fixture_refs stays green: every rule and prompt is generic; no project names, addresses,
  lots or room-specific examples taken from a fixture.
- Then propose ONE paid LLM-only run (extraction and prompt stages, no images) on fixture_01 and on
  the most recent real job's PDF, with the estimated cost, and wait for my OK. That job is the
  BASELINE (before) run for interior and exterior output: never resume, re-run or overwrite its
  job folder. Start a NEW job from a copy of its input PDF, with the same models, and keep the old
  job folder untouched for comparison. Show the ROOM LAYOUT blocks for the main bedroom, kitchen
  and ensuite next to the floor-plan crop so I can judge them by eye.
- After any paid image run, compare the new job with the baseline job side by side: for each
  render that exists in both, the drawing-check result and image_match, which openings or fixtures
  moved to the right wall, and anything that got worse. Report only; change nothing.
- Only after I approve the layouts, propose a small paid image run (main bedroom, kitchen,
  ensuite) with cost, and wait for my OK.

FINISH
List every docs/SPEC.md section this changes for the spec sync (expect §6 pipeline and prompt
assembly, §6 drawing check, §10 configuration) and ask me to confirm the live page matches.
Log the change in docs/REFINEMENT_LOG.md (date, problem, root cause, fix, files changed).
List changed files grouped as "public repo" and "private submodule (private/)". Stop.
If anything here contradicts the spec, list it and ask me one question instead of guessing.
```
