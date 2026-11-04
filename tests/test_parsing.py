from triagewell.triage import extract_python, ground_evidence, judge_repro, parse_json_object


def test_parse_json_with_think_and_fence():
    text = '<think>{"not": "this"}</think>Sure.\n```json\n{"kind": "bug", "nested": {"a": 1}}\n```'
    assert parse_json_object(text) == {"kind": "bug", "nested": {"a": 1}}


def test_parse_json_bare_object_with_braces_in_strings():
    text = 'Result: {"summary": "use {} carefully", "labels": ["bug"]} done'
    assert parse_json_object(text)["summary"] == "use {} carefully"


def test_parse_json_garbage():
    assert parse_json_object("no json here") == {}
    assert parse_json_object("") == {}


def test_extract_python_first_short_block():
    body = "text\n```python\nimport json\nprint(1)\n```\n```py\nprint(2)\n```"
    assert extract_python(body) == "import json\nprint(1)"


def test_extract_python_ignores_other_languages_and_huge_blocks():
    assert extract_python("```bash\nls\n```") is None
    huge = "```python\n" + "x = 1\n" * 200 + "```"
    assert extract_python(huge) is None


def test_judge_repro_variants():
    assert judge_repro("", "Traceback (most recent call last):\nTypeError: boom", 1) == (True, "reproduced: TypeError: boom")
    reproduced, verdict = judge_repro("", "ModuleNotFoundError: No module named 'any_llm'", 1)
    assert reproduced is None and "any_llm" in verdict
    assert judge_repro("ok\n", "", 0)[0] is False


def test_ground_evidence_splits_unbacked_claims():
    evidence = ["issue text: reporter uses Python 3.13", "fetched https://docs.example/x: it says so", "wiki: encoder is pluggable", "sandbox: TypeError"]
    backed, claimed = ground_evidence(evidence, [{"type": "mcp", "status": "completed"}], None)
    assert "wiki: encoder is pluggable" in backed
    assert "issue text: reporter uses Python 3.13" in backed
    assert "fetched https://docs.example/x: it says so" in claimed
    assert "sandbox: TypeError" in claimed


def test_ground_evidence_counts_direct_sandbox_run():
    backed, claimed = ground_evidence(["sandbox: TypeError"], [], {"via": "otari /code/execute"})
    assert backed == ["sandbox: TypeError"] and claimed == []
