"""Replay the reporter's snippet against the release the reporter had.

A bug report describes the package as it was when the issue was opened. Running
the snippet against whatever version a sandbox happens to have says little: a
bug fixed since then will look like the reporter's mistake. So Triagewell finds
the PyPI package the repository publishes, picks the version the reporter
states (or, failing that, the newest release published before the issue was
opened), installs exactly that in Otari's sandbox, and runs the snippet in a
fresh interpreter.

Everything here is read-only: the GitHub contents API, PyPI's JSON API, and a
sandbox that is thrown away after the run.
"""

from __future__ import annotations

import base64
import json
import re
import tomllib
from dataclasses import dataclass
from typing import Any

import httpx
from packaging.version import InvalidVersion, Version

PYPI = "https://pypi.org/pypi"

# Markers the sandbox program prints, so the verdict can tell an install
# failure from a failure of the snippet itself.
INSTALL_OK = "TRIAGEWELL_INSTALL_OK"
INSTALL_FAILED = "TRIAGEWELL_INSTALL_FAILED"
SNIPPET_START = "TRIAGEWELL_SNIPPET_START"
EXIT_MARK = "TRIAGEWELL_EXIT"
EXTRA_MARK = "TRIAGEWELL_EXTRA_INSTALL"
IMPORT_MARK = "TRIAGEWELL_ADDED_IMPORT"
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Import names whose PyPI distribution is called something else.
IMPORT_TO_DIST = {
    "yaml": "pyyaml", "PIL": "pillow", "cv2": "opencv-python", "sklearn": "scikit-learn", "bs4": "beautifulsoup4",
    "dateutil": "python-dateutil", "attr": "attrs", "google": "protobuf", "jwt": "pyjwt", "dotenv": "python-dotenv",
}


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")


@dataclass
class Pin:
    package: str
    version: str
    source: str  # "stated in the issue" or "newest release before the issue"

    @property
    def spec(self) -> str:
        return f"{self.package}=={self.version}"


def package_name(gh_http: httpx.Client, repo: str) -> str | None:
    """The distribution name from the repository's pyproject.toml, if it has one."""
    try:
        response = gh_http.get(f"/repos/{repo}/contents/pyproject.toml")
        if response.status_code != 200:
            return None
        raw = base64.b64decode(response.json().get("content") or "")
        data = tomllib.loads(raw.decode("utf-8", "replace"))
    except (httpx.HTTPError, ValueError, tomllib.TOMLDecodeError):
        return None
    name = (data.get("project") or {}).get("name") or ((data.get("tool") or {}).get("poetry") or {}).get("name")
    return str(name) if name else None


def releases(name: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, str]:
    """Version -> first upload time (ISO 8601) for every non-yanked release on PyPI."""
    try:
        with httpx.Client(timeout=20, transport=transport, headers={"User-Agent": "triagewell/0.1"}) as http:
            response = http.get(f"{PYPI}/{name}/json")
        if response.status_code != 200:
            return {}
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return {}
    out: dict[str, str] = {}
    for version, files in (data.get("releases") or {}).items():
        times = [f.get("upload_time_iso_8601") or "" for f in files or [] if not f.get("yanked")]
        times = [t for t in times if t]
        if times:
            out[version] = min(times)
    return out


def _stable(version: str) -> bool:
    try:
        v = Version(version)
    except InvalidVersion:
        return False
    return not (v.is_prerelease or v.is_devrelease)


def stated_version(text: str, name: str, known: set[str]) -> str | None:
    """A version the reporter wrote down, if it is a real release of this package."""
    base = re.escape(name.split("[")[0])
    alt = base.replace(r"\-", "[-_]")
    patterns = [
        rf"\b{alt}\s*==\s*([0-9][\w.]*)",
        rf"\b{alt}(?:\s+version)?\s*[:=]?\s*v?([0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[\w.]*)?)\b",
        r"\bversion\s*[:=]?\s*v?([0-9]+\.[0-9]+\.[0-9]+)\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidate = match.group(1).rstrip(".")
            if candidate in known:
                return candidate
    return None


def release_before(found: dict[str, str], created_at: str) -> str | None:
    """The newest stable release published before the issue was opened."""
    if not created_at:
        return None
    before = [v for v, t in found.items() if t <= created_at and _stable(v)]
    return max(before, key=Version) if before else None


def choose_pin(name: str, issue_text: str, created_at: str, found: dict[str, str]) -> Pin | None:
    if not found:
        return None
    stated = stated_version(issue_text, name, set(found))
    if stated:
        return Pin(name, stated, "stated in the issue")
    before = release_before(found, created_at)
    if before:
        return Pin(name, before, "newest release before the issue")
    return None


# The edge in front of otari.ai gives up on a request after about a minute, so
# the whole sandbox program works to a deadline well inside that.
DEADLINE_S = 38


def sandbox_program(snippet: str, pin: Pin | None) -> str:
    """Python for Otari's sandbox: install the pinned release, then run the
    snippet in a fresh interpreter so the pinned version is the one imported.
    A package the snippet imports but the sandbox lacks is installed once, if
    time allows. Everything works to DEADLINE_S.

    The sandbox is a notebook kernel, where sys.exit raises instead of exiting,
    so the program prints the snippet's exit code as a marker line instead."""
    lines = [
        "import os, re, subprocess, sys, time",
        "t0 = time.monotonic()",
        f"left = lambda: {DEADLINE_S} - (time.monotonic() - t0)",
        "PIP = [sys.executable, '-m', 'pip', 'install', '-q', '--disable-pip-version-check', '--only-binary', ':all:']",
        "ok = True",
    ]
    if pin:
        lines += [
            "try:",
            f"    r = subprocess.run(PIP + [{pin.spec!r}], capture_output=True, text=True, timeout=22)",
            "    failed = r.returncode != 0",
            "    detail = r.stderr[-1500:]",
            "except subprocess.TimeoutExpired:",
            "    failed, detail = True, 'pip install took longer than 30 s'",
            "if failed:",
            f"    print({INSTALL_FAILED!r}, {pin.spec!r})",
            "    print(detail, file=sys.stderr)",
            "    ok = False",
            "else:",
            f"    print({INSTALL_OK!r}, {pin.spec!r})",
        ]
    lines += [
        "env = dict(os.environ, PYTHON_COLORS='0', NO_COLOR='1', TERM='dumb')",
        f"ALIASES = {IMPORT_TO_DIST!r}",
        f"snippet = {snippet!r}",
        f"PKG = {(pin.package if pin else '')!r}",
        "import importlib, importlib.util, typing",
        "def import_for(name):",
        "    # A name the reporter used without importing it: a module, a name from a few",
        "    # common standard modules,",
        "    # or something the package under test exports. Nothing else is guessed.",
        "    if importlib.util.find_spec(name) is not None:",
        "        return f'import {name}'",
        "    for std in ('typing', 'types', 'collections.abc', 'dataclasses', 'enum', 'pathlib', 'datetime', 'decimal', 'functools'):",
        "        if hasattr(importlib.import_module(std), name):",
        "            return f'from {std} import {name}'",
        "    top = PKG.replace('-', '_').split('[')[0]",
        "    try:",
        "        mod = importlib.import_module(top) if top else None",
        "    except Exception:",
        "        mod = None",
        "    if mod is not None and hasattr(mod, name):",
        "        return f'from {top} import {name}'",
        "    return None",
        "child, code = None, None",
        "if ok:",
        f"    print({SNIPPET_START!r}, flush=True)",
        "    for attempt in range(4):",
        "        try:",
        "            child = subprocess.run([sys.executable, '-c', snippet], capture_output=True, text=True, timeout=max(3, left() - 2), env=env)",
        "            code = child.returncode",
        "        except subprocess.TimeoutExpired:",
        "            child, code = None, 124",
        "            break",
        "        n = re.search(r\"NameError: name '([A-Za-z_][A-Za-z0-9_]*)' is not defined\", child.stderr or '')",
        "        if code != 0 and n and attempt < 3 and left() > 8:",
        "            fix = import_for(n.group(1))",
        "            if fix and fix not in snippet:",
        "                snippet = fix + '\\n' + snippet",
        f"                print({IMPORT_MARK!r}, fix, flush=True)",
        "                continue",
        "        m = re.search(r\"No module named '([A-Za-z0-9_]+)\", child.stderr or '')",
        "        if code == 0 or not m or attempt == 3 or left() < 15:",
        "            break",
        "        dist = ALIASES.get(m.group(1), m.group(1))",
        "        try:",
        "            r = subprocess.run(PIP + [dist], capture_output=True, text=True, timeout=max(3, left() - 12))",
        "        except subprocess.TimeoutExpired:",
        "            break",
        "        if r.returncode != 0:",
        "            break",
        f"        print({EXTRA_MARK!r}, dist, flush=True)",
        "    if child is not None:",
        "        sys.stdout.write(child.stdout)",
        "        sys.stderr.write(child.stderr)",
        "    else:",
        "        print('TimeoutExpired: the snippet did not finish inside the sandbox deadline', file=sys.stderr)",
        f"    print('\\n{EXIT_MARK}', code)",
    ]
    return "\n".join(lines) + "\n"


def split_output(stdout: str) -> tuple[str | None, str]:
    """(install status line or None, the snippet's own stdout)."""
    status = None
    lines = stdout.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.startswith((INSTALL_OK, INSTALL_FAILED)):
            status = line
        if line.startswith(SNIPPET_START):
            start = i + 1
            break
    own = [line for line in lines[start:] if not line.startswith((EXIT_MARK, EXTRA_MARK, IMPORT_MARK))]
    return status, strip_ansi("\n".join(own).strip("\n"))


def added_imports(stdout: str) -> list[str]:
    """Imports the sandbox added to the reporter's snippet, shown in the report."""
    return [line.split(" ", 1)[1].strip() for line in stdout.splitlines() if line.startswith(IMPORT_MARK + " ")]


def extra_installs(stdout: str) -> list[str]:
    """Packages the sandbox added because the snippet imported them."""
    return [line.split(" ", 1)[1].strip() for line in stdout.splitlines() if line.startswith(EXTRA_MARK + " ")]


def exit_code(stdout: str, fallback: int) -> int:
    """The snippet's own exit code, from the marker line, else the sandbox's."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(EXIT_MARK):
            try:
                return int(line.split()[1])
            except (IndexError, ValueError):
                break
    return fallback


def pin_dict(pin: Pin | None) -> dict[str, Any] | None:
    return None if pin is None else {"package": pin.package, "version": pin.version, "source": pin.source}


def describe(pin: Pin | None) -> str:
    return json.dumps(pin_dict(pin)) if pin else "no pinned release (sandbox default packages)"
