"""append_turn must tolerate CC-path string turn_ids in the shared chat_log.

The Container-CC path records its turns in the SAME session["chat_log"] as the
NS path, but with string UUID turn_ids (cc_turn_complete.serialize_cc_chat_log_entry
-> turn_id = str(run_id)). The NS append_turn computes the next turn_id as an int.
When an NS turn (e.g. run_pipeline_launch) follows a CC turn, the previous turn_id
is a string and `log[-1]["turn_id"] + 1` raised TypeError. Regression lock.
"""
from chat_nextseek.chat_memory import append_turn


def test_append_turn_after_cc_string_turn_id():
    # Two CC discovery turns with string UUID ids (the live-repro shape).
    session = {"chat_log": [
        {"turn_id": "2c83666a-8585-401f-b60b-334d0dff5fd1", "mode": "cc"},
        {"turn_id": "5f088949-3d5d-471d-a647-200139399ee8", "mode": "cc"},
    ]}
    append_turn(session, user_query="launch scrnaseq", mode="pipeline_agent")
    log = session["chat_log"]
    assert len(log) == 3
    # No prior INT ids in the log -> the first NS turn starts at 1.
    assert log[-1]["turn_id"] == 1
    assert isinstance(log[-1]["turn_id"], int)


def test_append_turn_int_sequence_survives_interleaved_cc():
    # An int NS turn, then a CC string turn: the next NS id must be max_int+1 (2),
    # not reset to 1 (which would collide) and not crash on the string id.
    session = {"chat_log": [
        {"turn_id": 1, "mode": "graph_query"},
        {"turn_id": "cc-run-uuid", "mode": "cc"},
    ]}
    append_turn(session, user_query="x", mode="graph_query")
    assert session["chat_log"][-1]["turn_id"] == 2


def test_append_turn_plain_int_sequence_unchanged():
    # Pure NS log: behavior is unchanged (monotonic int increment).
    session = {"chat_log": [{"turn_id": 1, "mode": "graph_query"}]}
    append_turn(session, user_query="y", mode="graph_query")
    assert session["chat_log"][-1]["turn_id"] == 2
