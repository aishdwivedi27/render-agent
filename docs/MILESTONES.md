# Milestones

Work in order: M0 → M1 → M2 → M3 → M4 → M5 → M5.5 → M6 → M7 (M7 is phase 2). Each milestone ends
at its exit gate: run the gate checks, report, and wait for approval before moving on. No paid
calls before M3 unless the user asks. Every exit gate (from 15 Oct 2026) also needs the **spec
sync**: list every `docs/SPEC.md` section the milestone changed and have the owner confirm SPEC.md
and the live page match; only then update "Last synced" at the top of SPEC.md. Claude Code never
runs state-changing git commands; each task ends with changed files grouped "public repo" and
"private submodule (private/)".

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
- [x] `scripts/install.ps1`: private submodule check, checked pip steps, config/folders, doctor; tested on the laptop (`-SkipInstall`, missing-private case). Scheduled Task only once the watcher exists (deferred). Clean-host run is part of the exit gate
- [x] Fault-injection tests for every row of the spec's error table (`tests/integration/test_error_table.py` maps each row to its test; watcher/notify rows marked deferred)
- [x] Final `docs/INSTRUCTIONS.md`: every instruction-file option and every default, checked against the code (living draft since 14 Oct 2026)
- [x] Wire `@llm` (per-job model choice) and read `selections.yaml` from the drop folder (both parsed/named but not applied yet; see docs/INSTRUCTIONS.md)

**Exit gate:** 10 drops on the Windows host, including a restart mid-render, all end in the correct status; one flag regenerates exactly one render and every other file is byte-identical.

Rehearsed on the Windows laptop 14 Oct 2026 (mock images, no spend): 10 drops in one batch all ended
correctly (6 AWAITING_APPROVAL, broken/empty/password-protected FAILED to failed/, duplicate
SKIPPED); a render killed mid-way resumed with the finished renders byte-identical and none redone;
one flag made exactly one image call and changed no existing file.

**M4 closed 15 Oct 2026, apart from the clean-host install check.** That check ("the same on the
Windows host after a fresh `install.ps1`") moved to M6's exit gate, where it is done with
`Setup.exe` on a clean designer laptop (decision 15 Oct 2026: no separate host).

## M5: Designer desktop app (FastAPI + native window)

Decisions 15 Oct 2026 (SPEC §12, points 1 to 12). Designers use the agent with no terminal.

- [x] `config.py`: install root and working root resolved separately (`C:\Program Files\RenderAgent` read-only; `%LOCALAPPDATA%\RenderAgent` for jobs, config, logs, feedback); dev mode keeps the repo layout; no hard-coded drive letters or user names
- [x] Desktop `RenderAgent\Input`, `Output`, `History` created on first launch (known-folder API); Start: copy, sha256 verify, move originals to `History\<YYYY-MM-DD HHMM> - <input name>\` with `started.txt`; `-2`/`-3` and ` (YYYY-MM-DD)` suffixes; Output `Draft\`, `Final\`, `report.html` linking to History; OneDrive detection; all Output/History writes atomic (SPEC §8)
- [x] FastAPI app package `src/render_agent/app/`: API routes for Input items, jobs, approval gate, review, settings and health; calls runner, gate, s9_review and output directly (never the CLI); localhost only, per-session token on every state-changing request, files served only from the job's own folders
- [x] Background job worker with queue: one job rendering at a time; `state.json` the single source of truth; UI polls (or SSE)
- [x] `keyring` key storage (Windows Credential Manager), no-cost key validation, `.env` developer fallback; keys never in logs, UI responses or job files
- [x] `images.enabled_providers` (first release `openai` only); Settings and Start show a provider only when enabled with a valid key
- [x] Flag categories (wrong material/colour, extra opening, missing opening, wrong roof/storeys, wrong framing/view, other) + correction text on review; job corrections at the gate or review saved to `corrections.json`, overriding the House Specification for later renders and re-runs, shown in `report.html`
- [x] Feedback store `%LOCALAPPDATA%\RenderAgent\feedback\feedback.jsonl` (job ID, view, category, correction, "suggest as a general rule"); Help → "Export feedback" zip (text and thumbnails only, no keys, no drawings) to `Desktop\RenderAgent`
- [x] Test: a job correction overrides the spec on regeneration and never touches `knowledge/` or `prompts/`
- [x] pywebview launcher (Edge WebView2) on a free 127.0.0.1 port, single-instance lock
- [x] One dev launch command (bash and PowerShell) that opens the app window from the repo
- [x] Screens:
  - Home: items waiting in Input with Refresh and Start (directive options on Start), job list with plain-English status, Retry on failed or flagged jobs, OneDrive warning when relevant
  - Approval: planned views and materials, NOT FOUND items to fill, fact corrections, planned image count before any spend, Approve button
  - Progress
  - Review: thumbnails with reference crop and drawing-check result, approve / flag with category and note, Continue
  - Results: "Open Output folder" and "Open History folder" buttons
  - Settings: keys, enabled providers only, model choice, test connection
  - Help: doctor results in plain English, "Copy diagnostics", "Export feedback"
- [x] Every `error.json` is shown as its what_happened / what_to_do text, never a stack trace
- [x] New error-table rows (SPEC §9) each covered by a fault-injection test
- [x] Tests use FastAPI TestClient with FakeLLM and the mock image provider

**Exit gate (three parts):**

(a) Automated: fixture 01 runs end to end through the API with FakeLLM and mock images; the
original moves to a dated History folder with a correct `started.txt`; one flag regenerates exactly
one render and every other file is byte-identical; all existing tests green, including
`check_no_fixture_refs`.

(b) Developer-laptop front-end test, done by the owner by hand: Claude Code writes
`docs/M5_UI_TEST.md`, a checklist followed in the app window with no terminal after launch: enter
and test a key in Settings; drop a PDF into Input and press Start with two toggles set; confirm the
PDF moved to a dated History folder with a correct `started.txt`; approve the plan after filling one
NOT FOUND item; watch progress; flag one render with a note; continue; approve the rest; check Draft
and Final contents; open the Output folder; trigger one input error (a password-protected PDF) and
read its message; close and reopen the app mid-render and confirm it resumes. The owner ticks each
line and notes anything confusing.

(c) Refinement rounds: the owner records UI feedback in `docs/M5_UI_FEEDBACK.md` (one numbered item
per change, status open / done / won't do). Claude Code works through open items one round at a
time, re-runs part (a) after each round, and adds or updates lines in `docs/M5_UI_TEST.md` for
anything it changed. Rounds repeat until the owner writes "M5 signed off" with a date at the top of
`docs/M5_UI_FEEDBACK.md`.

Spec sync confirmed. M5.5 must not start until "M5 signed off" exists and the spec sync is confirmed.

## M5.5: End-to-end refinement on real projects (paid runs, through the M5 app)

- [ ] Fixture intake: PDF + delivered renders in `private/fixtures/fixture_NN/` (`input/`, `final_renders/exterior|interior/`). Claude Code drafts `answer_key.yaml` from the PDF with every value "unverified"; the owner or a designer verifies it. Generic names only
- [ ] Run: real models under a per-round cap `images.max_images_per_round`, checked by `budget.py`; show the planned image count and estimated cost and wait for the owner's OK before any spend
- [ ] Compare: `scripts/compare_fixture.py` writes `runs/<date>/comparison.html` per fixture: extraction grade vs answer key, planned vs delivered views, agent render beside delivered render, drawing-check result, designer flags and notes
- [ ] Learn: findings in `docs/REFINEMENT_LOG.md` (date, fixture, problem, root cause, fix, file). Fixes only as general rules in `private/knowledge/` and `private/prompts/`; `check_no_fixture_refs` stays green. Delivered renders are evaluation targets only, never sent to the image model
- [ ] Regression: after each fix round re-run extraction and planning on all fixtures (LLM only); re-render only the fixtures the fix affects
- [ ] Feedback promotion: `scripts/review_feedback.py` groups designer feedback by category and proposes general rules; only owner-approved proposals are applied, and they go through the same regression
- [ ] Metrics: one row per fixture per round in `docs/REFINEMENT_METRICS.csv` (date, fixture, extraction score, must_not_appear hits, first-pass drawing-check pass rate, first-pass designer approval rate, regenerations, LLM cost, image cost); each round shows the change
- [ ] Holdout: one extra verified fixture marked `holdout: true`, never used to propose fixes, run only at the gate

**Exit gate:** fixture 01 + 4 new (single house, two-storey, separate subdivision, set with a 3D
page), each with a verified answer key, zero `must_not_appear` hits, expected contradictions
flagged, front views passing designer review; holdout within the agreed margin of the tuned
fixtures; "M5.5 signed off" with a date at the top of `docs/REFINEMENT_LOG.md`; spec sync confirmed.
M6 must not start before.

## M6: Windows installer

- [ ] `scripts/build_installer.ps1`: builds the PyInstaller folder (one-folder mode) and the Inno Setup `Setup.exe` in one command; version stamped from `pyproject.toml`; app icon
- [ ] Installer key page with validation (no-cost authentication call); installation succeeds only with working keys
- [ ] Optional `muapi-cli` component (off by default) that asks for a MUAPI key on the key page
- [ ] WebView2 runtime check / install
- [ ] Desktop and Start menu shortcuts; uninstaller in Windows "Apps"
- [ ] Upgrade over an existing install keeps config, keys, working jobs and the Desktop folders
- [ ] Private `knowledge/` and `prompts/` bundled (risk recorded in SPEC §10); unsigned build (SmartScreen note in README)

**Exit gate:** copy `Setup.exe` from a pen drive to a clean Windows laptop with no Python, Git or
VS Code; install, enter keys, launch from the desktop icon and complete the M4 10-drop rehearsal
through the UI (this is M4's clean-host install check, moved here 15 Oct 2026); an upgrade install
keeps keys, jobs and Desktop folders; an upgrade that turns on the muapi component works without
reinstalling; uninstall leaves `Desktop\RenderAgent` in place; spec sync confirmed.

## M7: Video (phase 2)

Separate spec after M4 sign-off: image-to-video clips from approved stills, HyperFrames composition, FFmpeg stitching.

## Phase 2 backlog (after M7)

- `watcher.py`: automatic start from Input
- `notify.py`: Windows toasts and the 2-working-day reminder
- Browser mode for the UI
- Code signing

## Golden dataset and fixtures

Replaced by M5.5 (15 Oct 2026): fixture 01 + 4 new verified fixtures and one holdout, built and
scored there.
