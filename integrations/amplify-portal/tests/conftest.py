"""Fixtures for the amplify-portal audit correlator tests.

The handler imports ``observability`` and ``ontap_audit_parser`` the way it does
in Lambda -- as top-level modules provided by the shared layer -- so the shared
directory goes on the path rather than the handler being rewritten to use
package-relative imports it would not have at runtime.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

# Every vendor ships a module named `handler`, so a cached one from whichever
# vendor's suite ran first would satisfy `import handler` here and the tests
# would silently exercise the wrong code. Purged, following the same pattern as
# the other vendor conftests.
for _module in ("handler", "observability", "ontap_audit_parser"):
    sys.modules.pop(_module, None)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SHARED = str(_REPO_ROOT / "shared" / "python")
_LAMBDA = str(pathlib.Path(__file__).resolve().parents[1] / "lambda")
for _path in (_SHARED, _LAMBDA):
    if _path not in sys.path:
        sys.path.insert(0, _path)


# Real audit output, captured on FSx for ONTAP, ONTAP 9.18.1P3D1, xml audit
# format. The volume-prefixed ObjectName and the "Not Present" subject are both
# properties of the real output, not simplifications.
S3_CREATE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Event>
  <System>
    <Computer>FsxId0123456789abcdef0/svm1</Computer>
    <TimeCreated SystemTime="2026-08-25T23:32:21.000000000Z"/>
    <EventID>4656</EventID>
  </System>
  <EventData>
    <Data Name="Source">HTTP</Data>
    <Data Name="EventName">Create Object</Data>
    <Data Name="SubjectUserName">Not Present</Data>
    <Data Name="SubjectDomainName">Not Present</Data>
    <Data Name="SubjectIP">203.0.113.10</Data>
    <Data Name="ObjectName">(vol1);/data/object.txt</Data>
    <Data Name="ObjectType">File</Data>
  </EventData>
</Event>
<Event>
  <System>
    <Computer>FsxId0123456789abcdef0/svm1</Computer>
    <TimeCreated SystemTime="2026-08-25T23:35:02.000000000Z"/>
    <EventID>4663</EventID>
  </System>
  <EventData>
    <Data Name="Source">CIFS</Data>
    <Data Name="EventName">Write Object</Data>
    <Data Name="SubjectUserName">svcuser</Data>
    <Data Name="SubjectDomainName">EXAMPLE</Data>
    <Data Name="SubjectIP">10.0.0.10</Data>
    <Data Name="ObjectName">(vol1);/data/object.txt</Data>
    <Data Name="ObjectType">File</Data>
  </EventData>
</Event>
"""


@pytest.fixture
def s3_create_xml() -> bytes:
    """One S3-access-path event and one SMB event on the same object.

    The pair matters: the SMB record has the same file name, so it is what a
    file-name-only correlation would wrongly attribute to a portal user.
    """
    return S3_CREATE_XML.encode("utf-8")


@pytest.fixture
def handler_env(monkeypatch):
    """Set the environment the handler reads, without touching X-Ray."""
    monkeypatch.setenv("FSX_S3_ACCESS_POINT_ARN", "arn:aws:s3:ap-northeast-1:123456789012:accesspoint/ap")
    monkeypatch.setenv("AUDIT_LOG_PREFIX", "audit/")
    monkeypatch.setenv("CHECKPOINT_PARAM_NAME", "/fsxn-appsig/test/last-processed-key")
    monkeypatch.setenv("MAX_KEYS_PER_RUN", "50")
    monkeypatch.setenv("EMF_NAMESPACE", "TestNamespace")
    monkeypatch.setenv("ENABLE_XRAY", "false")
    monkeypatch.setenv("ENVIRONMENT", "test")
