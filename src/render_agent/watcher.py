"""Inbox watcher (M4): polling loop, stability check (size + mtime unchanged for stable_seconds),
SHA-256 de-duplication, single-instance lock file, disk-space guard, claims a drop by moving it
into jobs/<id>/input/. Loose PDF + <stem>.txt, or a sub-folder of PDFs + instructions.txt."""
