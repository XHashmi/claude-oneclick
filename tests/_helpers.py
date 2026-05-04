"""Shared test helpers — temp HOME setup so tests don't pollute real config."""
from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def isolated_home():
    """Run inside a temp $HOME / $XDG_CONFIG_HOME so config files are sandboxed."""
    tmp = tempfile.mkdtemp(prefix="coc-test-")
    saved = {k: os.environ.get(k) for k in ("HOME", "XDG_CONFIG_HOME", "APPDATA")}
    os.environ["HOME"] = tmp
    os.environ["XDG_CONFIG_HOME"] = str(Path(tmp) / ".config")
    os.environ["APPDATA"] = str(Path(tmp) / "AppData/Roaming")
    try:
        yield Path(tmp)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
