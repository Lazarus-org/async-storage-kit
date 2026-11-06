"""Release input and artifact-path validation, without creating real release tags."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def load_script(name):
    path = Path(__file__).resolve().parents[1] / ".github/scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/file",
        "source/.env",
        "source/private.key",
        "source/data.sqlite3",
        "source/.venv/python.exe",
        "source/credentials.pem",
        "source/__pycache__/code.pyc",
    ],
)
def test_artifact_validator_rejects_private_and_unsafe_paths(name):
    with pytest.raises(RuntimeError):
        load_script("validate_distribution")._check_path(name)


@pytest.mark.parametrize(
    "tag", ["--help", "v1.0.0;malicious", "v1/evil", "", "v1\nsha=bad"]
)
def test_release_rejects_unsafe_tag_before_git(tag, monkeypatch):
    script = load_script("resolve_release")
    monkeypatch.setenv("RELEASE_TAG", tag)
    monkeypatch.setattr(script, "git", lambda *args: pytest.fail("git must not run"))
    with pytest.raises(SystemExit, match="Invalid release tag"):
        script.main()


@pytest.mark.parametrize(
    "version, runtime, event_sha, succeeds",
    [
        ("1.0.0.dev3", "1.0.0.dev3", "a" * 40, True),
        ("1.0.0", "1.0.0", "a" * 40, False),
        ("1.0.0.dev3", "1.0.0", "a" * 40, False),
        ("1.0.0.dev3", "1.0.0.dev3", "b" * 40, False),
    ],
)
def test_release_pins_commit_checks_versions_and_moved_tag(
    tmp_path, monkeypatch, version, runtime, event_sha, succeeds
):
    script = load_script("resolve_release")
    sha = "a" * 40
    output = tmp_path / "output"
    for key, value in {
        "RELEASE_TAG": "v1.0.0.dev3",
        "DEFAULT_BRANCH": "main",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_SHA": "event",
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)

    def git(*args):
        if args[0] == "rev-parse":
            return event_sha if args[-1] == "event^{commit}" else sha
        if args[0] == "check-ref-format":
            return ""
        if args[-1].endswith("pyproject.toml"):
            return f'[project]\nname="async-storage-kit"\nversion="{version}"'
        return f'__version__ = "{runtime}"'

    calls = []
    monkeypatch.setattr(script, "git", git)
    monkeypatch.setattr(
        script.subprocess, "run", lambda command, **kwargs: calls.append(command)
    )
    if succeeds:
        script.main()
        assert output.read_text() == f"sha={sha}\n"
        assert calls == [["git", "merge-base", "--is-ancestor", sha, "origin/main"]]
    else:
        with pytest.raises(SystemExit):
            script.main()
        assert not output.exists()
