import pytest

from render_agent.errors import AgentError, InputError, PauseJobError, PermanentError, TransientError


@pytest.mark.parametrize("cls", [TransientError, PermanentError, PauseJobError, InputError])
def test_every_error_carries_plain_english_fields(cls):
    err = cls(code="X", what_happened="a", likely_cause="b", what_to_do="c")
    assert isinstance(err, AgentError)
    assert err.to_dict() == {"code": "X", "what_happened": "a", "likely_cause": "b", "what_to_do": "c"}
    assert str(err) == "[X] a"
