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
