# Milestones

Work in order. Each milestone ends at its exit gate: run the gate checks, report, and wait for
approval before moving on. No paid calls before M3 unless the user asks.

## M0: Foundations

- [x] `config.py`: load `config.yaml` + `.env`, validate (unknown keys and bad model IDs fail with the setting name)
- [x] `logging_setup.py`: rotating agent log, per-job `job.log`, API keys redacted
- [x] `errors.py`: TransientError, PermanentError, PauseJobError, InputError; each carries `code`, `what_happened`, `likely_cause`, `what_to_do`
- [x] `util/fs.py`: `atomic_write_text/json/bytes`, `sha256_file`, `safe_name`
- [x] `pipeline/job.py`: job id, folder layout from the spec, `state.json` read/write, stage checkpoints
- [x] `cli.py`: `doctor`, `run --dry-run` (stages may be stubs), `resume`
- [x] `doctor`: Python version, keys present, folders writable, free disk, `muapi` CLI on PATH (warn only), ffmpeg (warn only)
- [x] Tests: config validation, atomic write, state round-trip, `check_no_fixture_refs`

**Exit gate:** `doctor` passes on the laptop; `pytest` green; keys load from `.env`.

## M1: Extraction (stages 1 to 4)

- [x] s1 ingest: PyMuPDF page rasters (DPI from config), text layer per page, scanned-PDF detection, password detection, input errors per the spec table
- [x] `llm/`: `call_json` with OpenAI Responses (strict JSON Schema) and Anthropic Messages (forced tool call); jsonschema validation; one repair retry; transient-error backoff
- [x] s2 sheet analysis: one call per page with image + text layer → `sheets/sheet-N.json` (views with normalised bboxes)
- [x] s3 evidence → `evidence.json`; s4 spec → `house_specification.json`
- [x] `tests/grade_extraction.py`: grade a spec against `tests/fixtures/*/answer_key.yaml`
- [ ] Run once on fixture 01 with `gpt-5.6-sol` and once with `claude-sonnet-5-5` (paid LLM calls, small; ask first) — gpt-5.6-sol done 12 Oct 2026 (gate passed); claude-sonnet-5-5 pending an Anthropic key

**Exit gate:** the fixture 01 spec matches the answer key with zero `must_not_appear` hits and the expected contradiction flagged.

## M2: Planning

- [x] s5 checklist + `validate/checklist_validator.py` enforcing `knowledge/view_set.yaml`: limit per dwelling, required front day, optional night only on request, one secondary bedroom, WIR own view, pantry folded into kitchen, attached vs separate subdivisions, sharing of identical/mirrored views, counts recomputed, corrections logged
- [x] `selections.json`: materials pre-filled from the spec; NOT FOUND items flagged for the gate
- [x] Approval gate: `approval.json`, `APPROVE` file, `approve` CLI command, AWAITING_APPROVAL status
- [x] s6 prompts: reference crops from bboxes (fallback to full sheet), fixed identity block, assembled prompt files
- [x] Unit tests for the validator using hand-built checklists (single house, 4-bed house, attached mirrored, separate subdivision)

**Exit gate (passed 13 Oct 2026, gpt-5.6-sol: 14 renders; crops and identity blocks checked):** for fixture 01 the validated plan is 14 renders (panel counts are styling, so one shared front close-up); crops visibly contain the right elevation; every prompt for a dwelling has an identical identity block.

## M3: Rendering (first paid images)

- [x] `images/`: provider interface; `mock`, `openai_images` (images.edit with reference files), `muapi_cli` (upload, image edit); `budget.py` guard before every paid call
- [x] s7 render with concurrency limit, versioned outputs, per-render failure isolation
- [x] DRAFT overlay on every render (all versions) until approved at review; clean copy kept in `output/clean/` only
- [x] s8 drawing check (`schemas/drawing_check.json`) + one automatic retry with fix instructions; "Not checked" on errors or unsure
- [x] Drawing-check tests on doctored images (extra window, missing door, wrong door colour/material, second storey must fail; 10% larger openings and different panel counts must pass) — built 14 Oct 2026; paid vision run pending

**Exit gate:** the drawing check catches every doctored test image; front views for all three fixture 01 dwellings pass designer review.

Status 14 Oct 2026: doctored images 6/6 caught (paid run; the swapped-order image added later is not yet run paid). Front views rendered with per-dwelling crops + façade order (D1 close-up, shared D2-D3 close-up, streetscape), all pass the drawing check; designer review passed 14 Oct 2026 (feedback applied: single-dwelling framing, COLORBOND swatch tones).

**Exit gate passed 14 Oct 2026.** Open follow-ups: run the swapped-order doctored image against the paid check; Lot 3 cladding callout still placed on the wrong bay by extraction.

## M4: Hands-off on the Windows host

- [x] Manual batch trigger instead (decision 14 Oct 2026): `run` takes several PDFs/folders or `--batch <folder>`, one job each, failures isolated, duplicate PDFs skipped (`--force` to redo), summary table
- [ ] `watcher.py` (DEFERRED 14 Oct 2026): polling, stability check, hash de-dup, single-instance lock, disk-space guard
- [x] s9 review page (127.0.0.1), approve/flag with note, regenerate flagged only, versions kept, approved locked; `review.txt` fallback; approving exports the clean image (no DRAFT overlay) to the dwelling folders — page: `review <job> --serve`
- [x] `output/folders.py`: Common + `Dwelling N` folders, shared renders copied into each dwelling, manifest; single house has no Common
- [x] `report.html`, `manifest.json` (incl. knowledge-file hashes), compliance footer
- [ ] `notify.py` (DEFERRED 14 Oct 2026): Windows toast on approval needed, review ready, failure; 2-working-day reminder
- [ ] `scripts/install.ps1` on a clean host; Scheduled Task restart on failure
- [x] Fault-injection tests for every row of the spec's error table (`tests/integration/test_error_table.py` maps each row to its test; watcher/notify rows marked deferred)
- [ ] Final `docs/INSTRUCTIONS.md`: every instruction-file option and every default, checked against the code (living draft since 14 Oct 2026)
- [x] Wire `@llm` (per-job model choice) and read `selections.yaml` from the drop folder (both parsed/named but not applied yet; see docs/INSTRUCTIONS.md)

**Exit gate:** 10 drops on the Windows host, including a restart mid-render, all end in the correct status; one flag regenerates exactly one render and every other file is byte-identical.

## M5: Video (phase 2)

Separate spec after M4 sign-off: image-to-video clips from approved stills, HyperFrames composition, FFmpeg stitching.

## Golden dataset (later, decision 14 Oct 2026)

5 projects in total: each drawing PDF with the designer's final delivered renders (as
`tests/fixtures/fixture_01/final_renders/`) and an `answer_key.yaml`, run through the batch
trigger to compare the agent's output with what was delivered. fixture 01 is the first; 4 to add.

## Fixtures needed before M3

Fixture 01 is only the first. Add these under `private/fixtures/<name>/`, each with an `answer_key.yaml`:

- [ ] single detached house
- [ ] two-storey house
- [ ] subdivision of separate dwellings
- [ ] a set including a 3D perspective page
