"""A late cc_session_id write must not clobber pipeline_agent seeded concurrently."""
from django.contrib.auth.models import User
from django.test import TestCase

from nextseek_api.assistant.models_db import ChatSession


class PersistCcSessionMergeTests(TestCase):
    databases = {"default"}

    def test_persist_cc_session_preserves_concurrent_seed(self):
        user = User.objects.create_user("cc", password="p")
        cs = ChatSession.objects.create(user=user, extra_state={})

        # Simulate: the CC turn loaded `cs` with empty extra_state (stale object A).
        # Meanwhile the nested query/async seeded pipeline_agent on the DB row.
        ChatSession.objects.filter(pk=cs.pk).update(
            extra_state={"pipeline_agent": {"active": True}}
        )

        # The CC turn's late single-key write, using its stale object A.
        from nextseek_api.services.cc_assistant import _persist_cc_session_id
        _persist_cc_session_id(cs, "claude-123")

        cs.refresh_from_db()
        assert cs.extra_state["cc_session_id"] == "claude-123"
        assert cs.extra_state["pipeline_agent"]["active"] is True  # not clobbered
