Last synced with the live page: 29 Oct 2026

# Drawing-to-Render Agent: Specification (developer copy)

Agreed 12 Oct 2026; amended 14 and 15 Oct 2026 (§12). The live, authoritative version is the Claude
Docs page https://claude.ai/code/artifact/29555f11-8ad7-4134-838e-0d2553f96142. If they differ, the
live page wins; update this copy and say so in the commit message. The live page cannot be read from
this repo, so at every milestone exit gate Claude Code lists the sections of this file the milestone
changed and asks the owner to confirm both copies match. The "Last synced" date above changes only
after that confirmation, and a gate is not passed until it is given (decision 15 Oct 2026).

## 1. Purpose and scope

The agent turns a drawing set into a consistent set of photorealistic renders. A designer puts a
Revit-exported PDF (optionally with an instructions `.txt`) into `Desktop\RenderAgent\Input` on their
own Windows 10/11 laptop and starts the job from the Render Agent desktop app.
The agent extracts the documented architecture and materials, plans which views to render, renders
them with an image model, checks each render against the drawings, and hands thumbnails to a
designer for review. Every image must show the building the drawings describe.

- **Phase 1 (this spec):** PDF in; renders out that have been checked against the drawings and
  reviewed by a designer. Inputs: 2D sets (site plan, floor plan, elevations, colour schedule),
  optionally with 3D perspective pages.
- **Phase 2 (designed for, not built):** walkthrough or hero video from approved stills
  (image-to-video clips, HyperFrames, FFmpeg).
- **Out of scope:** design advice; compliance checking of any kind (NCC, Australian Standards,
  council); editing drawings; cost estimating; Eachlabs (dropped).

## 2. Users and journey

| User | Needs | Use |
| --- | --- | --- |
| Builder / developer (primary) | Marketing renders matching the approved plans | Drops the PDF, collects renders |
| Building designer (in-house, non-technical) | Visual check before issue; resolves gaps; no terminal | Runs the desktop app on their own laptop: starts jobs, approves the plan, reviews thumbnails, corrects facts |
| Sales / real-estate agent | Front hero images (night on request), later video | Receives a dwelling's folder |
| Operator | Cost control, failure visibility, prompt tuning | Edits config and knowledge files, reads logs |

Journey: put a PDF (+ optional `instructions.txt` / `selections.yaml`) into `Desktop\RenderAgent\Input`
→ press **Start** on the app's Home screen (options set there) → the original moves to `History` →
agent reads the drawings (stages 1–4) → **Choose views** screen (decision 21 Oct 2026): every
standard view with whether these drawings can support it; the designer picks all, some (External
only / Interior only / ticks) or says it in words, which the app shows as "I understood: …" to
confirm, then sees "What the agent will do" and presses Plan; the choice becomes `@views` (exact ids,
required views always added; `views_chosen.json`) → the agent plans those views and reads materials
from the PDF (stage 5) → **approval gate (always on)**, Approval screen: designer sees the renders
that will be made (and "Change the views" returns to Choose views and plans again), materials,
fills gaps and corrects facts; answers to unclear or disagreeing items ("What it actually means") re-run stages 4–5
with the corrections and show the revised plan with what changed before anything is rendered
(decision 21 Oct 2026) → render (Progress screen, "Step N of 7 · part p of n": Read the drawings, Plans (PV1, 29 Oct 2026), Choose views, Approve the plan, Make the renders, Review, Done) → drawing check (one automatic regeneration on fail) →
**thumbnail review**, Review screen: designer approves or flags individual renders with a category and
a note; only flagged renders are regenerated → approved set in `Output\<input name>\<Dwelling N>\FINAL APPROVED`,
filed by dwelling; a finished job's Output folder then moves to `Output\Completed\` and Home lists it
under Completed. A failed job shows its plain-English `error.json` text in the app, with a Retry button.
Developers can still drive every step from the CLI.

## 3. Functional requirements

| ID | Requirement |
| --- | --- |
| FR-1 | Accept input from `Desktop\RenderAgent\Input`: a loose PDF, a PDF with a same-name `.txt`, or a sub-folder of PDFs (2D set plus optional 3D views) + `instructions.txt` / `selections.yaml`. No folder watcher: Home lists waiting items (Refresh button); a job starts only when the designer presses Start. A PDF dragged onto the app window is placed in Input and goes through the same Start step. On Start the inputs are copied into the job's working folder, each copy verified by sha256, then the originals move to `History` (§8) |
| FR-2 | Read an optional `.txt` with free-text instructions and `@key=value` directives |
| FR-3 | Extract evidence from both the PDF text layer and the page images, per sheet |
| FR-4 | Produce a House Specification as the single source of truth, with "NOT FOUND" for gaps |
| FR-5 | Plan views from the standard view set, only for spaces documented on the plan; label each READY / REVIEW / NOT READY |
| FR-6 | Render using cropped drawing views as reference images, not text alone |
| FR-7 | Keep each dwelling's identity separate in multi-dwelling sites |
| FR-8 | Resume a job from the last completed stage after any failure |
| FR-9 | Report each render's status, references, materials, visualisation choices and open uncertainties |
| FR-10 | State on every output that renders are conceptual with no compliance claim |
| FR-11 | Materials (façade e.g. Hebel, doors, windows, roof, colours) are read from the PDF; the designer confirms at the gate and chooses only what the PDF does not specify |
| FR-12 | Drawing check after each render: fail on a window/door where the drawing has none, a missing documented opening, or wrong roof form, storeys or colours. Opening sizes and proportions may differ slightly (artist's impression) |
| FR-13 | Thumbnail review: approve or flag each render with a category (wrong material/colour, extra opening, missing opening, wrong roof/storeys, wrong framing/view, other) and a correction in the designer's words; only flagged renders are regenerated, with the correction added to the prompt |
| FR-14 | At most 16 planned renders per dwelling: a limit, not a target. Regenerations are outside the 16 and tracked separately |
| FR-15 | Run on each designer's own Windows 10/11 laptop, running that designer's own jobs |
| FR-16 | Desktop app: FastAPI + uvicorn on 127.0.0.1 (free port chosen at start-up) in a native pywebview (Edge WebView2) window; static HTML/CSS/JS served by FastAPI, no Node build, no CDN. It calls the pipeline modules directly, never the CLI; one job renders at a time, others queue; `state.json` is the single source of truth |
| FR-17 | API keys entered on the installer's key page and in Settings, validated with a no-cost authentication call, stored in Windows Credential Manager (`keyring`); no job starts without a valid key for every enabled provider |
| FR-18 | Image providers switchable without code changes; only those in `images.enabled_providers` are offered |
| FR-19 | Corrections at three levels: a render flag (category + correction) changes that render's prompt; a job correction (gate or review) is saved to `corrections.json`, overrides the House Specification for the rest of that job and is shown in `report.html`; every flag and correction is appended to the feedback store for developer review. The app never edits `knowledge/` or `prompts/`; correction text is data, quoted into labelled prompt blocks, and cannot change directives, limits or providers |

## 4. Standard view set (authoritative data: `knowledge/view_set.yaml`)

Priority order; a view is included only when the plan documents that space.

| View | Included when |
| --- | --- |
| Streetscape | Priority 1 whenever there are 2 or more dwellings: one render showing them side by side; inserted if missing, never trimmed or split; never for a single house; `@streetscape=false` omits it |
| Front elevation, day | Always; the dwelling's own front, framed on that dwelling |
| Rear elevation, day | A deck, pergola, alfresco, verandah or patio is documented for that dwelling (code-enforced) |
| Main bedroom | The bedroom the WIR or ensuite opens from on the plan |
| Secondary bedroom | One representative bedroom, however many the plan has |
| Ensuite | Documented |
| Common bathroom | Documented |
| Laundry | Documented |
| Kitchen | Documented; shows the pantry only if its opening is visible from the kitchen camera position (a pantry never gets its own render) |
| Living (family and meals) | Documented |
| Entry hallway | Documented; camera inside the open front door looking along the hall to the study, living or meals beyond |
| Study or second living | Documented |
| Walk-in robe | Documented and only while the plan is under the limit (lowest priority, trimmed first, never inserted) |
| Front elevation, night (optional) | Only when the instruction file asks (`@night=true` or plain words) |

Colours and materials follow the drawings' colour schedule. An item the drawings do not state takes
the standard `default` from `knowledge/materials_library.yaml`, labelled "standard default, not in the
drawings" in the prompt and at the gate, so a PDF with no instruction file runs with a plain approval. The designer or buyer changes them with
`@select <item>=<value>` (e.g. `@select roof_colour=Monument`) or at the approval gate; every change
is a logged override (decision 14 Oct 2026).

A 4-bedroom house gets the same set as a 3-bedroom one. Minor site services (rainwater tanks,
meters, bins) never create a render.

**Subdivisions**
- **Attached** (shared walls / boundary to boundary): one streetscape showing all dwellings
  side by side (priority 1), plus a front close-up per dwelling; dwellings whose fronts are the same (including mirrored)
  share one close-up. Any other view identical in two dwellings (mirrored or repeated) is rendered
  once from a representative dwelling. A dwelling gets its own render only where the drawings show
  a difference a viewer would notice; dimensional differences within artist's-impression tolerance
  (e.g. a room 300 mm deeper) do not count.
- **Separate** (boundary with open space between buildings): each dwelling is an independent house.

**Worked example (fixture 01, three attached dwellings):** D1 mirrors D2; D3 repeats D2. D1 has a
4-panel slider (DSD2429-4) and no deck; D2/D3 have a 2-panel slider onto a 24-24 timber deck. D2's
front window is "21-12 awn" on the plan (size and type) and drawn as four stacked panels on
Elevation 1 (appearance): one window, and the panel count is styling only, so D1-D3 share one front
close-up. Plan (view set of 14 Oct 2026): streetscape, shared front close-up, rear D2-D3 (D1 has no deck, so no
rear view), family-meals D1, family-meals D2-D3, 9 shared interiors (incl. entry hallway; WIR last) = 14,
plus a night view only on request.

**Reading drawings** (`knowledge/drawing_reading_rules.md`, decision 13 Oct 2026): a plan size code
and an elevation depiction of the same opening are one fact (size from the plan, look from the
elevation); panel and sash counts are styling and never split, review or block a view. Every render must
show every documented window and door in position, and doors in their documented material and colour. Roof junctions between attached dwellings that the front/rear elevations do not fully
show are REVIEW items, never blockers on their own. Renders are artist's impressions.

**Blocked views at approval** (decision 21 Oct 2026): a view the plan marks NOT READY because the
drawings disagree or are unclear shows its reasons with a "What it actually means" box (re-plans with
the answer as a job correction) and a "Render anyway" tick: the view becomes REVIEW (marked for a
closer look at review), its blockers are kept, and `approval.json` records it in `rendered_anyway`.
Views blocked by code (a drawing type missing from the set, or a required view inserted by the
validator) cannot be rendered anyway.

**Draft overlay** (decision 13 Oct 2026): every render is shown and delivered with a visible "DRAFT"
overlay until the designer approves it at review. The overlay-free copy is kept only inside the job
folder (`output/clean/`), only for the latest version of each render (a replaced version's copy is
deleted), and is deleted once that version is delivered (decision 20 Oct 2026). Approving a render
delivers it without the overlay to `FINAL APPROVED` and copies its overlay version to
`Draft/draft-final`; uncertain details are dealt with at review, not marked on the image.

**Output folders** (`knowledge/view_set.yaml: output_folders`)
```
output/
  Common/              master copy of every render that stands for more than one dwelling;
                       file name lists the dwellings it covers, e.g. rear_day_D2-D3.png
  Dwelling 1/          that dwelling's complete set: its own renders + copies of shared ones
  Dwelling 2/ ...
  report.html  manifest.json
```
Regenerating a shared render replaces the master and every copy; `manifest.json` records copies.
A single-house job has no `Common` folder. Inside each `Common` / `Dwelling N` folder: `Draft\` (every
version with the overlay), `Draft\draft-final\` (the approved version with the overlay) and
`FINAL APPROVED\` (the approved version without it, at `images.delivery_size`); folder names come from
`knowledge/view_set.yaml` `output_folders` (decision 20 Oct 2026). This is the job's working `output/`;
designers get the same layout under `Output\<input name>\` (§8).

## 5. Knowledge base and precedence

Rules are converted into prompt text (`prompts/`), JSON Schemas (`schemas/`) and code checks
(`validate/`). Code checks matter most. Precedence: drawings → designer gate decisions → instruction
file (presentation only) → `knowledge/visualisation_defaults.md`. Each job records knowledge-file
hashes in `manifest.json`. See `knowledge/README.md`.

## 6. Pipeline

| # | Stage | Output | Engine |
| --- | --- | --- | --- |
| 1 | Ingest | `pages/page-N.png` (DPI from config), `pages/page-N.txt`, `ingest.json` | PyMuPDF |
| 2 | Sheet analysis | `sheets/sheet-N.json` (sheet type, north arrow angle, views with normalised bboxes, room layouts, findings) | Vision LLM, one call per page, image + text layer |
| 3 | Evidence | `evidence.json` | LLM, `schemas/evidence.json` |
| 4 | House spec | `house_specification.json` (ROOMS copied by code from the sheets) | LLM, `schemas/house_spec.json`; `corrections.json` quoted as a data block when present |
| 5 | Checklist + selections | `checklist.json`, `checklist_validation.json`, `selections.json` | LLM then deterministic validator; `corrections.json` quoted as data; a view-only re-plan from approval re-validates the saved `llm_checklist` with no LLM call |
| Plan | Plan visuals stages `gate_plan_start` … `gate_continue` (docs/PLAN_VISUALS_SPEC.md §1; 28 Oct 2026, PV0) | `plan/…`, `memory/…` | Each stage is enabled by its PV step; until then new jobs mark it `skipped: not enabled yet`, and jobs started before PV0 mark it `skipped: added after this job started`. Skipped stages count as finished and are left out of progress |
| Views | Choose views (app jobs; CLI jobs pass at once and use `@views` from their instruction file) | `views_chosen.json`, `@views` written into `ingest.json` and `input/start_options.txt` | Designer in the desktop app (Choose views screen) |
| Gate | Approval (always on) | `approval.json`, `corrections.json` | Designer in the desktop app (Approval screen); developers: `APPROVE` file or CLI |
| 6 | Prompts | `prompts/<render_id>.txt`, `references/<view_id>.png` | LLM scene text; code assembles |
| 7 | Render | `output/<render_id>_vN.png` | gpt-image-2 (default) or MuAPI |
| 8 | Drawing check | `checks/<render_id>_vN.json` | Vision LLM, `schemas/drawing_check.json` |
| 9 | Review and report | `report.html`, `manifest.json`, `review.json`, dwelling folders | Desktop app Review screen (127.0.0.1); developers: `review --serve` / CLI |

**Prompt assembly (stage 6), in order:** fixed identity block from the spec (construction, roof and
pitch, eaves, storeys, every colour-schedule line; identical in every prompt for that dwelling) →
reference legend ("Image 1 is Elevation 1 (street front), sheet 3") → scene/camera/lighting from the
LLM → room layout (interiors) or façade left-to-right list (exteriors) → must-preserve and
must-avoid lists plus global rules → visualisation choices labelled as undocumented.

**Room layouts (stages 2, 4, 6; 20 Oct 2026):** sheet analysis records every room on a floor plan with
its bbox and, per wall as drawn on the page (top, bottom, left, right, other), the openings and
fixtures on it in order, each with a position along the wall (left end … right end, corner, island).
Fixtures are read from symbols. The north arrow is stored per sheet (`north_angle_deg`) for the
report, sun direction and compass-named elevations only, never as a wall label. Stage 4 copies the
rooms into `ROOMS` by code; a room drawn differently on two sheets keeps both and is flagged. Stage 6
finds the room for each interior render, picks the wall the camera faces from the view's `camera`
rule in `knowledge/view_set.yaml`, and writes the ROOM LAYOUT block in the viewer's frame ("Facing
you", "Left wall", "Right wall", "Behind you (not in view)"). The scene LLM receives the block as
text and must not change it. The image model gets the room cropped from the floor plan (bbox plus
`images.room_crop_margin`) instead of the whole plan and the site plan. No usable layout or an
invalid bbox → the previous behaviour, with the reason in `prompts/index.json` and the report.
Size and opening codes (patterns in `view_set.yaml`) are stripped from all text sent to the image
model; the spec and report keep them.

**Materials (stage 5):** `selections.json` holds one entry per item (façade wall, feature cladding,
entry door type and material, garage door type and finish, window frames, roof profile and colour)
with the PDF value and source sheet or NOT FOUND. The gate shows NOT FOUND items a planned view will
show, with options from `knowledge/materials_library.yaml`. Changing a PDF value is a logged
designer override.

**Drawing check (stage 8):** input = render + same reference crops + the opening list for that face
from the spec (exteriors) or the ROOM LAYOUT block (interiors). Interiors are checked for
`opening_walls` and `fixture_positions` instead of roof form and storeys: fail only for an opening or
fixture on the wrong wall, at the opposite end of the right wall, missing or extra; sizes and small
shifts along a wall pass; an interior answer without these two checks is "Not checked". Hard fail: extra or missing window/door/garage door on a visible face, wrong storeys
or roof form, visibly wrong schedule colour. Tolerated: opening sizes, exact proportions,
landscaping, people, vehicles, sky. Fail → one automatic regeneration with fix instructions; a
second fail goes to review marked "Failed drawing check". Error or unsure → "Not checked".
The judge looks at the image itself and gives each check a `confidence` plus an overall
`image_match` (0–1). Code takes the lowest of these as the render's confidence; a pass below
`drawing_check.min_confidence` (0.85) becomes "Not checked" with the reason, and the review card
shows "N% match". The confidence is self-reported by the model, not calibrated; tune the floor
against the fixture answer keys in M5.5.

**Approval delivers the approved image (stages 7 and 9; decision 20 Oct 2026):** every render is made
at `images.openai.draft` (size and quality) and reviewed with the DRAFT overlay. Approving a render makes
no further image call: its clean copy (the same image without the overlay) is centre-cropped to 16:9 and
scaled locally with Lanczos to `images.delivery_size`, then exported. Tokens reported by every image and
LLM call are saved (`budget.json`, `usage.json`); report.html and manifest.json show tokens and the cost
estimated from `pricing` per render, per LLM stage, and in total.

**Designer review (stage 9):** thumbnails grouped by dwelling beside their reference crop and check
result; Approve / Flag + category + correction; "Regenerate flagged" re-runs stages 6 to 8 for flagged IDs only;
results saved as `_v2`, `_v3`; approved renders are locked and exported without the DRAFT overlay. Fallback: `review.txt`, one line per
flag (`<render_id>: <note>`).

## 7. Providers

| Kind | Provider | Model (config) | Notes |
| --- | --- | --- | --- |
| LLM (default) | OpenAI Responses API, strict JSON Schema | `gpt-5.6-sol` | as in the n8n flow |
| LLM (swap) | Anthropic Messages API, schema via forced tool call | `claude-sonnet-5-5` | per job (`@llm=sonnet`) or per stage |
| Image (default) | OpenAI `images.edit` with reference files (`images.generate` if none) | `gpt-image-2` (`gpt-image-2.5-flare`, `gpt-image-2.5-sunburst` selectable) | |
| Image | MuAPI via `muapi-cli` (`upload file`, `image edit --model nano-banana-2-edit`) | config | verify CLI flags with `muapi image edit --help` in `doctor` |
| Image | Mock | n/a | placeholder images; dry runs and tests |

One interface each: `llm.call_json(stage, prompt, images, schema)` and `images.Provider.render(...)`.

## 8. Folder contract, directives, statuses

Three roots, none with a hard-coded drive letter or user name (decision 15 Oct 2026). `config.py`
resolves the install root separately from the working root; the Desktop is found with the Windows
known-folder API.

```
C:\Program Files\RenderAgent\       install root, read-only: program, knowledge/, prompts/, schemas/,
                                     engine/ and assets/ (plan visuals, 28 Oct 2026)

%LOCALAPPDATA%\RenderAgent\          working root (agent files; designers never need to open it)
  config.yaml
  jobs/<id>/      input/ pages/ sheets/ references/ prompts/ checks/
                  output/  clean/  Common/  Dwelling 1/ ...
                  state.json  selections.json  approval.json  corrections.json
                  review.json  error.json  job.log
  feedback/       feedback.jsonl (every flag and correction, all jobs)
  memory/         projects/<project key>.json (project memory), agent_rules.json (agent memory, seeded
                  once with the owner rules of 26 Oct 2026; PLAN_VISUALS_SPEC §13; 28 Oct 2026)
  logs/

Desktop\RenderAgent\                 designer folders, created on first launch
  Input\          a PDF; a PDF + same-name .txt; or a sub-folder of PDFs + instructions.txt / selections.yaml
  History\<YYYY-MM-DD HHMM> - <input name>\   originals moved here on Start + started.txt
  Output\<input name>\   while the job runs; Output\Completed\<input name>\ once it is finished (21 Oct 2026)
                  Common\ and Dwelling N\   (output/folders.py; decision 20 Oct 2026)
                    Draft\             every render version with the DRAFT overlay (_v1, _v2 …, never overwritten)
                      draft-final\     the approved version, still with the overlay
                    FINAL APPROVED\    the approved version without the overlay, at images.delivery_size
                  report.html  links back to the matching History folder
```

Dev mode (running from the repo) keeps today's repo-relative layout: everything under the repo root.

**Input.** Home lists the items waiting in Input with a Refresh button; there is no watcher. A job
starts only when the designer presses Start. A PDF dragged onto the app window is copied into Input
and then goes through the same Start step. An item still being copied or locked is not startable
(Start refuses with a message).

**Start.** (1) Copy the inputs into `jobs/<id>/input/`; (2) verify every copy by sha256 against its
original; (3) move the originals to `History\<YYYY-MM-DD HHMM> - <input name>\` (local time of Start;
if the name exists, append `-2`, `-3`); (4) write `started.txt` there: start date and time in plain
words, Windows user name, the full Output folder path and the job ID. If the copy or the
verification fails, the originals stay in Input and the app shows a plain-English error. A PDF
already processed (same sha256) opens the existing job; the designer may choose "Start anyway".

**Output.** `Output\<input name>\`; if it exists, append ` (YYYY-MM-DD)`, then ` (YYYY-MM-DD-2)`.
Every Output and History file is written atomically (`util.fs.atomic_write_*`), so a sync lock can
never leave a half-written file. The job's working state lives only in the working root: moving or
deleting Output or History folders never breaks a job.

**Completed** (decision 21 Oct 2026). When a job finishes, its Output folder moves to
`Output\<designer.completed>\<input name>\` (default `Completed`; ` (2)`, ` (3)` on a name clash) and
`state.json` records the new place. At app start, finished jobs (SUCCEEDED, or every render approved
at review) and Output folders that hold FINAL APPROVED images and belong to no job in progress are
moved there too. Home shows finished jobs in a separate Completed list. A failed move leaves the
folder where it is with a plain warning and is tried again at the next start.

**Retry.** A failed or flagged job is retried with a button that resumes the existing job; designers
never re-drop files.

**Cancel** (22 Oct 2026). Any job that is not finished has a Cancel button (Home card, progress and
stopped screens), with a confirm. Cancel moves the originals from the job's History folder back to
Input (a name already taken in Input gets " (2)"), deletes `started.txt` and the History folder and the job's working folder, moves the job's Output
folder (only if it is inside Output) to `Output\<designer.cancelled>\<input name>\` (default `Cancelled`;
" (2)" on a name clash), and takes it off the queue. A job with no Output folder (started from the
CLI) moves its working folder there instead; Cancel never deletes renders. The originals move first; if that fails nothing is deleted. A running job shows "Cancelling…",
stops before its next stage or next render (renders already in flight finish), and is removed then.
Finished jobs cannot be cancelled.

**OneDrive.** If the Desktop is redirected to OneDrive, Home shows a one-time warning suggesting
`Desktop\RenderAgent` be excluded from sync.

Each directive below is also an option on the Start step. Start options are written as these directives to `start_options.txt` in the job's `input/` folder, so they are recorded with the job and go through the same validation; the app's "Practice run" is a dry run (offline reader and mock images, no key needed).

| Directive | Example | Effect |
| --- | --- | --- |
| `@night` | `@night=true` | Adds the optional front night view (plain words work too) |
| `@streetscape` | `@streetscape=false` | Omits the streetscape (included by default for 2+ dwellings) |
| `@provider` | `@provider=muapi` | Image provider for this job (enabled providers only; any other is an invalid directive) |
| `@llm` | `@llm=sonnet` | Language model for this job |
| `@dwellings` | `@dwellings=DWELLING 1` | Only these dwellings |
| `@views` | `@views=front_day,rear_day` or `@views=Only external front and back elevation` | Only these views (required views are always kept). View ids, labels and plain words all work: words are matched to `view_set.yaml` views by code (`validate/view_words.py`, no LLM); optional views named here count as requested; words that match no view are listed as ignored, and if nothing matches every view is planned. In the app the view choice is its own screen after the drawings are read (Choose views): ticks or words confirmed as "I understood: …" become `@views` with exactly the chosen ids; an `@views` line (and night words) in the instruction file pre-tick that screen, and its free text is shown with "Read views from this text" to confirm in the same way; with no instruction file the screen alone decides (decision 21 Oct 2026) |
| `@max_images` | `@max_images=6` | Lowers the per-dwelling limit; cannot raise it |
| `@select` | `@select entry_door=pivot, spotted gum` | Pre-sets a material selection (still shown at the gate) |
| `@plans_mode` | `@plans_mode=renders_only` | `plans` or `renders_only` ("Exterior renders only (no plans)", today's path). Pre-selects Start the plan check in the app; a CLI job uses it directly and defaults to `renders_only` (PLAN_VISUALS_SPEC §2, 29 Oct 2026) |
| `@readout_llm` | `@readout_llm=both` | Readout model `claude`, `openai` or `both`; pre-selects Start the plan check (PLAN_VISUALS_SPEC §2) |
| `@dry_run` | `@dry_run=true` | Mock provider throughout |

Statuses (amended 15 Oct 2026, M5), each shown in the app in plain words:

| Status | App shows | Meaning |
| --- | --- | --- |
| RUNNING / RENDERING | Working on it… / Making your renders… (Waiting its turn while queued) | stages running |
| AWAITING_VIEWS | Choose the views | drawings read; waiting for the view choice before planning |
| AWAITING_APPROVAL | Waiting for your approval | approval gate |
| AWAITING_REVIEW | Ready for your review | thumbnail review |
| SUCCEEDED | Finished | every render approved |
| PAUSED | Paused, needs your attention | key, credit or disk problem; nothing lost; Retry resumes |
| FAILED | Something went wrong | error.json shown; Retry resumes the existing job |
| (any, cancel pending) | Cancelling… | running job stops at its next step, then is removed; its Output folder moves to Output\Cancelled |
| AWAITING_PLAN_START, AWAITING_PLAN_CHECK, AWAITING_ELEVATION_CHOICE, AWAITING_ELEVATION_CHECK, AWAITING_STYLES, AWAITING_FURNISH_CHECK, AWAITING_PLAN_REVIEW, AWAITING_CONTINUE | Start the plan check / Check the plan: questions for you / Do the elevation readout, or skip it? / Check the elevations: questions for you / Choose plan styles / Check the furniture / Review the plans / Plans are ready | plan visuals gates (PLAN_VISUALS_SPEC §1, §14; added 28 Oct 2026, PV0); until its PV step is built the app shows "This screen is coming in PV<n>" |
| PLAN_COMPLETE | Finished: plans only | the designer chose Finish here on Plans are ready (PLAN_VISUALS_SPEC §9) |

Stage states in `state.json`: pending, running, done, failed, skipped (with a `reason`; 28 Oct 2026).

## 9. Error handling

Rules: (1) fail small: call → render → stage → job, never the app; (2) never lose work: atomic
writes, versioned renders; (3) resume, don't restart: idempotent checkpointed stages; (4) no silent
failures: every error logged with a code, job-stopping errors write `error.json` (what happened,
likely cause, what to do), shown in the app as its what_happened / what_to_do text, never a stack
trace; (5) tell a person: the app's Home and job screens show approval needed, review ready and
failure (Windows toasts are phase 2); (6) retry only what can succeed; (7) spend safely: budget check before every paid call;
(8) validate everything that crosses a boundary.

| Area | Failure | Behaviour |
| --- | --- | --- |
| Input | Not a PDF, empty, won't open | Job FAILED + error.json "not a readable PDF", shown in the app |
| Input | Password-protected | Job FAILED; app asks for an unlocked copy |
| Input | Scan with no text layer | Continue on images only; lower confidence; said at the gate |
| Input | Item in Input still copying or locked | Shown as "still copying"; Start refused with a message until it is stable (size and mtime unchanged for `stable_seconds`) and readable |
| Input | Same PDF started twice | sha256 at Start; app opens the existing job; "Start anyway" makes a new job |
| Input | Copy verification failed at Start | Originals stay in Input; partial job copy removed; plain-English error on Home |
| Input | No elevation or no floor plan | Affected views NOT READY with reason; job continues |
| Input | Unknown/invalid directive | Ignored, listed at the gate; free text still used |
| Extraction | Invalid JSON / schema break | One repair retry quoting the error; then stage fails, raw output saved |
| Extraction | Refusal or truncation | Stage fails with the reason; no retry loop |
| Extraction | Drawings contradict | Both values kept and flagged at the gate |
| Extraction | Invalid/empty bbox | Full sheet image used for that view; noted |
| Extraction | Room layouts from the sheets do not fit `schemas/house_spec.json` ROOMS | Stage 4 stops with SCHEMA_INVALID and error.json (delete `sheets/` and resume) |
| Prompts | Interior render with no matching room, no openings or fixtures read, or no wall to face | Previous behaviour (whole floor plan, no ROOM LAYOUT block); reason in `prompts/index.json` and the report |
| Prompts | Room bbox invalid | Whole floor plan used instead of the room crop; noted in `prompts/index.json` |
| Drawing check | Interior answer without `opening_walls` and `fixture_positions` | "Not checked", with the reason |
| Planning | `@views` words match no view | Listed under "These options were not used"; matched words still apply; nothing matched → every view planned |
| Approval | Re-plan with corrections fails (LLM error) | Stage error as usual: plain `error.json`, Retry resumes the re-plan; corrections stay in `corrections.json` |
| Output | Moving a finished job to Output\Completed fails (file in use) | Job stays SUCCEEDED, folder left in place, plain warning on the job; retried at the next app start |
| Planning | > 16 renders for a dwelling, or counts wrong | Validator corrects by priority; each correction logged |
| Planning | Required front view missing | Validator inserts it as NOT READY with a blocker |
| Rendering | Rate limit, timeout, 5xx, network | Backoff, 5 attempts; then that render fails, others continue |
| Rendering | Content-policy / bad request | No retry; prompt and reason shown at review |
| Rendering | No image or unreadable image | Failed attempt; retried once |
| Rendering | Fails drawing check | One automatic regeneration; second fail → review "Failed drawing check" |
| Rendering | Drawing check errors or unsure | Review "Not checked"; never silently passed |
| Rendering | Drawing check passes with confidence below `drawing_check.min_confidence` | Review "Not checked" with "confidence X is below Y"; no regeneration |
| Rendering | Paid image calls reach `images.max_images_per_round` (M5.5 refinement cap) | Job PAUSES with code ROUND_CAP_REACHED before the next paid call; finished renders kept; owner approves a higher cap, then Retry resumes |
| Review | Designer flags a render | Only that render regenerated; counted and costed |
| Corrections | Correction text tries to act as an instruction (directive, limit, provider) | Treated as data only, quoted into a labelled prompt block; no behaviour change; shown at the gate/review as entered |
| Corrections | Feedback store not writable | Warning in the app and logged; the correction still applies to the job; job continues |
| Corrections | Export feedback fails (disk, permissions) | Plain-English message; no partial zip left (atomic write) |
| Account | Key missing/invalid/expired | Job PAUSES with "replace the key in Settings" (developers: `.env`); Retry resumes when fixed |
| Account | Invalid or missing key at install or first run | Installer key page / first-run check fails with a plain message (no-cost authentication call); no job can start until every enabled provider has a valid key |
| Account | Provider enabled without a key | Settings lists every provider in `llm.choices` and `images.offered_providers` with "No key yet" (28 Oct 2026, PV0); Start does not offer an image provider without a key; Home names the provider and says to add its key; no job uses it |
| Account | Out of credit / quota / spending limit (LLM or images; e.g. `insufficient_quota`, "credit balance is too low", HTTP 402) | Never retried. Job PAUSES with code OUT_OF_CREDIT: which provider, the provider's message, where to top up, and the Retry button (developers: `resume`); finished stages and renders kept and never paid for twice |
| System | Disk below 2 GB free | No new jobs; running jobs pause; message on Home |
| System | Crash, reboot, power loss | Reopening the app resumes queued and running jobs from `state.json` |
| System | Second app instance | Single-instance lock: the second launch exits and brings the open window to the front |
| System | Port in use | A free 127.0.0.1 port is chosen at start-up; if none can be bound, plain-English error and the app exits |
| System | WebView2 runtime missing | Installer installs it; at launch, plain-English message saying how to install it |
| System | Cancel cannot move the originals back to Input (file in use) | Nothing deleted; job stays as it was; app says "not cancelled", close the program and press Cancel again |
| System | Output or History folder not writable | Job working state unaffected; app names the folder and what to do; delivery retried with Retry |
| System | Desktop redirected to OneDrive | One-time warning on Home suggesting `Desktop\RenderAgent` be excluded from sync; all Output/History writes atomic |
| System | Network down | Calls pause and retry; queued jobs wait |
| People | Approval/review unanswered 2 working days | Never auto-approved; job stays listed on Home (reminder toasts phase 2) |
| Config | Invalid config or unknown model ID | Agent refuses to start; `doctor` names the setting |
| Config | `plans.readout_llm_default`, `renders.prompt_writers_default` or `renders.check_llm_default` names no `llm.choices` entry | CONFIG_INVALID naming the setting (28 Oct 2026, PV0) |
| Config | Wet-area tile colour outside `wet_tile.contrast_min`..`max` against white (`plan_styles.yaml`) | CONFIG_INVALID from the plan styles loader; `doctor` and Help name `wet_tile.colour` with the fix (PLAN_VISUALS_SPEC §15) |
| Memory | `memory/agent_rules.json` unreadable or invalid | Job continues without remembered rules; plain warning; Help shows "Agent memory file unreadable" with the fix (PLAN_VISUALS_SPEC §15) |
| Output | Disclaimer missing or contrast below 4.5:1 on an image or PDF page | `output/disclaimer.py` reports the file as not deliverable. Plan overlays (PV2): stamped again once, then the stage stops with DISCLAIMER_FAILED and a plain `error.json`; plans and renders from PV7 (PLAN_VISUALS_SPEC §11) |
| Plan | No floor plan page in the drawings on the plan path | `p1_trace` stops with NO_FLOOR_PLAN: plain `error.json` saying to check the PDF or start again with "Exterior renders only (no plans)" (PV2) |
| Plan | No vector walls on a floor plan page (scan, image or other drafting) | The readout model traces the walls from the image; Check the plan says so and labels the walls "traced by <model>" (PLAN_VISUALS_SPEC §3, §15) |
| Plan | Traced area differs from the drawing's area schedule by more than `areas.tolerance` (`plan_trace.yaml`, 3%) | Shown red in the area table; a Red row with a question in `plan/<dwelling>/readout.json` (PLAN_VISUALS_SPEC §15) |
| Plan | Both readout models chosen and one fails | Its reading is "not read" and the other is used; Check the plan names the failed model with "Read again with <model>", which re-reads only that model (cached readings are kept) (PLAN_VISUALS_SPEC §15) |
| Plan | A chosen readout model has no saved key | PAUSED before any call: "Your <Claude/OpenAI> key is not saved…", what to do "Add your <provider> key in Settings, then press Retry" (Practice runs need no key) (PLAN_VISUALS_SPEC §12, §15) |

## 10. Configuration, security, deployment

Config (`config.yaml` in the working root; keys in Windows Credential Manager): LLM provider/model
per stage; image provider/model; `images.enabled_providers` (first release: `openai` only; `muapi`
stays in the code, hidden); `images.max_images_per_round` (M5.5 refinement cap, checked by
`budget.py`); render image settings `images.openai.draft` (size, default 1536×864; quality `low | medium | high`, default `medium`; older files with `size`/`quality` set directly still load, and a `final` block is ignored; decision 20 Oct 2026); `pricing` (USD per 1M tokens per model id, for the token and cost report; empty = "price not set"); `images.delivery_size` (1920×1080: approved landscape renders are centre-cropped to that aspect and scaled locally with Lanczos to exactly this size, lossless sRGB PNG, no AI upscaler; drafts and job copies keep the generated size; portrait finals are delivered as generated in phase 1); `images.room_crop_margin` (0.04 of the page around a room's bbox in interior reference crops); `images.final_long_edge` is deprecated and ignored; references per render (3); planned renders per dwelling
(≤16); automatic retries after a failed drawing check (1); `paths.feedback` (feedback store); `designer.desktop` (empty = the Windows Desktop known folder) and `designer.folder` (`RenderAgent`) and `designer.completed` (`Completed`) and `designer.cancelled` (`Cancelled`); render concurrency (2); approval gate
(on); drawing-check strictness; page DPI; install root, working root and Desktop folder names.
Plan visuals (28 Oct 2026, PV0; PLAN_VISUALS_SPEC §12): `plans.readout_llm_default` (`sonnet`),
`renders.prompt_writers_default` (`[sonnet, gpt]`), `renders.check_llm_default` (`gpt`), all names
from `llm.choices`; `images.offered_providers` (`[openai, muapi]`, first = default on screen);
`paths.memory` (working root), `paths.engine` and `paths.assets` (install root). Every new setting has a default, so older `config.yaml` files load unchanged.
Settings shows these defaults in plain words; Help checks the plan engine files, the headless
browser's 3D drawing, a saved key per provider ("Check keys now" runs the no-cost checks),
the wet-tile contrast and the agent memory, each with a fix text.

Security: keys are entered on the installer page or in Settings, validated with a no-cost
authentication call (e.g. listing models, never a paid generation), and stored with `keyring` in
Windows Credential Manager; organisation-wide or individual keys both work; `.env` / environment
variables remain a developer fallback. Keys never appear in logs, UI responses or job files. The
app listens on 127.0.0.1 only; every state-changing request carries a per-session token; files are
served only from the job's own folders. Drawings leave the machine only to the configured LLM and
image provider; PDF, instruction and correction text is data, quoted into labelled prompt blocks;
only `@` directives change behaviour. **Risk accepted (15 Oct 2026):** the private `knowledge/` and
`prompts/` are bundled into the build for in-house designers; anything bundled can be extracted
from the install. External distribution would need a different design.

Deployment (decision 15 Oct 2026): the agent runs on each designer's own Windows 10/11 laptop. The
desktop app (FastAPI + uvicorn on a free 127.0.0.1 port, pywebview window on Edge WebView2, static
front end) calls `runner`, the gate, `s9_review` and `output` directly; a background worker renders
one job at a time and queues the rest; `state.json` is the single source of truth and the UI polls
(or uses SSE). It replaces `review/server.py` for designers with the same safety rules; the CLI stays
for developers. Packaging: PyInstaller (one-folder mode: fewer antivirus false alarms, faster start)
→ Inno Setup → one `Setup.exe`, handed out on a USB pen drive and run after copying it to the
laptop. The installer: key page with validation, optional `muapi-cli` component (off by default; asks
for a MUAPI key when ticked), WebView2 check/install, Desktop and Start menu shortcuts with icon,
uninstaller in Windows "Apps". Upgrades installed over the top keep config, keys, working jobs and
the Desktop folders; uninstall leaves `Desktop\RenderAgent` in place. Enabling another image provider
needs only a new `Setup.exe` run over the install plus `images.enabled_providers`. Unsigned for now
(SmartScreen warning possible); code signing is a Phase 2 decision. Help shows `doctor` results in
plain English. `scripts/install.ps1` remains a developer tool.

Stack: Python 3.12+, PyMuPDF, Pillow, openai, anthropic, jsonschema, tenacity, PyYAML,
python-dotenv, FastAPI, uvicorn, pywebview, keyring, PyInstaller, Inno Setup, muapi-cli (optional);
FFmpeg/HyperFrames phase 2 only.

## 11. Testing (see docs/MILESTONES.md for gates)

- Golden fixtures with `answer_key.yaml`; fixture 01 is the first of at least five (single detached,
  two-storey, separate subdivision, set with a 3D page), built up in M5.5.
- FakeLLM for stages 5 to 9 without keys; mock image provider for full dry runs.
- Drawing-check tests on doctored images; targeted re-render test (one flag, others byte-identical);
  model swap test on M1; fault injection for every error-table row.
- App: FastAPI TestClient with FakeLLM and mock images, end to end through the API; a job
  correction overrides the spec on regeneration and never touches `knowledge/` or `prompts/`;
  a hand-run UI checklist (`docs/M5_UI_TEST.md`).
- Install: `Setup.exe` from a pen drive on a clean Windows laptop with no Python, Git or VS Code;
  upgrade and uninstall checks (M6).
- Refinement loop (M5.5): real projects run through the app with a per-round image cap and an
  approved cost estimate; `scripts/compare_fixture.py` writes a comparison page per fixture
  (extraction grade, planned vs delivered views, agent render beside delivered render, drawing
  check, designer flags); fixes only as general rules, logged in `docs/REFINEMENT_LOG.md`; after
  each fix round extraction and planning re-run on all fixtures and affected fixtures re-render.
  Metrics: one row per fixture per round in `docs/REFINEMENT_METRICS.csv` (extraction score,
  must_not_appear hits, first-pass drawing-check and designer approval rates, regenerations, LLM and
  image cost). One verified **holdout** fixture is never used to propose fixes, runs only at the
  gate, and must score within an agreed margin of the tuned fixtures. Delivered renders are
  evaluation targets only, never sent to the image model.
- No fixture references in rules, prompts or code (`scripts/check_no_fixture_refs.py`).

## 12. Decisions (12 Oct 2026)

| Topic | Decision |
| --- | --- |
| Extraction model | `gpt-5.6-sol`, Claude Sonnet as a config swap |
| Image model | `gpt-image-2` |
| Approval gate | Always on |
| Render limit | ≤16 planned renders per dwelling; regenerations outside; a limit, not a target |
| Views | Standard view set above; night only on request |
| Materials | From the PDF; designer confirms and fills gaps |
| Subdivisions | 2+ dwellings get one streetscape first; attached share renders; separate are otherwise independent |
| Output | Common + Dwelling folders; shared renders copied into each dwelling |
| Host | ~~Windows, separate from the laptop; inbox on local disk~~ replaced 15 Oct 2026: each designer's own laptop (below) |
| After rendering | Automatic drawing check, then thumbnail review; only flagged renders regenerated |

**14 Oct 2026 (designer review of the first paid renders):** colours follow the drawings, changeable
with `@select` or at the gate; entry hallway added; rear view only with a documented deck, pergola or
similar; WIR lowest priority and never inserted; streetscape is priority 1 for 2 or more dwellings,
never for a single house (`@streetscape=false` omits it); Australian-style letterbox pillar allowed without numbers or text; drafting
symbols (window V lines, door swing lines) never drawn; no party-wall detail required.

**15 Oct 2026 (designer desktop app):**

| # | Topic | Decision |
| --- | --- | --- |
| 1 | Deployment | Runs on each designer's own Windows 10/11 laptop and runs their own jobs (replaces the separate host) |
| 2 | UI | Desktop app: FastAPI + uvicorn on 127.0.0.1, free port at start-up, native pywebview (Edge WebView2) window; static HTML/CSS/JS, no Node build, no CDN. Browser mode later |
| 3 | App and pipeline | FastAPI calls runner, gate, s9_review and output directly, never the CLI; background worker, one job rendering at a time, others queued; `state.json` is the single source of truth; UI polls or uses SSE; replaces `review/server.py` for designers with the same safety rules (localhost, per-session token on state-changing requests, files only from the job's folders); CLI stays for developers |
| 4 | Paths | Program in `C:\Program Files\RenderAgent` (read-only: code, knowledge, prompts, schemas); working files in `%LOCALAPPDATA%\RenderAgent`; `config.py` resolves install and working roots separately; dev mode keeps the repo layout; no hard-coded drive letters or user names |
| 5 | Designer folders | `Desktop\RenderAgent\Input`, `Output`, `History` (known-folder API); Start copies, verifies by sha256, moves originals to dated History with `started.txt`; Output has Common / Dwelling N with Draft, Draft/draft-final and FINAL APPROVED, and report.html; finished jobs move to Output\Completed (21 Oct 2026); Retry button resumes; OneDrive warning; atomic writes (§8) |
| 6 | API keys | Entered on the installer page and validated with a no-cost call; also checked at first run; organisation or individual keys; Windows Credential Manager via `keyring`; `.env` developer fallback; no job without a valid key per enabled provider; Settings replaces and re-tests; never in logs, UI responses or job files |
| 7 | Bundled rules | Private `knowledge/` and `prompts/` bundled for in-house use; risk: extractable from the install; external distribution needs a different design |
| 8 | Packaging | PyInstaller one-folder → Inno Setup → one `Setup.exe` on a USB pen drive; shortcuts, icon, uninstaller, upgrade over the top keeps config, keys, jobs and Desktop folders; WebView2 check/install; unsigned for now, code signing Phase 2 |
| 9 | Image providers | Switchable without code changes; first release enables OpenAI only; muapi hidden by `images.enabled_providers`; enabling it later = new `Setup.exe` over the install (optional muapi-cli component + MUAPI key page) + the flag; Settings shows a provider only when enabled with a valid key |
| 10 | Spec sync | `docs/SPEC.md` starts with "Last synced with the live page: <date>"; at every exit gate Claude Code lists the SPEC sections changed and asks for confirmation; date updated only after confirmation; gate not passed until confirmed |
| 11 | Git | Claude Code never runs state-changing git commands (public repo or `private/`); each task ends with the changed files grouped "public repo" / "private submodule (private/)"; enforced by deny rules in `.claude/settings.json` |
| 12 | Corrections and learning | Models do not learn by themselves. **This render:** flag category + correction in the designer's words, added to that render's prompt (FR-13). **This job:** facts corrected at the gate or in review go to `corrections.json`, override the House Specification for every later render and re-run of the job, shown in `report.html`; never change `knowledge/` or `prompts/`. **All future jobs:** every flag and correction appended to `%LOCALAPPDATA%\RenderAgent\feedback\feedback.jsonl` (job ID, view, category, correction text, "suggest as a general rule" tick); Help → "Export feedback" writes a zip (text and thumbnails only, no keys, no drawings) to `Desktop\RenderAgent` for return on a pen drive; `scripts/review_feedback.py`, run by a developer with Claude Code, groups feedback and proposes general rules; only owner-approved rules are applied, followed by the M5.5 regression, and reach designers in the next `Setup.exe`. Correction text is data and cannot change directives, limits or providers (FR-19) |
