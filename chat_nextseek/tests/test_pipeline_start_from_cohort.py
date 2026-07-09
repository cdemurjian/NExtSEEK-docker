import chat_nextseek.pipeline.agent as pa


def test_start_from_cohort_seeds_state(monkeypatch):
    def fake_run_loop(session, config, *, log_dir=None):
        return {"action": "ask",
                "reply": "Primed 2 samples for rnaseq. Confirm genome/params?",
                "params": None}
    monkeypatch.setattr(pa, "_run_loop", fake_run_loop)

    session = {}
    result = pa.start_from_cohort(
        session, config=object(), uids=["MUS-1", "MUS-2"], pipeline_key="rnaseq")

    state = session["pipeline_agent"]
    assert state["active"] is True
    assert state["pipeline_key"] == "rnaseq"
    assert state["resolved"]["uids"] == ["MUS-1", "MUS-2"]
    assert state["resolved"]["accessions"] == []
    assert result["action"] == "ask"
    assert "Primed" in result["reply"]
