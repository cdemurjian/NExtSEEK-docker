import json
from chat_nextseek.pipeline import agent_tools as at


class _Cfg:
    def __init__(self, tower, luria):
        self.TOWER_ENV_COMPLETE = tower
        self.LURIA_ENV_COMPLETE = luria
        self.LURIA_ENV = {"user": "u", "key": "/k", "working_path": "/p", "host": "luria.mit.edu"}


def _names(schemas):
    return [t["name"] for t in schemas]


def test_exposure_tower_only():
    names = _names(at.build_pipeline_tool_schemas(_Cfg(tower=True, luria=False)))
    assert "submit_to_tower" in names and "submit_to_luria" not in names
    assert names[-1] == "conclude"


def test_exposure_luria_only():
    names = _names(at.build_pipeline_tool_schemas(_Cfg(tower=False, luria=True)))
    assert "submit_to_luria" in names and "submit_to_tower" not in names


def test_exposure_both():
    names = _names(at.build_pipeline_tool_schemas(_Cfg(tower=True, luria=True)))
    assert "submit_to_tower" in names and "submit_to_luria" in names


def test_exposure_neither_still_has_core_and_conclude():
    names = _names(at.build_pipeline_tool_schemas(_Cfg(tower=False, luria=False)))
    assert names == ["resolve_samples", "write_samplesheet", "configure_run", "conclude"]


def test_tool_submit_to_luria_calls_submitter(monkeypatch):
    monkeypatch.setattr(at, "submit_luria",
                        lambda launch, **kw: [{"job_id": "9", "remote_dir": "/d", "log": "/d/nf-9.out", "run_name": "r"}])
    cfg = _Cfg(tower=False, luria=True)
    state = {"artifacts": {"launch": "/tmp/launch.yml"}}
    out = json.loads(at.tool_submit_to_luria(cfg, state, {"resources": {"partition": "bcc"}}))
    assert out["ok"] is True
    assert out["luria_runs"][0]["job_id"] == "9"
    assert state["artifacts"]["luria_runs"][0]["job_id"] == "9"


def test_tool_submit_to_luria_guards_missing_artifact():
    out = json.loads(at.tool_submit_to_luria(_Cfg(tower=False, luria=True), {"artifacts": {}}, {}))
    assert out["ok"] is False


def test_existing_static_schema_unchanged():
    # Regression guard: the Tower-era constant still lists exactly the original five.
    assert {t["name"] for t in at.PIPELINE_TOOL_SCHEMAS} == {
        "resolve_samples", "write_samplesheet", "configure_run", "submit_to_tower", "conclude"}
