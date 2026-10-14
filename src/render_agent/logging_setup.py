"""Agent log (rotating) and per-job log, with API keys redacted."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from render_agent.config import SECRET_ENV_VARS

LOGGER_NAME = "render_agent"
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
# Vendor key shapes: OpenAI (sk-..., sk-proj-...), Anthropic (sk-ant-...), bearer headers.
KEY_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"),
]
MASK = "***REDACTED***"


class RedactingFilter(logging.Filter):
    """Masks known secret values and anything shaped like an API key, in message and args."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, MASK)
        for pat in KEY_PATTERNS:
            text = pat.sub(lambda m: (m.group(1) if m.groups() else "") + MASK, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True


def _secret_values(extra: Iterable[str]) -> list[str]:
    return [*(os.environ.get(v, "") for v in SECRET_ENV_VARS), *extra]


def setup_agent_logging(logs_dir: Path, secrets: Iterable[str] = (), console: bool = True) -> logging.Logger:
    """Rotating agent.log under logs_dir (5 x 5 MB). Safe to call more than once."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    for h in list(logger.handlers):
        if getattr(h, "_agent_handler", False):
            logger.removeHandler(h)
            h.close()
    redactor = RedactingFilter(_secret_values(secrets))
    logs_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(logs_dir / "agent.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    ]
    if console:
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(logging.Formatter(FORMAT))
        h.addFilter(redactor)
        h._agent_handler = True  # type: ignore[attr-defined]
        logger.addHandler(h)
    return logger


def attach_job_log(job_dir: Path, secrets: Iterable[str] = ()) -> logging.Handler:
    """Adds jobs/<id>/job.log to the agent logger; call detach_job_log when the job stops."""
    job_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(job_dir / "job.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(RedactingFilter(_secret_values(secrets)))
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return handler


def detach_job_log(handler: logging.Handler) -> None:
    logging.getLogger(LOGGER_NAME).removeHandler(handler)
    handler.close()


def close_job_logs(job_dir: Path) -> None:
    """Detaches every job.log handler inside job_dir, so Windows lets the folder be moved."""
    logger = logging.getLogger(LOGGER_NAME)
    root = job_dir.resolve()
    for h in list(logger.handlers):
        name = getattr(h, "baseFilename", None)
        if name and Path(name).resolve().is_relative_to(root):
            detach_job_log(h)
