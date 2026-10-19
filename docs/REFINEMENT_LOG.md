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
