# Render Agent

Drop an architectural drawing PDF into a folder; get back photorealistic renders that have been
checked against the drawings and reviewed by a designer, filed by dwelling.

- What it does and why: `docs/SPEC.md` (agreed 12 Oct 2026)
- Build order: `docs/MILESTONES.md` (start at M0)
- Working with Claude Code: `CLAUDE.md`

Status: **scaffold**. Schemas, prompts, knowledge rules, fixtures, tests and the package layout
are in place; stages are implemented milestone by milestone.

## For designers: install

1. Copy `Setup.exe` from the USB pen drive to your laptop (for example to Downloads), then run it
   from there. If Windows shows a blue "Windows protected your PC" box, choose **More info → Run
   anyway** (the program is not code-signed yet).
2. On the key page, enter the API key you were given. The installer checks it for free before it
   continues; installation only finishes with a working key.
3. Open **Render Agent** from the desktop icon or the Start menu.
4. Your folders are on the Desktop in `RenderAgent`:
   - **Input**: put a drawing PDF (or a folder of PDFs with `instructions.txt`) here, or drag it
     onto the app window. Press Refresh, then Start.
   - **History**: after Start, the original files move here, in a folder named with the date and
     time, with a `started.txt` saying who started it and where the renders are.
   - **Output**: one folder per job. `Draft` has every version with a DRAFT mark; `Final` has the
     approved images by dwelling; `report.html` summarises the job.
5. To replace a key later, use Settings in the app. To update, run the new `Setup.exe` the same way;
   your keys, jobs and folders are kept. Uninstalling leaves `Desktop\RenderAgent` in place.

If your Desktop syncs to OneDrive, the app will suggest excluding `Desktop\RenderAgent` from sync.

## Private files (not in the repository)

The prompts (`private/prompts/`), the rules (`private/knowledge/`), the original workflow (`private/reference/`) and the
client drawing sets with their delivered renders (`private/fixtures/<name>/`) are private and listed in
`.gitignore`. `redacted/` holds a structure-only copy of the prompts and rules (headings,
`{{PLACEHOLDERS}}` and YAML keys; no wording or values), regenerated with
`python scripts/make_redacted.py`. To run the agent or the tests, restore the private folders from
the private store; without them `pytest` stops with a message saying what is missing.

## Open in VS Code with Claude Code

1. Unzip this folder on the development laptop and open it in VS Code (**File → Open Folder**).
2. Accept the recommended extensions (Python, Ruff, Claude Code, YAML, PDF viewer).
3. Create the environment (below), then open the Claude Code panel and run `/next-milestone`.
   Claude Code reads `CLAUDE.md` automatically and works through M0 first.

Project slash commands (in `.claude/commands/`):

| Command | What it does |
| --- | --- |
| `/next-milestone` | Implements the next unfinished milestone, then stops at its exit gate with a report |
| `/check-against-spec` | Lists every gap between the code and `docs/SPEC.md`; changes nothing |
| `/add-fixture <folder>` | Drafts `answer_key.yaml` for a new drawing set in `tests/fixtures/<folder>/` |

## Set up the laptop

bash (macOS, Linux, Git Bash):

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.yaml config.yaml
cp .env.example .env          # then add your API keys
pytest
python -m render_agent doctor
```

PowerShell (Windows):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item config.example.yaml config.yaml
Copy-Item .env.example .env   # then add your API keys
pytest
python -m render_agent doctor
```

If PowerShell blocks the activation script, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

`.vscode/settings.json` points VS Code at `.venv\Scripts\python.exe` (Windows). On macOS or Linux,
pick `.venv/bin/python` with **Python: Select Interpreter**.

## Developer: build the installer (from M6)

```powershell
.\scripts\build_installer.ps1   # PyInstaller folder + Inno Setup Setup.exe, version from pyproject.toml
```

`scripts/install.ps1` (venv, pinned dependencies, config and folders, doctor) stays as a developer
check; designers get the M6 `Setup.exe` instead. Launch the desktop app from the repo with
`python -m render_agent app` (from M5).

## Using it (developers, CLI)

1. Run `python -m render_agent run <pdf or folder>` (or `--batch <folder>`). Optional: an
   `instructions.txt` beside the PDF, for example:
   ```
   @night=true
   Include a family on the driveway in the front view.
   ```
2. Approve the plan and materials (`approve` command, or drop a file named
   `APPROVE` into the job folder).
3. Review the thumbnails; flag any render with a note to regenerate only that one.
4. Collect the renders from `jobs\<job>\output\Dwelling N\` (each folder is a complete set).

Renders are conceptual visualisations. Detailed compliance must be confirmed by a registered
architect, building designer or certifier.

## Folder map

```
CLAUDE.md               instructions Claude Code follows in this repo
docs/                   SPEC.md, MILESTONES.md, LOCKFILE.md, reference/n8n (original workflow)
knowledge/              runtime rules: view_set.yaml, materials_library.yaml, defaults, files 03-05
schemas/                strict JSON Schemas for each LLM stage
prompts/                prompt templates for stages 2-5 (stage 6 and 8 prompts are added in M2-M3)
src/render_agent/       the package (cli, config, pipeline, llm, images, validate, review, output)
tests/                  unit + integration tests, FakeLLM, fixtures with answer keys
scripts/                install.ps1, check_no_fixture_refs.py
.vscode/                settings, launch configs (doctor, dry run, watch), tasks
.claude/                Claude Code permissions and slash commands
```
