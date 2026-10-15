Read CLAUDE.md, docs/SPEC.md and docs/MILESTONES.md. Find the first milestone whose exit gate is not
yet marked done in docs/MILESTONES.md. List its unchecked tasks, propose an order, then implement them
one at a time with tests. Do not make paid API calls. When every task is done, run the exit-gate checks
and stop with a short report: what passed, what failed, anything you need from me.

Rules:
- Never run git commands that change state (add, commit, push, reset, checkout, submodule, or any
  other), in the public repo or in private/. The owner does all git work.
- Order and preconditions: M5 → M5.5 → M6 → M7. M5.5 needs "M5 signed off" with a date at the top of
  docs/M5_UI_FEEDBACK.md; M6 needs "M5.5 signed off" with a date at the top of docs/REFINEMENT_LOG.md.
  If the precondition line is missing, stop and say so.
- For M5: stop after exit-gate part (a), write docs/M5_UI_TEST.md and hand it to me. Then work through
  open items in docs/M5_UI_FEEDBACK.md one round at a time, re-running part (a) after each round.
- For M5.5: before every paid round, show the planned image count and estimated cost (LLM and images)
  and wait for my OK.
- At the exit gate, run the spec sync: list every docs/SPEC.md section this milestone changed and ask
  me to confirm SPEC.md and the live page match. Update "Last synced with the live page" only after I
  confirm. The gate is not passed until then.
- Never start the next milestone without my explicit go-ahead.
- End with every changed file grouped as "public repo" and "private submodule (private/)".
