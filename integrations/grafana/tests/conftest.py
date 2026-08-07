"""Pytest fixtures for Grafana integration tests."""


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
import pytest


@pytest.fixture(autouse=True)
def env_vars(monkeypatch):
    monkeypatch.setenv("LOKI_ENDPOINT", "https://logs-prod.grafana.net")
    monkeypatch.setenv("LOKI_TENANT_ID", "")
    monkeypatch.setenv("API_KEY_SECRET_ARN", "arn:aws:secretsmanager:ap-northeast-1:123:secret:grafana")
    monkeypatch.setenv("S3_ACCESS_POINT_ARN", "arn:aws:s3:ap-northeast-1:123:accesspoint/fsxn-audit")
    monkeypatch.setenv("S3_KEY_PREFIX", "audit/svm-prod-01/")
    monkeypatch.setenv("CHECKPOINT_PARAM_NAME", "/fsxn-grafana/test-stack/last-processed-key")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")


@pytest.fixture
def sample_logs():
    logs = [
        {"timestamp": "2026-01-15T12:00:01Z", "EventID": "4663", "SVMName": "svm-prod-01",
         "UserName": "admin@corp.local", "Operation": "ReadData", "Result": "Success"},
        {"timestamp": "2026-01-15T12:00:02Z", "EventID": "4656", "SVMName": "svm-prod-01",
         "UserName": "user1@corp.local", "Operation": "Open", "Result": "Failure"},
    ]
    return "\n".join(json.dumps(l) for l in logs)


@pytest.fixture
def scheduler_event():
    return {
        "source": "scheduler",
        "s3_access_point_arn": "arn:aws:s3:ap-northeast-1:123:accesspoint/fsxn-audit",
        "prefix": "audit/svm-prod-01/",
    }


@pytest.fixture
def s3_event():
    return {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "my-bucket"},
                    "object": {"key": "audit/svm-prod-01/2026/01/15/audit.json"},
                }
            }
        ]
    }


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
