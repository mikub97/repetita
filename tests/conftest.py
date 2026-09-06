"""
Test bootstrap.

Environment variables are set here, at import time of the *conftest*, because
module-level path constants in `repetita.store` are read when the module is first
imported. Setting them inside a fixture would be too late: the first `import
repetita.store` anywhere in the test session would already have captured the real
paths, and a test run would write into actual study history.
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="repetita-tests-"))
os.environ["REPETITA_DATA"] = str(_TMP)
os.environ["REPETITA_DB"] = str(_TMP / "repetita.db")
os.environ["REPETITA_TOKEN_FILE"] = str(_TMP / ".token")
