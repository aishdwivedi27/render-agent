# Plan visuals approval log

One section per step (see `docs/plan-visuals-steps.md`). Write `PV<n> approved <YYYY-MM-DD>` under a
step once its browser test passes; the next step checks for that line before it starts.

## PV0
Settings and Help (foundations).

BROWSER TEST:
1. Settings shows the defaults above (Readout model Claude; Render prompts Claude + OpenAI; Drawing
   check OpenAI; Images OpenAI, MuAPI listed).
2. Help lists the new checks (green or with a fix text); pricing "not set" until you fill it.
3. Home: existing jobs unchanged. Practice run fixture_03 reaches Choose views as before.
4. Help -> Copy diagnostics: new stages "skipped: not enabled yet".

Start the app:
- PowerShell: `.\.venv\Scripts\Activate.ps1` then `python -m render_agent app --browser`
- bash: `source .venv/bin/activate` then `python -m render_agent app --browser`

Tested: 2026-10-29
Result: All check passed
PV0 approved 2026-10-28

## PV1
Start the plan check.

BROWSER TEST:
1. Practice run fixture_03: after Read the drawings, "Start the plan check" appears with Claude selected.
2. Hover/click ⓘ: the explanation appears. Pick Both: the extra cost is shown.
3. Continue: the job card shows "Readout: Claude + OpenAI".
4. New Practice run, pick "Exterior renders only (no plans)": Choose views opens as before.
5. instructions.txt "@readout_llm=openai": the screen opens with OpenAI selected.

Start the app:
- PowerShell: `.\.venv\Scripts\Activate.ps1` then `python -m render_agent app --browser`
- bash: `source .venv/bin/activate` then `python -m render_agent app --browser`

Tested:
Result:
PV1 approved 2026-10-29

## PV2
Check the plan: overlay and area check.

BROWSER TEST:
1. Practice run fixture_03 (Showcase): overlay on the drawing; robe pockets and shower recess follow
   the wall jogs.
2. The square beside the ensuite WC is labelled "accessibility clearance (not drawn)".
3. Area table: living, garage, alfresco, porch with differences; over 3% in red.
4. fixture_01: one overlay per dwelling or "same as Dwelling 2, mirrored".

Note: a Practice run makes no model reading, so it shows the traced walls, the overlay and the traced
total only. Steps 2, 3 (garage, alfresco, porch, living) and 4 (the dwellings) need a real run: Start
the plan check shows the estimated reading cost for each model choice. In tests these steps are covered
by canned readings (`private/fixtures/fixture_0{1,3}/canned/plan_trace.json`).

Start the app:
- PowerShell: `.\.venv\Scripts\Activate.ps1` then `python -m render_agent app --browser`
- bash: `source .venv/bin/activate` then `python -m render_agent app --browser`

Tested: 2026-10-29
Result: all pass
PV2 approved 2026-10-29

## PV3
Check the plan: floor plan questions.

BROWSER TEST:
1. Practice run, Readout model Both: summary line shows a mix; the seeded row shows "Claude: ... /
   OpenAI: ..." as Orange.
2. Click a Red row: highlighted on the overlay; first batch starts with Red.
3. Answer a batch: rows update and show what changed.
4. Answer until all Green: the job moves on automatically.
5. Repeat with Claude only: no per-model column.

Note: use fixture_03 (`private/fixtures/fixture_03/input/`). A Practice run of that PDF reads the sample
readings in `fixture_03/canned/` (Claude: MUD Orange, ENS WC symbol Red; OpenAI: the front door read as a
cavity slider, the seeded disagreement). The other Orange rooms are where the sample outlines sit more
than 100 mm off the traced walls. Free-text answers in a Practice run are "read again" from
`canned/plan_reread.json`. The job then waits on "Elevation choice (coming in PV4)".

Start the app:
- PowerShell: `.\.venv\Scripts\Activate.ps1` then `python -m render_agent app --browser`
- bash: `source .venv/bin/activate` then `python -m render_agent app --browser`

Tested:
Result:

### Fix round: trace parity (steps F to H)

BROWSER TEST:
1. Practice run fixture_03, Readout model Claude: the main table lists scale, rooms, connections, recesses,
   windows, then any wall rows; its summary line reads "X Green, Y Orange, Z Red"; "Other checks" (fixtures,
   areas, shower, clearance) is a separate table below with its own line. No per-model column.
2. Item names read like the project's: "Bed 3", "Bed 3 -> Hall", "Robe in Bed 3", "Window, Bed 3 top wall".
   Click an opening row: Evidence describes the drawing (arc, dashed line, double lines, nothing).
3. Readout model Both: columns Code, Claude, OpenAI; the seeded disagreement shows
   "Code: ...; Claude: ...; OpenAI: ..." as Orange.
4. Answer batches (up to 5, Red first, across both tables) until every row is Green: the final tables show
   "All items are Green." and Continue; nothing moves on before you press Continue.
5. Open `plan/<dwelling>/readout.md` in the job folder: the same two tables as markdown.
6. Run `python scripts/compare_reference.py fixture_03` (PowerShell: `python scripts\compare_reference.py
   fixture_03`) and open `docs/parity/fixture_03.md`: the owner's first-pass floor plan table (55 rows)
   against what code reads: doors, sliders and arches, garage door, window codes, robes.

Tested:
Result:
