"""_append_cc_turn_complete must not clobber a concurrently-seeded pipeline_agent.

During a CC turn, the agent's nextseek-pipeline op seeds extra_state.pipeline_agent
via a nested query/async request on the SAME ChatSession (a different ORM object).
The CC turn holds a stale ChatSession object; its turn-complete chat_log write is a
whole-extra_state-column save, which used to overwrite the seed -> the F9 router gate
saw is_active=False next turn -> the follow-up re-routed to CC and reset the wizard.
Regression lock for the v2 bridge handoff.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.cc_assistant.cc_turn_complete import TurnCompletePayload
from nextseek_api.services.cc_assistant import _append_cc_turn_complete


class CcTurnCompleteMergeTests(TestCase):
    databases = {"default"}

    def test_append_cc_turn_preserves_concurrent_pipeline_seed(self):
        user = User.objects.create_user("ccm", password="p")
        cs = ChatSession.objects.create(user=user, extra_state={})

        # Simulate the nested nextseek-pipeline op seeding pipeline_agent on the DB
        # row while THIS CC turn still holds a stale object (extra_state={}).
        ChatSession.objects.filter(pk=cs.pk).update(
            extra_state={"pipeline_agent": {"active": True, "pipeline_key": "scrnaseq"}})

        payload = TurnCompletePayload(
            chat_session=cs,  # stale object A (extra_state still {})
            user_query="submit scrnaseq", assistant_reply="proposed the run",
            ts="2026-07-11T00:00:00", artifacts=None, cc_traces=[],
            turn_id="cc-uuid-1", cc_session_id="claude-1", raw_jsonl=b"{}")
        _append_cc_turn_complete(payload)

        cs.refresh_from_db()
        # The concurrently-seeded pipeline_agent must survive (not clobbered).
        assert cs.extra_state["pipeline_agent"]["active"] is True
        assert cs.extra_state["pipeline_agent"]["pipeline_key"] == "scrnaseq"
        # ...and the CC turn is still recorded in the shared chat_log.
        assert cs.extra_state["chat_log"][-1]["mode"] == "cc"
        assert cs.extra_state["chat_log"][-1]["turn_id"] == "cc-uuid-1"
