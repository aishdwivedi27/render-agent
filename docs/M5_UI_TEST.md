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
| 6 | Open **Start options**, tick **Practice run**, then press **Start** | The job opens. The PDF is gone from Input. Progress shows "Step 1 of 6: Read the drawings · part N of 4" | | |
| 6a | Watch the progress screen while the drawings are read | The heading line says "Step 1 of 5: Start · part N of 5: …" and the step strip shows "part N of 5" under Start; N goes up as it works | | |
| 7 | Open `Desktop\RenderAgent\History` | A folder `<YYYY-MM-DD HHMM> - <name>` with the PDF and `started.txt` (date and time in words, your Windows user name, the full Output folder path, the job ID) | | |
| 7a | **Choose the views** screen appears once the drawings are read | Every view is listed; front day is ticked and locked ("always made"); views these drawings cannot support are greyed with the reason (e.g. no deck); the night view is unticked but can be ticked | | |
| 7b | Type `only external front and back elevation, front at night` under "Or say it in your own words" | "I understood: Front elevation, day · Rear elevation, day · Front elevation, night" (rear listed as "Cannot be made" if there is no deck). **Plan these views** says "Confirm your words first" until you press **Yes, use these views**, which sets the ticks. **What the agent will do** lists the ticked views | | |
| 7c | Press **Plan these N views** | Progress shows "Step 3 of 6: Approve the plan · part 1 of 2: Planning the views", then Approval lists only the chosen views under **These renders will be made** | | |
| 7d | Start a PDF with a same-name `.txt` holding `@views=Only external front and back elevation` and `Please include a night view.` | Choose views shows "From the instruction file" with both lines; front, rear (if a deck is drawn) and front night are ticked, no interiors. **Read views from this text** fills the words box for you to confirm | | |
| 8 | Approval → **Change the views** | Back to Choose views with your last ticks and words; change them and plan again; Approval shows a green "Revised plan" with Added/Removed | | |
| 8c | If a view is listed under **Blocked by the drawings**: tick **Render anyway** on one | **Approve these N renders** goes up by one; after approval that view is rendered and shown at review as not fully checked (*). A view blocked because a drawing is missing has no tick | | |
| 8a | Under **Unclear or disagreeing in the drawings**, type what one item actually means → **Apply and show the revised plan** | Progress shows Step 1 parts 4–5 again, then Approval returns with "Revised with your corrections", what changed and your correction listed | | |
| 8b | Fill one empty material box, add one **Correct a fact** row → **Approve these N renders** | Approval is accepted. If a required item is empty, it is named in a red notice and nothing starts | | |
| 9 | Watch the progress screen | Heading "Making your renders"; "Step 3 of 5: Make the renders · part 2 of 3: Making the renders"; the bar and "12 of 14 renders" count move | | |
| 10 | Review: flag one render (category + a note, tick "Also use this for the rest of this job"), approve the others, then press **Continue: redo 1 flagged** | The flagged card shows a red dashed cloud. After Continue only that render comes back, as a new version waiting for you | | |
| 11 | Approve the new version → **Finish** | Results screen says "Finished" | | |
| 12 | Check `Desktop\RenderAgent\Output\<name>` | Each `Dwelling N\` (and `Common\` for shared views) has `Draft\` with every version carrying the DRAFT mark (`_v1`, `_v2`), `Draft\draft-final\` with the approved version (still marked) and `FINAL APPROVED\` with the approved version without the mark at 1920×1080; no `clean` folder; `report.html` opens with thumbnails and a "Tokens and cost" table | | |
| 12a | In step 10, also leave one render untouched (neither approved nor flagged) before **Continue** | Only the flagged render comes back as a new version; the untouched one keeps its version and is still waiting. Approving makes no new image (no extra call in the job's `budget.json`) | | |
| 13 | Results → **Open Output folder**, then **Open History folder** | Explorer opens each folder; the Output folder is under `Output\Completed\<name>` | | |
| 13a | Home: open **Completed** at the bottom | The finished job is listed there (not under Your jobs) with an **Open folder** button. Jobs finished before this change are moved to `Output\Completed` the next time the app opens | | |
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
| 24 | Polish round | Hover the main button on any screen | It lifts, glows and a light sweep crosses it; pressing it sinks slightly | | |
| 25 | Polish round | Watch Home, the progress screen and Review | Faint grid background; rows and cards fade in one after another; the progress bar shimmers; review cards lift and the image zooms on hover | | |
