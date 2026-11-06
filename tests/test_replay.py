from triagewell import replay

RELEASES = {
    "2.9.0": "2024-09-01T00:00:00Z",
    "2.10.0": "2024-11-20T00:00:00Z",
    "2.10.1b1": "2024-12-01T00:00:00Z",
    "2.11.0": "2025-03-27T00:00:00Z",
}


def test_a_stated_version_wins_when_it_is_a_real_release():
    pin = replay.choose_pin("pydantic", "pydantic==2.9.0 on Linux", "2025-06-01T00:00:00Z", RELEASES)
    assert (pin.version, pin.source) == ("2.9.0", "stated in the issue")
    pin = replay.choose_pin("pydantic", "Pydantic version: 2.10.0", "2025-06-01T00:00:00Z", RELEASES)
    assert pin.version == "2.10.0"


def test_otherwise_the_newest_stable_release_before_the_issue():
    pin = replay.choose_pin("pydantic", "it breaks", "2025-01-15T00:00:00Z", RELEASES)
    assert (pin.version, pin.source) == ("2.10.0", "newest release before the issue")
    assert replay.choose_pin("pydantic", "it breaks", "2020-01-01T00:00:00Z", RELEASES) is None


def test_a_version_that_is_not_a_release_is_ignored():
    pin = replay.choose_pin("pydantic", "pydantic==9.9.9", "2025-04-01T00:00:00Z", RELEASES)
    assert pin.version == "2.11.0"


def test_the_sandbox_program_installs_then_runs_in_a_fresh_interpreter():
    program = replay.sandbox_program("import pydantic\nprint(pydantic.VERSION)", replay.Pin("pydantic", "2.10.0", "x"))
    compile(program, "<sandbox>", "exec")
    assert "'pydantic==2.10.0'" in program and "sys.executable, '-c'" in program
    assert "sys.exit" not in program  # the sandbox is a notebook kernel
    out = "TRIAGEWELL_INSTALL_OK pydantic==2.10.0\nTRIAGEWELL_SNIPPET_START\n2.10.0\n\nTRIAGEWELL_EXIT 3\n"
    assert replay.split_output(out) == ("TRIAGEWELL_INSTALL_OK pydantic==2.10.0", "2.10.0")
    assert replay.exit_code(out, 0) == 3
    assert replay.exit_code("no marker", 1) == 1


def test_the_program_runs_locally_and_reports_the_exit_code():
    import subprocess
    import sys

    program = replay.sandbox_program("import sys\nprint('hi')\nsys.exit(4)", None)
    done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=60)
    assert replay.split_output(done.stdout)[1] == "hi"
    assert replay.exit_code(done.stdout, 0) == 4


def test_colour_codes_do_not_hide_the_verdict():
    from triagewell.triage import judge_repro

    err = "Traceback (most recent call last):\n\x1b[1;35mNameError\x1b[0m: \x1b[35mname 'BaseModel' is not defined\x1b[0m"
    assert judge_repro("", err, 1)[2] == "inconclusive"
    err = "\x1b[1;35mModuleNotFoundError\x1b[0m: No module named 'fastapi'"
    assert judge_repro("", err, 1)[2] == "not_run"


def test_a_missing_import_is_installed_once_and_recorded():
    out = "TRIAGEWELL_SNIPPET_START\nTRIAGEWELL_EXTRA_INSTALL fastapi\nhello\n\nTRIAGEWELL_EXIT 0\n"
    assert replay.extra_installs(out) == ["fastapi"]
    assert replay.split_output(out)[1] == "hello"


def test_a_missing_import_is_added_and_reported():
    import subprocess
    import sys

    program = replay.sandbox_program("logging.getLogger('x')\nx: Any = 1\nprint('fine')", None)
    done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=60)
    assert replay.split_output(done.stdout)[1] == "fine"
    assert replay.added_imports(done.stdout) == ["import logging", "from typing import Any"]
    assert replay.exit_code(done.stdout, 1) == 0


def test_an_unknown_name_is_not_guessed():
    import subprocess
    import sys

    program = replay.sandbox_program("print(no_such_thing_anywhere)", None)
    done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=60)
    assert replay.added_imports(done.stdout) == []
    assert "NameError" in done.stderr
