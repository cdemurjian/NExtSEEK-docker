import pytest
from pydantic import ValidationError
from nextseek_api.assistant.models_api import PipelineOpRequest

_SID = "941ad031-e789-4444-8888-000000000000"


def test_valid_request_normalizes():
    req = PipelineOpRequest(session_id=_SID, uids="MUS-1, MUS-2 ,", pipeline="RNAseq")
    assert req.pipeline == "rnaseq"
    assert req.uid_list() == ["MUS-1", "MUS-2"]


def test_empty_uids_rejected():
    with pytest.raises(ValidationError):
        PipelineOpRequest(session_id=_SID, uids=" , ", pipeline="rnaseq")


def test_missing_session_rejected():
    with pytest.raises(ValidationError):
        PipelineOpRequest(uids="MUS-1", pipeline="rnaseq")


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        PipelineOpRequest(session_id=_SID, uids="MUS-1", pipeline="rnaseq", bogus=1)
