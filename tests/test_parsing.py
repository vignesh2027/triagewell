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
    # The error the report names shows up: reproduced.
    r = judge_repro("", "Traceback (most recent call last):\nTypeError: boom", 1, "I get TypeError: boom")
    assert r == (True, "reproduced: TypeError: boom", "reproduced")
    # No module: not run.
    reproduced, verdict, outcome = judge_repro("", "ModuleNotFoundError: No module named 'any_llm'", 1)
    assert reproduced is None and "any_llm" in verdict and outcome == "not_run"
    # Report names an error, snippet runs cleanly: refuted.
    assert judge_repro("ok\n", "", 0, "raises KeyError on empty dict")[2] == "refuted"
    # Nothing named and nothing raised: inconclusive, not refuted.
    assert judge_repro("ok\n", "", 0, "the output is wrong")[2] == "inconclusive"
    # A different error than the one reported is not a reproduction.
    assert judge_repro("", "Traceback (most recent call last):\nValueError: x", 1, "I get a KeyError")[2] == "inconclusive"
    # A snippet that dies on NameError is incomplete, not a reproduction.
    assert judge_repro("", "Traceback (most recent call last):\nNameError: name 'm' is not defined", 1)[2] == "inconclusive"
    # A warning the report names counts even with exit code 0.
    assert judge_repro("", "x.py:3: DeprecationWarning: old", 0, "emits DeprecationWarning")[2] == "reproduced"
    # Names that only appear inside the reporter's own code do not count.
    assert judge_repro("ok\n", "", 0, "```python\ntry:\n  f()\nexcept ValueError: pass\n```", "try:\n  f()\nexcept ValueError: pass")[2] == "inconclusive"
    # An install failure is reported as such.
    out = "TRIAGEWELL_INSTALL_FAILED pydantic==1.0\n"
    assert judge_repro(out, "error", 97)[2] == "not_run"

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


def test_search_terms_given_as_one_string_are_split_not_spelled():
    from triagewell.triage import search_terms

    assert search_terms("BaseStruct, dataclass", "t") == ["BaseStruct", "dataclass"]
    assert search_terms(["a", "TypeAdapter"], "t") == ["TypeAdapter"]
    assert search_terms(None, "click.launch fails on PowerShell") == ["click.launch", "powershell"]


def test_a_pasted_repl_session_becomes_code():
    text = "```python\n>>> import json\n>>> json.dumps({1})\nTraceback ...\n```"
    assert extract_python(text) == "import json\njson.dumps({1})"


def test_missing_credentials_are_not_a_reproduction():
    out = judge_repro("", "any_llm.exceptions.MissingApiKeyError: No openai API key provided.", 1, "raises KeyError")
    assert out[2] == "not_run"


def test_a_crash_is_not_a_reproduction_when_the_report_describes_no_failure():
    err = "Traceback (most recent call last):\nAttributeError: module 'logging' has no attribute 'addHandler'"
    assert judge_repro("", err, 1, "Tabs are not expanded when printing")[2] == "inconclusive"
    assert judge_repro("", err, 1, "It crashes when printing tabs")[2] == "reproduced"
    tail = "pydantic.errors.PydanticUserError: not fully defined\n\nFor further information visit https://errors.pydantic.dev"
    assert "PydanticUserError" in judge_repro("", tail, 1, "it raises an error")[1]
