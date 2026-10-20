# M5.5 refinement log

<!-- When M5.5 is done, write on the first line above this comment:  M5.5 signed off <YYYY-MM-DD>
     M6 cannot start until that line exists. -->

One row per finding from a refinement round. Fixes go in only as **general rules** in
`private/knowledge/` or `private/prompts/` (never a fixture's address, number, client or lot names;
`check_no_fixture_refs` stays green). Delivered renders are evaluation targets only and are never
sent to the image model. After each fix round, extraction and planning re-run on all fixtures (LLM
only); only the fixtures the fix affects are re-rendered, and the result goes in the last column.
The holdout fixture is never used to propose a fix.

Before every paid round: planned image count per fixture, `images.max_images_per_round`, and the
estimated LLM and image cost are shown to the owner, and nothing runs without the owner's OK.

Evidence per round: `runs/<YYYY-MM-DD>/<fixture>/comparison.html` (`scripts/compare_fixture.py`).
Numbers per round: `docs/REFINEMENT_METRICS.csv`.

## Rounds

| Round | Date | Fixtures | Image cap | Est. cost (LLM + images) | Owner OK | Actual cost |
| --- | --- | --- | --- | --- | --- | --- |

## Findings

| Date | Round | Fixture | Problem | Root cause | Fix (general rule) | File | Regression result |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-20 | wall-placement fix (phase 1) | all | Interior renders put windows, doors, robes and fixtures on the wrong walls; image prompts carried size codes; finals were 1536x1024 upscaled | Sheet analysis recorded room connections only, never which wall; symbol-only fixtures were not extracted; FLOOR_PLAN was free text; the scene LLM wrote interiors blind; the image model got the whole floor plan and site plan; the drawing check judged interiors on roof and storeys | Per-room wall layouts read from the plan (page frame, symbols included) and copied into ROOMS by code; camera rules per interior view in view_set.yaml; code-written ROOM LAYOUT block in viewer terms; room crop as reference; size and opening codes stripped from image prompts (patterns in view_set.yaml); interior drawing check uses opening_walls and fixture_positions; generation 2560x1440, approved landscape finals delivered at exactly 1920x1080 (Lanczos, lossless sRGB). Phase 2 item: portrait finals at 1080x1920 if gpt-image-2 prices portrait the same as landscape, else the cheapest good-quality size | schemas/sheet_analysis.json, schemas/house_spec.json, schemas/drawing_check.json, src/render_agent/pipeline/room_layout.py, stages s2/s4/s6/s8, output/folders.py, output/report.py, config.py, private/prompts (sheet_analysis, evidence, spec, render_prompts, render_rules, drawing_check), private/knowledge (drawing_reading_rules, view_set) | Unit + integration tests pass with FakeLLM; paid LLM-only run pending owner OK |
| 2026-10-20 | wall-placement fix, owner review rounds | SATL 0005 (new job) | Drafts cost as much as finals (18 high-quality 2560x1440 images for 3 views); no token or cost record | Every version, including rejected drafts, was rendered at final quality; providers' usage was discarded | Drafts at images.openai.draft (1536x864, medium); one final at images.openai.final (2560x1440, high) made only after approval, as an edit of the approved draft, drawing-checked, then exported; tokens saved for every image and LLM call and shown with estimated cost (config pricing) in report.html and manifest.json. Also from the rounds: size codes become proportion words (opening_shapes), room crop turned so the facing wall is at the top, review fixes made as edits of the best earlier version | config.py, images/base.py, images/openai_images.py, images/budget.py, llm/base.py, llm/openai_llm.py, llm/anthropic_llm.py, output/costs.py (new), output/report.py, output/folders.py, pipeline/stages/s7_render.py, s9_review.py, room_layout.py, s6_prompts.py, s8_check.py; private: knowledge/view_set.yaml, prompts/render_rules.md, prompts/sheet_analysis.md | 291 tests pass (FakeLLM, mock images); one paid draft+final measurement pending owner OK |
| 2026-10-20 | owner decision after the first finals | SATL 0005 (new job) | A generated HD final cost about 7x a draft (7,370 vs 1,078 output tokens) and 3 of 7 finals drifted from the approved draft and failed the check | A second image call re-renders the picture and can change it | Approval now delivers the approved image itself: DRAFT overlay removed (clean copy), cropped to 16:9 and scaled with Lanczos to images.delivery_size; no final image call. Config keeps images.openai.draft only (old `final` block ignored) | config.py, images/__init__.py, images/budget.py, pipeline/stages/s7_render.py, s9_review.py, config.example.yaml, docs/SPEC.md §6/§9/§10 | 288 tests pass |
| 2026-10-20 | owner decision: output layout | all | Delivered images, every draft version and the clean working copies were mixed in output/ and Output\Draft/Final | No per-dwelling structure for drafts; clean copies of discarded versions kept forever | Per Common / Dwelling N folder: Draft (every version, overlay), Draft/draft-final (approved version, overlay), FINAL APPROVED (approved, no overlay, delivery size). Clean copies only for the latest version and deleted after delivery. Folder names in view_set.yaml output_folders | output/folders.py, pipeline/stages/s7_render.py, s9_review.py, app/designer.py, app/routes/review.py, review/server.py, output/report.py, images/overlay.py, private knowledge/view_set.yaml, docs/SPEC.md §3/§4/§8/§13 | 289 tests pass |
