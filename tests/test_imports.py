from __future__ import annotations

import os
import subprocess
import sys


def test_importing_core_does_not_import_aiofiles():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, async_storage_kit; assert 'aiofiles' not in sys.modules",
        ],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
