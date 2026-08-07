"""Pytest fixtures for New Relic integration tests."""


import sys
from pathlib import Path

# Isolate this vendor's handler module: purge any previously cached handler
# so that `import handler` in test files resolves to THIS vendor's lambda/.
_handler_modules = ["handler", "ems_handler", "fpolicy_handler"]
for _m in _handler_modules:
    sys.modules.pop(_m, None)
_lambda_dir = str(Path(__file__).parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

import json
import os

import pytest

# Set environment variables BEFORE handler import (module-level os.environ[] access)
os.environ.setdefault("NEW_RELIC_REGION", "US")
os.environ.setdefault("API_KEY_SECRET_ARN", "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:nr-key")
os.environ.setdefault("S3_ACCESS_POINT_ARN", "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit")
os.environ.setdefault("LOG_LEVEL", "DEBUG")


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("NEW_RELIC_REGION", "US")
    monkeypatch.setenv("API_KEY_SECRET_ARN", "arn:aws:secretsmanager:ap-northeast-1:123456789012:secret:nr-key")
    monkeypatch.setenv("S3_ACCESS_POINT_ARN", "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/fsxn-audit")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")


@pytest.fixture
def sample_s3_event():
    return {
        "Records": [{
            "s3": {
                "bucket": {"name": "fsxn-audit-logs"},
                "object": {"key": "audit/svm1/2026/01/15/audit.json"},
            }
        }]
    }


@pytest.fixture
def sample_logs():
    logs = [
        {"timestamp": "2026-01-15T12:00:01Z", "EventID": "4663", "SVMName": "svm-prod-01",
         "UserName": "admin@corp.local", "ClientIP": "10.0.1.50", "Operation": "ReadData",
         "ObjectName": "/vol/data/file.txt", "Result": "Success"},
        {"timestamp": "2026-01-15T12:00:02Z", "EventID": "4656", "SVMName": "svm-prod-01",
         "UserName": "user1@corp.local", "ClientIP": "10.0.1.51", "Operation": "Open",
         "ObjectName": "/vol/data/secret.pdf", "Result": "Failure"},
    ]
    return "\n".join(json.dumps(l) for l in logs)


# Make the shared ONTAP audit parser importable, mirroring how deploy.sh bundles
# it next to the handler in the Lambda zip. Without this the handler falls back
# to JSON-only parsing and the audit-format tests fail.
# Imports are repeated locally so this block is self-contained regardless of
# where it sits relative to the rest of the file's imports.
import sys as _sys
from pathlib import Path as _Path

_shared_python_dir = str(_Path(__file__).resolve().parents[3] / "shared" / "python")
if _shared_python_dir not in _sys.path:
    _sys.path.insert(0, _shared_python_dir)

# The EMS handler delegates parsing to the ems_parser Lambda Layer. Adding the
# layer's source to sys.path lets the EMS tests exercise the real parser instead
# of the passthrough fallback.
_ems_parser_dir = str(
    _Path(__file__).resolve().parents[3]
    / "shared" / "lambda-layers" / "ems-parser" / "python"
)
if _ems_parser_dir not in _sys.path:
    _sys.path.insert(0, _ems_parser_dir)
