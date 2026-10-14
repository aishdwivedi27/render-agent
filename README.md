# Render Agent

Drop an architectural drawing PDF into a folder; get back photorealistic renders that have been
checked against the drawings and reviewed by a designer, filed by dwelling.

- What it does and why: `docs/SPEC.md` (agreed 12 Oct 2026)
- Build order: `docs/MILESTONES.md` (start at M0)
- Working with Claude Code: `CLAUDE.md`

Status: **scaffold**. Schemas, prompts, knowledge rules, fixtures, tests and the package layout
are in place; stages are implemented milestone by milestone.

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

## Install on the Windows host (from M4)

```powershell
git clone <repo> C:\RenderAgent    # or copy the release zip there
cd C:\RenderAgent
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
notepad .env                        # add API keys
```

`install.ps1` creates the venv, installs pinned dependencies, creates `config.yaml` and the
`inbox`, `jobs`, `failed` and `logs` folders, registers the `RenderAgent` Scheduled Task (start at
logon, restart on failure) and runs `doctor`. Re-run it to upgrade; jobs, `config.yaml` and
`.env` are never overwritten.

## Using it

1. Drop a PDF (or a folder of PDFs) into `inbox\`. Optional: an `instructions.txt` beside it,
   for example:
   ```
   @night=true
   Include a family on the driveway in the front view.
   ```
2. Approve the plan and materials when notified (local review page, or drop a file named
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
