"""Error taxonomy (docs/SPEC.md section 9).

Every error carries the three plain-English fields written to error.json, so a person on the host
can act without reading a stack trace.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentError(Exception):
    code: str
    what_happened: str
    likely_cause: str = ""
    what_to_do: str = ""

    def __str__(self) -> str:
        return f"[{self.code}] {self.what_happened}"

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "what_happened": self.what_happened,
            "likely_cause": self.likely_cause,
            "what_to_do": self.what_to_do,
        }


class TransientError(AgentError):
    """Worth retrying with backoff: rate limit, timeout, 5xx, network."""


class PermanentError(AgentError):
    """Retrying cannot help: bad request, content policy, unreadable output. Fail this unit."""


class PauseJobError(AgentError):
    """The job must wait for a person or a fix (bad key, quota, low disk). Never marked FAILED."""


class InputError(AgentError):
    """The dropped files cannot be processed (not a PDF, encrypted, empty). Job goes to failed/."""
