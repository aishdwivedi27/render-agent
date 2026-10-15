# Instruction file guide

How to steer a job, and what the agent does when you don't. Kept in step with the code: a test
(`tests/unit/test_instructions_doc.py`) fails if a directive, material item or view is missing here.

## How to use it

**Designers (desktop app, from M5):** put the PDF (with its `.txt` or in a folder with
`instructions.txt` / `selections.yaml`) into `Desktop\RenderAgent\Input`, or drag it onto the app
window. On the Home screen press Refresh, then **Start**. Every `@directive` below is also an
option on the Start step, so you can set night view, streetscape, materials and so on there
instead of writing a file. After Start the original moves to `Desktop\RenderAgent\History` and the
renders arrive in `Desktop\RenderAgent\Output\<input name>`. To retry a failed or flagged job, press
Retry in the app; never put the files back in Input.

**Developers (CLI):** start jobs by hand from the repo:

```text
python -m render_agent run "drawings\house_a.pdf"                 one job
python -m render_agent run house_a.pdf house_b.pdf "set_c"         three jobs
python -m render_agent run --batch drawings                        one job per PDF and per sub-folder
python -m render_agent run --batch drawings --dry-run              same, no paid calls
```

- A PDF is one job; an instruction file with the same name next to it (`house_b.pdf` +
  `house_b.txt`) goes with it.
- A folder is one drawing set: all its PDFs, `.txt` files and `selections.yaml` form one job.
- A `<name>.selections.yaml` next to a PDF also goes with it (see "Material choices in a file").
- Every job gets its own folder in `jobs\` and its own output; one failing never stops the others,
  and a batch ends with a summary table.
- PDFs that were already processed are skipped with a pointer to the existing job; add `--force` to
  run them again.
- Each job stops at the approval gate: `python -m render_agent approve jobs\<job-id>`.


A job can be given a plain-text instruction file such as `instructions.txt` (in the drawing
set's folder, or `<name>.txt` next to the PDF). A PDF on its own runs with every default below.

- Lines starting with `@` are **directives**: they change what the agent does.
- Every other line is **free text**: passed to the planning and prompt models as presentation
  preferences (time of day, styling, people). Free text can never change documented architecture.
- An unknown or malformed directive is ignored and listed at the approval gate.
- Everything is still shown at the approval gate, where the designer can change it before any
  image is paid for.

```text
@streetscape=false
@night=true
@select roof_colour=Monument
@select window_frames=Aluminium, black
@select entry_door=pivot, timber
@max_images=10
Late-afternoon light, no people or cars.
```

## Sample files to copy

Ready-to-use samples are in `docs/examples/` (a test checks every one is valid):

| File | For |
| --- | --- |
| `instructions_subdivision.txt` | 2-3 attached dwellings: client colour palette, night view, lower limit |
| `instructions_single_house.txt` | One house: chosen cladding and door, only four views |
| `instructions_minimal.txt` | Defaults except no streetscape |

What each line of `instructions_subdivision.txt` does:

```text
@select roof_colour=Monument              roof Monument instead of the scheduled colour (logged override)
@select window_frames=Aluminium, black    black frames instead of the scheduled colour
@select entry_door=hinged, painted        door type "hinged", door material "painted"
@select garage_door_finish=Monument       garage door colour
@night=true                               adds the front night view
@max_images=12                            at most 12 planned renders per dwelling (default 16)
Late-afternoon summer light ...           free text: lighting preference
No people or cars ...                     free text: overrides the default of 0-2 people, one car
Interiors bright and minimal ...          free text: interior styling
```

(The right-hand notes are explanations only; don't copy them into your file. Every line in the file is
either a directive or free text, there is no comment syntax.)

Tips:
- One directive per line, starting with `@` at the beginning of the line.
- Use the item keys exactly as in the materials table (`roof_colour`, not `roof color`).
- Free text describes presentation only. "Add a balcony" is ignored as architecture; change the
  drawings instead.
- Words such as "night view" in free text turn the night view on; write `@night=false` to be sure.

## Directives

| Directive | Example | Effect | Default without it |
| --- | --- | --- | --- |
| `@night` | `@night=true` | Adds the front night view. Plain words "night view", "night render" or "night shot" also work; `@night=false` refuses even if the words appear | No night view |
| `@streetscape` | `@streetscape=false` | Omits the streetscape | Streetscape included, priority 1, when there are 2 or more dwellings; never for a single house |
| `@select` | `@select roof_colour=Monument` | Sets a material or colour (see the table below). Overrides the drawing's value; every override is logged and shown at the gate | Values from the drawings' colour schedule and notes |
| `@max_images` | `@max_images=8` | Lowers the planned renders per dwelling. Cannot raise it | 16 per dwelling (a limit, not a target) |
| `@views` | `@views=Kitchen,Living` | Only these views (the front view is always kept) | Full standard view set |
| `@dwellings` | `@dwellings=DWELLING 1` | Only views that show these dwellings (a streetscape is not added automatically, but one already planned is kept because it shows them too) | All dwellings |
| `@provider` | `@provider=muapi` | Image provider for this job: `openai`, `muapi` or `mock`, only if listed in `images.enabled_providers` (a provider not enabled is an invalid directive, listed at the gate) | `images.provider` in config.yaml (openai, gpt-image-2) |
| `@llm` | `@llm=sonnet` | Language model for this whole job: a name from `llm.choices` in config.yaml (`gpt`, `sonnet`). An unknown name is ignored and listed at the gate | `llm` in config.yaml (gpt-5.6-sol) |
| `@dry_run` | `@dry_run=true` | Mock images, no drawing check, no spend | Real (paid) run after approval |

True/false values accept `true/false`, `yes/no`, `on/off`, `1/0`.

## Materials and colours (`@select`)

Default: whatever the drawings document (colour schedule and notes). An item the drawings don't
document takes the **standard default** in the table, labelled "standard default, not in the drawings"
in the prompt and at the gate. So a PDF with no instruction file runs with the drawing's colours and
needs only a plain approval. `@select` (or the gate) replaces either value; every change is logged.

Every job folder gets **`MATERIALS_AND_DEFAULTS.txt`**: whether the PDF has a colour schedule (and the
lines read from it), then each item grouped as *from the drawings*, *standard defaults used*,
*chosen by the designer* and *still to choose*. It is rewritten whenever a choice changes at the gate.

| Item key | What it sets | Standard default (drawings silent) | Other options offered |
| --- | --- | --- | --- |
| `facade_wall` | Main wall material/finish | Hebel panel, painted render finish | rendered brick; face brick; fibre cement; timber; natural stone feature |
| `feature_cladding` | Feature cladding | None | vertical timber-look (Linea/Stria); horizontal weatherboard; stone feature wall |
| `entry_door_type` | Entry door type | Hinged, solid | hinged with sidelight; pivot |
| `entry_door_material` | Entry door material/colour (reads the schedule's ENTRY DOOR line) | Painted | timber; timber-look aluminium |
| `garage_door_type` | Garage door type | Panel-lift (sectional) | roller |
| `garage_door_finish` | Garage door finish/colour | Colorbond, Monument | timber-look |
| `window_frames` | Window frame material/colour | Aluminium, black | aluminium white |
| `roof_profile` | Roof sheeting/tile profile | Colorbond corrugated | standing seam; concrete tile; terracotta tile |
| `roof_colour` | Roof colour | Monument | Woodland Grey; Basalt; Surfmist |

Shortcut: `@select entry_door=pivot, timber` fills `entry_door_type` then `entry_door_material`
(the same works for `garage_door=...` and `roof=...`). Any value may be typed, not only the listed options.
The list lives in `private/knowledge/materials_library.yaml`.

A COLORBOND colour name (from the schedule, a default or your choice) is rendered as the real product
colour: the prompt carries its tone, e.g. "Woodland Grey: dark grey with a green undertone"
(`private/knowledge/colour_swatches.yaml`).

### Material choices in a file (`selections.yaml`)

Instead of `@select` lines you can put the choices in `selections.yaml` (in the drawing set's folder)
or `<name>.selections.yaml` (next to a PDF), one `item: value` per line, same keys and shortcuts:

```yaml
roof_colour: Monument
window_frames: Aluminium, black
entry_door: pivot, timber
```

If both the file and an `@select` line set the same item, the `@select` line wins. Lines that are not
`item: value` are ignored and listed at the gate.

## Views planned without an instruction file

Priority order (when the limit forces a cut, the bottom goes first). A view is planned only when the
drawings document that space.

| # | View | Included when |
| --- | --- | --- |
| 1 | `streetscape`: all dwellings side by side | 2 or more dwellings; never trimmed |
| 2 | `front_day`: the dwelling's own front | Always (inserted as NOT READY if it can't be planned) |
| 3 | `rear_day` | A deck, pergola, alfresco, verandah or patio is documented for that dwelling |
| 4 | `main_bedroom` | The bedroom the WIR or ensuite opens from |
| 5 | `secondary_bedroom` | One representative bedroom, however many the plan has |
| 6 | `ensuite` | Documented |
| 7 | `common_bath` | Documented |
| 8 | `laundry` | On the floor plan |
| 9 | `kitchen` | Documented; shows the pantry if visible (a pantry never gets its own render) |
| 10 | `living`: family and meals | Documented |
| 11 | `hallway`: entry hall | Documented; from inside the open front door towards the room beyond |
| 12 | `study` | Documented |
| 13 | `wir`: walk-in robe | Documented and the dwelling is under its limit (cut first, never inserted) |
| 14 | `front_night` | Only with `@night=true` or the night words |

Attached dwellings that look the same (including mirrored) share one render of a view; separate
dwellings each get their own set (the streetscape still shows them together).

## Other defaults (no instruction file)

| Topic | Default | Changed by |
| --- | --- | --- |
| Approval gate | Always on: nothing paid until the plan and materials are approved (a plain approve is enough when nothing is missing) | (cannot be turned off) |
| Out of API credit | Job pauses (OUT_OF_CREDIT) saying which provider and where to top up; finished work kept; `resume` continues | top up, then `python -m render_agent resume jobs\<job-id>` |
| Image size / quality | 1536x1024, high | config.yaml `images.openai` |
| References per render | Up to 3 drawing crops | config.yaml `images.references_per_render` |
| Drawing check | On; a failed render is regenerated once with fix instructions | config.yaml `drawing_check`, `planning.auto_retries_after_failed_check` |
| DRAFT overlay | On every render until approved at review; clean copy exported on approval | (always) |
| Landscaping | As the site plan shows; if silent, low native planting and lawn | free text |
| Letterbox | Australian-style rendered pillar allowed where the site plan is silent; never numbers or text | free text ("no letterbox") |
| People / vehicles | Exterior 0-2 people, at most one car, never hiding the façade; interiors none | free text |
| Lighting | Clear Australian daylight | free text, `@night=true` |
| Drafting symbols | Window V lines, door swing lines, dimensions never drawn | (always) |
| Text in images | None (no numbers, labels, logos) | (always) |

Defaults for styling live in `private/knowledge/visualisation_defaults.md`; global image rules in
`private/prompts/render_rules.md`.

## Reviewing renders

**In the app (designers):** the Review screen shows each render beside its drawing and check
result. To flag one, pick what is wrong (wrong material/colour, extra opening, missing opening,
wrong roof/storeys, wrong framing/view, other) and say the correction in your own words; only that
render is redone with your correction. If a fact about the house itself is wrong (at the approval
step or in review), correct it once: it is saved for this job (`corrections.json`), used for every
later render of the job and listed in `report.html`. Corrections never change the agent's general
rules; tick "suggest as a general rule" if you think it should, and use Help → "Export feedback"
when asked to hand feedback back on a pen drive.

**Developers:**

When the renders are ready the job waits at AWAITING_REVIEW. Every image carries a DRAFT overlay.
For each render: **approve** it (locked, then delivered clean to the `Common` and `Dwelling N`
folders) or **flag** it with a note (only that render is regenerated with the note, re-checked and
shown again as a new version; older versions are kept).

- Review page: `python -m render_agent review jobs\<job-id> --serve` opens a page on this computer
  only (127.0.0.1) with each render beside its drawing and its drawing-check result, Approve and Flag
  buttons, and **Continue** (regenerates flagged renders and delivers approved ones).
- Command line: `python -m render_agent review jobs\<job-id>` lists the renders;
  `--approve <id>` or `--approve all`, and `--flag <id>="what to change"`.
- Or put `review.txt` in the job folder and resume the job:

  ```text
  P1-SHARED-KITCHEN: make the benchtop darker stone
  approve all
  ```

  One `<render_id>: <note>` line per flag; `approve all` approves every other render waiting for
  review. The file is applied once and renamed `review.applied-<time>.txt`.
- The job is SUCCEEDED when every render is approved. `report.html` and `manifest.json` in the job
  folder summarise what was delivered.

## Enabling another image provider

The first release offers OpenAI images only. MuAPI is in the program but hidden. To turn it on for a
laptop (no reinstall from scratch, no code change):

1. Run the new `Setup.exe` over the existing install (copy it off the pen drive first).
2. On the components page, tick **muapi-cli** (it is off by default).
3. On the key page, enter the MUAPI key; the installer tests it with a free check.
4. Add `muapi` to `images.enabled_providers` in `config.yaml` (in `%LOCALAPPDATA%\RenderAgent`).
5. Open the app: Settings now shows MuAPI with its key status, and the Start step offers it as the
   image provider. A provider appears only when it is enabled and has a valid key.

Config, keys, jobs and the Desktop folders are kept by the upgrade.

## Not built yet (deferred, 14 Oct 2026)

- Automatic start from Input (`watch`): phase 2. Designers press Start in the app; developers use `run`.
- Windows notifications and the 2-working-day reminder: phase 2. Job status, errors and
  `report.html` are shown in the app (developers: terminal output and `jobs\<job-id>\error.json`).
