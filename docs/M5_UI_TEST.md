# M5 front-end test (exit gate part b)

Done by the owner by hand on the developer laptop, in the app window. After the launch command,
use no terminal. Tick each line and write anything confusing in the Notes column, or as a numbered
item in `docs/M5_UI_FEEDBACK.md`.

**No spend:** tick **Practice run** in Start options for every job in this test. A practice run
reads the drawings offline and makes placeholder images, so nothing is paid for. The only network
call in this checklist is **Test connection** in Settings, which lists models and costs nothing.

## Before you start

- Launch (from the repo, once): `python -m render_agent app`
- Have the fixture PDF ready (any drawing-set PDF works).
- Make a password-protected copy for step 11. This one-off command is run before you begin and is
  not part of the test:
  `python -c "import pymupdf,sys; d=pymupdf.open(sys.argv[1]); d.save(sys.argv[2], encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='x', owner_pw='x')" "<fixture>.pdf" "Locked set.pdf"`

## Checklist

| # | Step | Expected | ✓ | Notes |
| --- | --- | --- | --- | --- |
| 1 | The window opens. Close it and launch again while it is open | One window only; the second launch brings the first to the front | | |
| 2 | **Keys** (side rail) → paste your OpenAI key → **Test connection** | "The openai key works." No key is shown anywhere afterwards | | |
| 3 | **Save keys** | Pill changes to "Key saved"; Home has no key warning | | |
| 4 | Keys shows only OpenAI (no MuAPI) | MuAPI is not offered anywhere (Keys, Start options) | | |
| 5 | Put the fixture PDF in `Desktop\RenderAgent\Input` (or drag it onto the window) → **Refresh** | It is listed as "Ready" (a few seconds after copying; before that it says "still copying") | | |
| 6 | Open **Start options**, tick **Practice run** and **Add a night view of the front**, then press **Start** | The job opens. The PDF is gone from Input | | |
| 7 | Open `Desktop\RenderAgent\History` | A folder `<YYYY-MM-DD HHMM> - <name>` with the PDF and `started.txt` (date and time in words, your Windows user name, the full Output folder path, the job ID) | | |
| 8 | Approval screen: read the image count, fill one empty material box (a "Not in the drawings" item), add one **Correct a fact** row → **Approve and start rendering** | Approval is accepted. If a required item is empty, it is named in a red notice and nothing starts | | |
| 9 | Watch the progress screen | Heading "Making your renders"; the step strip shows step 3; the bar and "12 of 14 renders" count move | | |
| 10 | Review: flag one render (category + a note, tick "Also use this for the rest of this job"), approve the others, then press **Continue: redo 1 flagged** | The flagged card shows a red dashed cloud. After Continue only that render comes back, as a new version waiting for you | | |
| 11 | Approve the new version → **Finish** | Results screen says "Finished" | | |
| 12 | Check `Desktop\RenderAgent\Output\<name>` | `Draft\` holds every version with the DRAFT mark (`_v1`, `_v2`); `Final\` holds approved clean images in `Common\` / `Dwelling N\`; `report.html` opens and shows thumbnails | | |
| 13 | Results → **Open Output folder**, then **Open History folder** | Explorer opens each folder | | |
| 14 | Put `Locked set.pdf` in Input → Practice run → **Start** | Status "Something went wrong" with a plain message asking for an unlocked copy; no technical text | | |
| 15 | Start the fixture again (copy it back into Input) | "was started before" with **Open the existing job** and **Start anyway** | | |
| 16 | Start a practice job, and during "Making your renders…" close the window. Launch again | The job carries on by itself and reaches review | | |
| 17 | **Help**: read the health checks, press **Copy diagnostics** and paste it into Notepad | Plain words; no key in the text | | |
| 18 | Help → **Export feedback** | A zip named `RenderAgent feedback <date>.zip` on `Desktop\RenderAgent`, holding `feedback.jsonl` (your flag and correction) and thumbnails, with no PDFs | | |
| 19 | Every screen has one big main button, and the words make sense without knowing the code | No codes such as AWAITING_APPROVAL anywhere | | |

## Lines added or changed in refinement rounds

(Claude Code adds a line here for anything it changes in a round of `docs/M5_UI_FEEDBACK.md`.)

| # | Added | Step | Expected | ✓ | Notes |
| --- | --- | --- | --- | --- | --- |
| 20 | Visual refresh | On every job screen, look at the step strip under the heading (Start → Approve the plan → Make the renders → Review → Done) | The current step is highlighted; a paused or failed job shows its step in red | | |
| 21 | Visual refresh | Home with Input empty | A large drop area says "Drag a PDF onto this window" and shows the Input folder path | | |
| 22 | Visual refresh | Review: approve a few, flag one | The bottom bar keeps a live tally (approved / flagged / waiting); approved cards get a green tick | | |
| 23 | Review fix | On a practice run, look at a review card, then click the image | The card says "Practice image" and shows a plain house with a clear DRAFT stamp (no prompt text). Clicking opens the render large, next to the drawing it was made from | | |
