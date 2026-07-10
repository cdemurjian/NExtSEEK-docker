import chat_nextseek.pipeline.agent as pa


def test_start_from_cohort_seeds_without_running_loop(monkeypatch):
    # The bridge endpoint calls this over HTTP with a ~30s client read-timeout, so it
    # must NOT run the Bedrock loop (that ReadTimeouts the CC bin). Guard: if _run_loop
    # is ever called, fail loudly.
    def boom(*a, **k):
        raise AssertionError(
            "start_from_cohort must NOT run the agent loop — it would ReadTimeout the CC bin")
    monkeypatch.setattr(pa, "_run_loop", boom)

    session = {}
    result = pa.start_from_cohort(
        session, config=object(), uids=["MUS-1", "MUS-2"], pipeline_key="rnaseq")

    state = session["pipeline_agent"]
    assert state["active"] is True
    assert state["pipeline_key"] == "rnaseq"
    assert state["resolved"]["uids"] == ["MUS-1", "MUS-2"]
    assert state["resolved"]["accessions"] == []

    # UID-bearing opening message so the NS-side loop (next turn) can resolve the cohort.
    msg = state["messages"][0]["content"]
    assert "MUS-1" in msg and "MUS-2" in msg
    assert "explicit_uids" in msg

    # Fast, deterministic reply — no LLM.
    assert result["action"] == "ask"
    assert "Primed 2" in result["reply"]
    assert "rnaseq" in result["reply"]
