import re

from planning.gemini_video_trigger import _prompt as video_prompt
from planning.judge import ReportabilityTaste, _group_prompt, _prompt as judge_prompt
from planning.planner import build_prompt


CJK = re.compile(r"[\u3400-\u9fff]")


def test_all_gemini_prompt_templates_are_english_only():
    prompts = [
        build_prompt("a person holding a phone"),
        judge_prompt("person hands-on phone", ReportabilityTaste(),
                     confirm="holding phone"),
        _group_prompt([{"index": 0, "label": "holding phone",
                        "claim": "holding phone"}], ReportabilityTaste()),
        video_prompt("a person holds up a phone"),
    ]

    assert all(not CJK.search(prompt) for prompt in prompts)
    assert all("English" in prompt for prompt in prompts)
