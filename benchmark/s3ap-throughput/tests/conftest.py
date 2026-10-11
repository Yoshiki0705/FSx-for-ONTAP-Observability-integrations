"""Pytest configuration for the S3 Access Points benchmark tool tests.

Puts the tool directory (which holds handler.py) on sys.path so `import
handler` resolves here, and purges any handler module a sibling vendor suite
may have cached under --import-mode=importlib.
"""

import sys
from pathlib import Path

sys.modules.pop("handler", None)
_tool_dir = str(Path(__file__).resolve().parent.parent)
if _tool_dir not in sys.path:
    sys.path.insert(0, _tool_dir)

# The "process" test mode imports the canonical parser from shared/python. The
# deployed Lambda gets it from scripts/deploy.sh bundling; the test suite needs
# it on sys.path so handler's guarded import resolves to the real parser rather
# than falling back to None.
_shared_python_dir = str(Path(__file__).resolve().parents[3] / "shared" / "python")
if _shared_python_dir not in sys.path:
    sys.path.insert(0, _shared_python_dir)
