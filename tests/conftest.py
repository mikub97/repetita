"""
Test bootstrap.

Environment variables are set here, at import time of the *conftest*, because
module-level path constants in `roda.store` are read when the module is first
imported. Setting them inside a fixture would be too late: the first `import
roda.store` anywhere in the test session would already have captured the real
paths, and a test run would write into actual study history.
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="roda-tests-"))
os.environ["RODA_DATA"] = str(_TMP)
os.environ["RODA_DB"] = str(_TMP / "roda.db")
os.environ["RODA_TOKEN_FILE"] = str(_TMP / ".token")
