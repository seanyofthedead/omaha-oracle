"""Fixtures for CDK infrastructure tests.

CDK asset paths in the stacks are relative to infra/, so the JSII subprocess
must be started with infra/ as its working directory.  We chdir *before* any
CDK import triggers the JSII kernel.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

# chdir at import time so the JSII subprocess inherits the correct CWD.
# Stacks reference asset paths like "infra/layer" and "src" relative to repo root.
os.chdir(_REPO_ROOT)
