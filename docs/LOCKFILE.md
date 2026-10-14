Generate the pinned lock file used by scripts/install.ps1 on the Windows host:

    pip-compile pyproject.toml -o requirements.lock

Commit requirements.lock whenever dependencies change.
