from pathlib import Path
import chat_nextseek


def _prompt_text():
    base = Path(chat_nextseek.__file__).parent / "prompts" / "pipeline_agent.txt"
    return base.read_text(encoding="utf-8")


def test_prompt_mentions_submit_to_luria_and_selection_rule():
    text = _prompt_text()
    assert "submit_to_luria" in text
    assert "submit_to_tower" in text  # both still present
    # A selection rule so the agent picks by user intent, not at random.
    assert "Luria" in text or "cluster" in text
