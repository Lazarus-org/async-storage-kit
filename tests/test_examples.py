"""Execute public documentation examples with an injected SDK fake, never AWS."""

from __future__ import annotations

import re
import runpy
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLES = re.findall(
    r"```python\n(.*?)```",
    (PROJECT / "README.md").read_text(encoding="utf-8"),
    re.DOTALL,
)


@pytest.mark.parametrize(
    "code", EXAMPLES, ids=[f"readme-{index}" for index in range(len(EXAMPLES))]
)
def test_readme_examples(code, monkeypatch):
    from test_s3 import FakeSession

    import async_storage_kit

    original = async_storage_kit.S3Storage
    session = FakeSession()
    monkeypatch.setattr(
        async_storage_kit,
        "S3Storage",
        lambda *args, **kwargs: original(*args, **kwargs, session=session),
    )
    monkeypatch.setenv("STORAGE_BUCKET", "documentation-test")
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    exec(compile(code, "README.md", "exec"), {"__name__": "__main__"})


def test_basic_example_runs():
    runpy.run_path(str(PROJECT / "examples/basic.py"), run_name="__main__")
