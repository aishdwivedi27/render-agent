# Drawing-to-Render Agent: Specification (developer copy)

Agreed 12 Oct 2026. The live, authoritative version is the Claude Docs page
https://claude.ai/code/artifact/29555f11-8ad7-4134-838e-0d2553f96142. If they differ, the live page
wins; update this copy and say so in the commit message.

## 1. Purpose and scope

The agent turns a drawing set into a consistent set of photorealistic renders. Someone drops a
Revit-exported PDF (optionally with an instructions `.txt`) into a watched folder on a Windows host.
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
| Building designer | Visual check before issue; resolves gaps | Approves the plan, reviews thumbnails |
| Sales / real-estate agent | Front hero images (night on request), later video | Receives a dwelling's folder |
| Operator | Cost control, failure visibility, prompt tuning | Edits config and knowledge files, reads logs |

Journey: drop PDF (+ optional `instructions.txt` / `selections.yaml`) into `inbox/` → agent reads
the drawings, plans views from the standard view set, reads materials from the PDF → **approval
gate (always on)**: designer confirms views and materials and fills gaps → render → drawing check
(one automatic regeneration on fail) → **thumbnail review**: designer approves or flags individual
renders with a note; only flagged renders are regenerated → final set filed by dwelling.
A failed job lands in `failed/` with a plain-English `error.json`.

## 3. Functional requirements

| ID | Requirement |
| --- | --- |
| FR-1 | Watch a folder on the host's local disk; accept a loose PDF or a sub-folder of PDFs (2D set plus optional 3D views) |
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
| FR-13 | Thumbnail review: approve or flag each render with a note; only flagged renders are regenerated, with the note added to the prompt |
| FR-14 | At most 16 planned renders per dwelling: a limit, not a target. Regenerations are outside the 16 and tracked separately |
| FR-15 | Run on a Windows host separate from the development laptop |

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

**Draft overlay** (decision 13 Oct 2026): every render is shown and delivered with a visible "DRAFT"
overlay until the designer approves it at review. The clean image is kept only inside the job folder
(`output/clean/`) and is never copied to the dwelling folders while unapproved. Approving a render
copies the clean version out without the overlay; uncertain details are dealt with at review, not
marked on the image.

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
A single-house job has no `Common` folder.

## 5. Knowledge base and precedence

Rules are converted into prompt text (`prompts/`), JSON Schemas (`schemas/`) and code checks
(`validate/`). Code checks matter most. Precedence: drawings → designer gate decisions → instruction
file (presentation only) → `knowledge/visualisation_defaults.md`. Each job records knowledge-file
hashes in `manifest.json`. See `knowledge/README.md`.

## 6. Pipeline

| # | Stage | Output | Engine |
| --- | --- | --- | --- |
| 1 | Ingest | `pages/page-N.png` (DPI from config), `pages/page-N.txt`, `ingest.json` | PyMuPDF |
| 2 | Sheet analysis | `sheets/sheet-N.json` (sheet type, views with normalised bboxes, findings) | Vision LLM, one call per page, image + text layer |
| 3 | Evidence | `evidence.json` | LLM, `schemas/evidence.json` |
| 4 | House spec | `house_specification.json` | LLM, `schemas/house_spec.json` |
| 5 | Checklist + selections | `checklist.json`, `checklist_validation.json`, `selections.json` | LLM then deterministic validator |
| Gate | Approval (always on) | `approval.json` | Designer (page, `APPROVE` file or CLI) |
| 6 | Prompts | `prompts/<render_id>.txt`, `references/<view_id>.png` | LLM scene text; code assembles |
| 7 | Render | `output/<render_id>_vN.png` | gpt-image-2 (default) or MuAPI |
| 8 | Drawing check | `checks/<render_id>_vN.json` | Vision LLM, `schemas/drawing_check.json` |
| 9 | Review and report | `report.html`, `manifest.json`, `review.json`, dwelling folders | Local page on 127.0.0.1 |

**Prompt assembly (stage 6), in order:** fixed identity block from the spec (construction, roof and
pitch, eaves, storeys, every colour-schedule line; identical in every prompt for that dwelling) →
reference legend ("Image 1 is Elevation 1 (street front), sheet 3") → scene/camera/lighting from the
LLM → must-preserve and must-avoid lists plus global rules → visualisation choices labelled as
undocumented.

**Materials (stage 5):** `selections.json` holds one entry per item (façade wall, feature cladding,
entry door type and material, garage door type and finish, window frames, roof profile and colour)
with the PDF value and source sheet or NOT FOUND. The gate shows NOT FOUND items a planned view will
show, with options from `knowledge/materials_library.yaml`. Changing a PDF value is a logged
designer override.

**Drawing check (stage 8):** input = render + same reference crops + the opening list for that face
from the spec. Hard fail: extra or missing window/door/garage door on a visible face, wrong storeys
or roof form, visibly wrong schedule colour. Tolerated: opening sizes, exact proportions,
landscaping, people, vehicles, sky. Fail → one automatic regeneration with fix instructions; a
second fail goes to review marked "Failed drawing check". Error or unsure → "Not checked".

**Designer review (stage 9):** thumbnails grouped by dwelling beside their reference crop and check
result; Approve / Flag + note; "Regenerate flagged" re-runs stages 6 to 8 for flagged IDs only;
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

```
RenderAgent/      install root on the Windows host's local disk
  inbox/          drop a PDF, or a folder of PDFs + instructions.txt / selections.yaml
  jobs/<id>/      input/ pages/ sheets/ references/ prompts/ checks/
                  output/  Common/  Dwelling 1/ ...
                  state.json  selections.json  approval.json  review.json  job.log
  failed/         jobs that stopped, each with error.json
  knowledge/      runtime rules
  config.yaml  .env
```

A file is claimed only after size and mtime are unchanged for `stable_seconds` (default 10);
locked files are skipped until the next poll.

| Directive | Example | Effect |
| --- | --- | --- |
| `@night` | `@night=true` | Adds the optional front night view (plain words work too) |
| `@streetscape` | `@streetscape=false` | Omits the streetscape (included by default for 2+ dwellings) |
| `@provider` | `@provider=muapi` | Image provider for this job |
| `@llm` | `@llm=sonnet` | Language model for this job |
| `@dwellings` | `@dwellings=DWELLING 1` | Only these dwellings |
| `@views` | `@views=Front day,Rear day` | Only these view types |
| `@max_images` | `@max_images=6` | Lowers the per-dwelling limit; cannot raise it |
| `@select` | `@select entry_door=pivot, spotted gum` | Pre-sets a material selection (still shown at the gate) |
| `@dry_run` | `@dry_run=true` | Mock provider throughout |

Statuses: AWAITING_APPROVAL, RENDERING, AWAITING_REVIEW, SUCCEEDED (every render approved), FAILED.

## 9. Error handling

Rules: (1) fail small: call → render → stage → job, never the watcher; (2) never lose work: atomic
writes, versioned renders; (3) resume, don't restart: idempotent checkpointed stages; (4) no silent
failures: every error logged with a code, job-stopping errors write `error.json` (what happened,
likely cause, what to do); (5) tell a person: Windows notification on approval needed, review ready,
failure; (6) retry only what can succeed; (7) spend safely: budget check before every paid call;
(8) validate everything that crosses a boundary.

| Area | Failure | Behaviour |
| --- | --- | --- |
| Input | Not a PDF, empty, won't open | `failed/` + error.json "not a readable PDF" |
| Input | Password-protected | `failed/`; ask for an unlocked copy |
| Input | Scan with no text layer | Continue on images only; lower confidence; said at the gate |
| Input | Still copying / locked | Skip until stable; retry every poll |
| Input | Same PDF dropped twice | File hash; link to the existing job, no reprocessing |
| Input | No elevation or no floor plan | Affected views NOT READY with reason; job continues |
| Input | Unknown/invalid directive | Ignored, listed at the gate; free text still used |
| Extraction | Invalid JSON / schema break | One repair retry quoting the error; then stage fails, raw output saved |
| Extraction | Refusal or truncation | Stage fails with the reason; no retry loop |
| Extraction | Drawings contradict | Both values kept and flagged at the gate |
| Extraction | Invalid/empty bbox | Full sheet image used for that view; noted |
| Planning | > 16 renders for a dwelling, or counts wrong | Validator corrects by priority; each correction logged |
| Planning | Required front view missing | Validator inserts it as NOT READY with a blocker |
| Rendering | Rate limit, timeout, 5xx, network | Backoff, 5 attempts; then that render fails, others continue |
| Rendering | Content-policy / bad request | No retry; prompt and reason shown at review |
| Rendering | No image or unreadable image | Failed attempt; retried once |
| Rendering | Fails drawing check | One automatic regeneration; second fail → review "Failed drawing check" |
| Rendering | Drawing check errors or unsure | Review "Not checked"; never silently passed |
| Review | Designer flags a render | Only that render regenerated; counted and costed |
| Account | Key missing/invalid/expired | Job PAUSES with "update the key in .env"; resumes when fixed |
| Account | Out of credit / quota / spending limit (LLM or images; e.g. `insufficient_quota`, "credit balance is too low", HTTP 402) | Never retried. Job PAUSES with code OUT_OF_CREDIT: which provider, the provider's message, where to top up, and the resume command; finished stages and renders kept and never paid for twice |
| System | Disk below 2 GB free | No new jobs; running jobs pause; notification |
| System | Crash, reboot, power loss | Scheduled Task restarts the agent; jobs resume |
| System | Second agent instance | Lock file stops it |
| System | Network down | Calls pause and retry; watcher keeps queueing |
| People | Approval/review unanswered 2 working days | Reminder; never auto-approved |
| Config | Invalid config or unknown model ID | Agent refuses to start; `doctor` names the setting |

## 10. Configuration, security, deployment

Config (`config.yaml`, secrets in `.env`): LLM provider/model per stage; image provider/model;
image size and quality; references per render (3); planned renders per dwelling (≤16);
automatic retries after a failed drawing check (1); render concurrency (2); approval gate (on);
drawing-check strictness; poll interval; page DPI; install and inbox paths.

Security: keys only from `.env`/environment, never logged; drawings leave the machine only to the
configured LLM and image provider; PDF and instruction text is data, quoted into labelled prompt
blocks; only `@` directives change behaviour.

Windows deployment: built on the laptop, run on a separate Windows host; all paths relative to the
install root. `scripts/install.ps1` creates the venv, installs pinned dependencies, installs
`muapi-cli`, creates `config.yaml` if missing, registers a Scheduled Task (start at logon, restart on
failure). `doctor` checks Python, keys, folder permissions, provider reachability, free disk.
Upgrades: `git pull` or release zip, then `install.ps1`; jobs, `config.yaml`, `.env` untouched.

Stack: Python 3.12+, PyMuPDF, Pillow, openai, anthropic, jsonschema, tenacity, PyYAML,
python-dotenv, muapi-cli; review page on the standard-library HTTP server; FFmpeg/HyperFrames
phase 2 only.

## 11. Testing (see docs/MILESTONES.md for gates)

- Golden fixtures with `answer_key.yaml`; fixture 01 is the first of at least five (single detached,
  two-storey, separate subdivision, set with a 3D page).
- FakeLLM for stages 5 to 9 without keys; mock image provider for full dry runs.
- Drawing-check tests on doctored images; targeted re-render test (one flag, others byte-identical);
  model swap test on M1; host install test; fault injection for every error-table row.
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
| Host | Windows, separate from the laptop; inbox on local disk |
| After rendering | Automatic drawing check, then thumbnail review; only flagged renders regenerated |

**14 Oct 2026 (designer review of the first paid renders):** colours follow the drawings, changeable
with `@select` or at the gate; entry hallway added; rear view only with a documented deck, pergola or
similar; WIR lowest priority and never inserted; streetscape is priority 1 for 2 or more dwellings,
never for a single house (`@streetscape=false` omits it); Australian-style letterbox pillar allowed without numbers or text; drafting
symbols (window V lines, door swing lines) never drawn; no party-wall detail required.
