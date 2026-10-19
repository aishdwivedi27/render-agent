# M5 UI feedback

M5 signed off 2026-10-16

<!-- When the app is right, write on the first line above this comment:  M5 signed off <YYYY-MM-DD>
     M5.5 cannot start until that line exists. -->

One numbered item per change. Status: open / done / won't do. Claude Code works through the open
items one round at a time, re-runs exit-gate part (a) after each round, and records what it changed
in the Response column and in `docs/M5_UI_TEST.md`.

| # | Screen | What you saw / what should change | Status | Response (Claude Code) |
| --- | --- | --- | --- | --- |
| 1 | All | Fonts and buttons should look more polished | done | New type pair (Bricolage Grotesque headings, Inter Tight body); main button has depth, glow, lift and a light sweep on hover; approve/flag tinted; link buttons get an animated underline |
| 2 | All | Add effects so it looks impressive | done | Faint blueprint grid background, card lift and image zoom, staggered entrance, progress-bar shimmer, pulsing current step, side-rail active bar; all motion off under reduced-motion |
