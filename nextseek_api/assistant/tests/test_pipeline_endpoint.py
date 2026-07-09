import pytest
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from nextseek_api.assistant.models_db import ChatSession
import nextseek_api.services.assistant as assistant_svc

_URL = "/nextseek_api/assistant/pipeline/"


@pytest.fixture(autouse=True)
def _allow_participating(monkeypatch):
    # AssistantViewSet gates on UserInParticipatingProject, whose real check
    # calls out to SEEK (get_current_person). Patch it True so the tests exercise
    # the endpoint logic, mirroring test_granular_endpoints.py's setUp.
    monkeypatch.setattr(
        assistant_svc.UserInParticipatingProject, "has_permission",
        lambda self, request, view: True,
    )


@pytest.mark.django_db
def test_pipeline_endpoint_seeds_session(monkeypatch):
    User = get_user_model()
    user = User.objects.create_user("alice", password="x")
    cs = ChatSession.objects.create(user=user)

    def fake_start(session, config, *, uids, pipeline_key, **kw):
        session["pipeline_agent"] = {
            "active": True, "pipeline_key": pipeline_key,
            "resolved": {"uids": list(uids), "accessions": []},
        }
        return {"action": "ask", "reply": f"Primed {len(uids)} for {pipeline_key}."}
    monkeypatch.setattr(assistant_svc.pipeline_agent, "start_from_cohort", fake_start)

    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.post(_URL, {"session_id": str(cs.session_id),
                              "uids": "MUS-1,MUS-2", "pipeline": "rnaseq"}, format="json")

    assert resp.status_code == 200
    body = resp.json()
    assert "Primed 2" in body["reply"]
    assert body["primed_uid_count"] == 2
    cs.refresh_from_db()
    assert cs.extra_state["pipeline_agent"]["active"] is True
    assert cs.extra_state["pipeline_agent"]["resolved"]["uids"] == ["MUS-1", "MUS-2"]


@pytest.mark.django_db
def test_pipeline_endpoint_rejects_unknown_pipeline():
    User = get_user_model()
    user = User.objects.create_user("bob", password="x")
    cs = ChatSession.objects.create(user=user)
    client = APIClient()
    client.force_authenticate(user=user)
    resp = client.post(_URL, {"session_id": str(cs.session_id),
                              "uids": "MUS-1", "pipeline": "notreal"}, format="json")
    assert resp.status_code == 400


@pytest.mark.django_db
def test_pipeline_endpoint_rejects_foreign_session():
    User = get_user_model()
    owner = User.objects.create_user("owner", password="x")
    intruder = User.objects.create_user("intruder", password="x")
    cs = ChatSession.objects.create(user=owner)
    client = APIClient()
    client.force_authenticate(user=intruder)
    resp = client.post(_URL, {"session_id": str(cs.session_id),
                              "uids": "MUS-1", "pipeline": "rnaseq"}, format="json")
    assert resp.status_code == 404
