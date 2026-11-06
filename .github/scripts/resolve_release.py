"""Resolve and validate a release tag once, before any candidate jobs start."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import tomllib


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> None:
    tag = os.environ["RELEASE_TAG"]
    if not re.fullmatch(r"v[0-9][0-9A-Za-z.+-]*", tag):
        raise SystemExit("Invalid release tag")
    sha = git("rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
    branch = os.environ["DEFAULT_BRANCH"]
    git("check-ref-format", f"refs/heads/{branch}")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, f"origin/{branch}"], check=True
    )
    if os.environ.get("GITHUB_EVENT_NAME") == "push":
        event_sha = git("rev-parse", "--verify", os.environ["GITHUB_SHA"] + "^{commit}")
        if event_sha != sha:
            raise SystemExit("Release tag moved since the triggering event")
    project = tomllib.loads(git("show", f"{sha}:pyproject.toml"))["project"]
    if project["name"] != "async-storage-kit" or tag != f"v{project['version']}":
        raise SystemExit("Release tag does not match package identity/version")
    module = ast.parse(git("show", f"{sha}:src/async_storage_kit/__init__.py"))
    version = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        )
    )
    if version != project["version"]:
        raise SystemExit("Runtime version differs from project metadata")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"sha={sha}\n")
    print(f"Validated {tag} at commit {sha}")


if __name__ == "__main__":
    main()
