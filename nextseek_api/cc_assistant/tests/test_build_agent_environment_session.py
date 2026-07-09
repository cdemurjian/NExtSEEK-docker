from nextseek_api.cc_assistant.cc_engine import build_agent_environment


def test_session_id_injected():
    env = build_agent_environment(source={}, api_user=None, api_pass=None,
                                  path_mappings={}, chat_session_id="sess-123")
    assert env["NEXTSEEK_CHAT_SESSION_ID"] == "sess-123"


def test_session_id_absent_when_none():
    env = build_agent_environment(source={}, api_user=None, api_pass=None,
                                  path_mappings={})
    assert "NEXTSEEK_CHAT_SESSION_ID" not in env
