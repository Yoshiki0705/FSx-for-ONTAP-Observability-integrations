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
