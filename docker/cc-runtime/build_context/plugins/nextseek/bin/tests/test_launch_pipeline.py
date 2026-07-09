import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _assistant_client as ac  # noqa: E402


def test_launch_pipeline_posts_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        import json
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"reply": "Primed 2 for rnaseq.",
                                         "action": "ask", "pipeline": "rnaseq",
                                         "primed_uid_count": 2})

    client = ac.AssistantClient(
        base_url="http://testserver", assistant_prefix="nextseek_api/assistant",
        auth=("u", "p"), transport=httpx.MockTransport(handler))
    out = client.launch_pipeline(session_id="sess-1", uids="MUS-1,MUS-2", pipeline="rnaseq")

    assert out["primed_uid_count"] == 2
    assert seen["url"].endswith("/nextseek_api/assistant/pipeline/")
    assert seen["body"] == {"session_id": "sess-1", "uids": "MUS-1,MUS-2",
                            "pipeline": "rnaseq", "use_prod": False}
