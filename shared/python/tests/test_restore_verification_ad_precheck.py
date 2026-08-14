"""Exercise the AD DC reachability pre-check in the restore-verification workflow.

Why this exists
---------------
On an AD-joined SVM, every S3 Access Point data operation needs the SVM to reach
its domain controllers, because ONTAP performs a unix->win reverse name-mapping
lookup per operation. When the DCs are unreachable, ListObjectsV2 returns
AccessDenied while HeadBucket still succeeds, so the symptom points at IAM or the
access point policy rather than at Active Directory.

The workflow guards against this before it wastes 30+ minutes on clone creation
and FSx discovery. That guard lived only in the inline `ZipFile` body of
CreateCloneFunction in shared/templates/restore-verification.yaml, and nothing
executed it: no test imported it, and template-embedded code is invisible to
pytest. The one gate specifically built for this failure had never been run.

These tests extract the inline source from the template and drive it with
scripted ONTAP responses, so the guard is exercised where it actually ships.
They also pin the ordering claim -- the check must precede access point creation,
since running after it would defeat the purpose.

Three fall-through cases are covered deliberately, and they are gaps rather than
features: the check only fails on `discovered_servers == []`. See the docstrings
on the `test_unverified_*` cases.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = REPO_ROOT / "shared/templates/restore-verification.yaml"


# --------------------------------------------------------------------------
# Extract the inline Lambda source from the CloudFormation template
# --------------------------------------------------------------------------


def _extract_zipfile(resource_name: str) -> str:
    """Return the ZipFile body of a Lambda resource, dedented.

    Parsed as text rather than YAML on purpose: the template uses !Sub, !Ref and
    !GetAtt, which a plain yaml.safe_load refuses, and registering constructors
    for them would risk resolving the very literal being tested.
    """
    lines = TEMPLATE.read_text(encoding="utf-8").splitlines()

    start = next(
        (i for i, line in enumerate(lines) if line.strip() == f"{resource_name}:"),
        None,
    )
    assert start is not None, f"{resource_name} not found in {TEMPLATE.name}"

    zip_at = next(
        (
            i
            for i in range(start, len(lines))
            if re.match(r"^\s*ZipFile:\s*\|\s*$", lines[i])
        ),
        None,
    )
    assert zip_at is not None, f"no ZipFile block under {resource_name}"

    body_indent = len(lines[zip_at]) - len(lines[zip_at].lstrip()) + 2
    collected: list[str] = []
    for line in lines[zip_at + 1 :]:
        if line.strip() and (len(line) - len(line.lstrip())) < body_indent:
            break
        collected.append(line)

    source = textwrap.dedent("\n".join(collected))
    assert "def lambda_handler" in source, (
        f"extracted block for {resource_name} has no lambda_handler; the "
        "extraction is wrong and every test below would be vacuous"
    )
    return source


@pytest.fixture(scope="module")
def create_clone_source() -> str:
    return _extract_zipfile("CreateCloneFunction")


@pytest.fixture
def clone_module(create_clone_source: str, monkeypatch: pytest.MonkeyPatch):
    """Execute the inline source in an isolated namespace.

    Credentials and HTTP are never reached: _request is replaced per test.
    """
    monkeypatch.setenv("ONTAP_MGMT_IP", "198.51.100.10")
    monkeypatch.setenv("ONTAP_CREDENTIALS_SECRET_ARN", "arn:aws:secretsmanager:x:y:secret:z")
    monkeypatch.setenv("STRICT_AD_CHECK", "false")
    namespace: dict[str, Any] = {"__name__": "create_clone_inline"}
    exec(compile(create_clone_source, "<CreateCloneFunction>", "exec"), namespace)
    return namespace


@pytest.fixture
def scan_module(monkeypatch: pytest.MonkeyPatch):
    """The scan step, which is where a missed AD problem actually surfaces."""
    monkeypatch.setenv("SUSPICIOUS_RATIO_THRESHOLD", "0.05")
    monkeypatch.setenv("SUSPICIOUS_MIN_COUNT", "20")
    namespace: dict[str, Any] = {"__name__": "scan_inline"}
    exec(compile(_extract_zipfile("ScanFunction"), "<ScanFunction>", "exec"), namespace)
    return namespace


class Recorder:
    """Scripted _request replacement that records the call sequence."""

    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, path: str, body: Any = None) -> dict:
        self.calls.append((method, path))
        for pattern, value in self.responses.items():
            if pattern in path:
                if isinstance(value, Exception):
                    raise value
                return value
        return {}

    @property
    def paths(self) -> list[str]:
        return [p for _, p in self.calls]


def _clone_ok() -> dict[str, Any]:
    """Responses for the clone-creation half, so tests focus on the AD half."""
    return {
        "/storage/volumes?name=": {"records": [{"uuid": "clone-uuid-1"}]},
        "/storage/volumes": {"job": {}},
    }


EVENT = {
    "svm_name": "svm-prod-01",
    "volume_name": "vol_data",
    "snapshot_name": "incident_response_20260708_143022",
    "vpc_id": "vpc-0123456789abcdef0",
    "fsvol_id": "fsvol-0123456789abcdef0",
}

CIFS_ENABLED = {"records": [{"ad_domain": {"fqdn": "corp.example.com"}}]}


# --------------------------------------------------------------------------
# The failure the check exists to catch
# --------------------------------------------------------------------------


def test_ad_joined_svm_with_no_discoverable_dcs_fails_fast(clone_module) -> None:
    recorder = Recorder({
        "/protocols/cifs/services": CIFS_ENABLED,
        "/protocols/cifs/domains": {"records": [{"discovered_servers": []}]},
        **_clone_ok(),
    })
    clone_module["_request"] = recorder

    with pytest.raises(RuntimeError, match="AD CONNECTIVITY FAILURE"):
        clone_module["lambda_handler"](dict(EVENT), None)

    assert not any(
        method == "POST" and path == "/storage/volumes"
        for method, path in recorder.calls
    ), (
        "the clone was created despite the AD check failing; failing fast is the "
        "entire point, since the alternative is discovering this 30+ minutes later"
    )


def test_failure_message_names_the_domain_and_the_downstream_symptom(clone_module) -> None:
    """The message has to redirect the reader away from IAM and the AP policy,
    which is where AccessDenied otherwise sends them."""
    clone_module["_request"] = Recorder({
        "/protocols/cifs/services": CIFS_ENABLED,
        "/protocols/cifs/domains": {"records": [{"discovered_servers": []}]},
        **_clone_ok(),
    })
    with pytest.raises(RuntimeError) as excinfo:
        clone_module["lambda_handler"](dict(EVENT), None)
    message = str(excinfo.value)
    assert "corp.example.com" in message
    assert "AccessDenied" in message
    assert "svm-prod-01" in message


# --------------------------------------------------------------------------
# Ordering: the guard is worthless after the access point exists
# --------------------------------------------------------------------------


def test_ad_check_runs_before_the_clone_is_created(clone_module) -> None:
    recorder = Recorder({
        "/protocols/cifs/services": CIFS_ENABLED,
        "/protocols/cifs/domains": {"records": [{"discovered_servers": [{"name": "dc1"}]}]},
        **_clone_ok(),
    })
    clone_module["_request"] = recorder
    clone_module["lambda_handler"](dict(EVENT), None)

    cifs_index = next(i for i, p in enumerate(recorder.paths) if "cifs" in p)
    post_index = next(
        i for i, (m, p) in enumerate(recorder.calls)
        if m == "POST" and p == "/storage/volumes"
    )
    assert cifs_index < post_index, (
        f"AD check ran after clone creation: {recorder.paths}"
    )


def test_state_machine_creates_the_clone_before_attaching_the_access_point() -> None:
    """The Lambda-level ordering above only matters if the state machine also
    puts CreateFlexClone ahead of AttachAccessPoint."""
    text = TEMPLATE.read_text(encoding="utf-8")
    assert '"StartAt": "CreateFlexClone"' in text, (
        "the workflow no longer starts at CreateFlexClone, so the AD pre-check "
        "is no longer the first thing that runs"
    )
    create_at = text.index('"CreateFlexClone"')
    attach_at = text.index('"AttachAccessPoint"')
    assert create_at < attach_at


def test_both_cifs_endpoints_are_queried(clone_module) -> None:
    """Presence of the CIFS service says the SVM is AD-joined; only the domains
    endpoint says whether any DC is reachable. Querying just the first would
    detect nothing."""
    recorder = Recorder({
        "/protocols/cifs/services": CIFS_ENABLED,
        "/protocols/cifs/domains": {"records": [{"discovered_servers": [{"name": "dc1"}]}]},
        **_clone_ok(),
    })
    clone_module["_request"] = recorder
    clone_module["lambda_handler"](dict(EVENT), None)

    joined = " ".join(recorder.paths)
    assert "/protocols/cifs/services" in joined
    assert "/protocols/cifs/domains" in joined
    assert "discovered_servers" in joined, (
        "the domains query must request the discovered_servers field; without it "
        "the response omits the field and the check silently passes"
    )


# --------------------------------------------------------------------------
# Cases that must NOT fail
# --------------------------------------------------------------------------


def test_non_ad_svm_proceeds(clone_module) -> None:
    """No CIFS service means no AD, so there is nothing to verify. Failing here
    would block every NFS-only SVM."""
    recorder = Recorder({
        "/protocols/cifs/services": {"records": []},
        **_clone_ok(),
    })
    clone_module["_request"] = recorder
    result = clone_module["lambda_handler"](dict(EVENT), None)

    assert result["volume_uuid"] == "clone-uuid-1"
    assert not any("cifs/domains" in p for p in recorder.paths), (
        "the domains endpoint was queried for an SVM with no CIFS service"
    )


def test_reachable_dcs_proceed_and_preserve_the_event(clone_module) -> None:
    """vpc_id must survive: AttachAccessPoint reads it from this step's output."""
    clone_module["_request"] = Recorder({
        "/protocols/cifs/services": CIFS_ENABLED,
        "/protocols/cifs/domains": {
            "records": [{"discovered_servers": [{"name": "dc1"}, {"name": "dc2"}]}]
        },
        **_clone_ok(),
    })
    result = clone_module["lambda_handler"](dict(EVENT), None)
    assert result["vpc_id"] == EVENT["vpc_id"]
    assert result["fsvol_id"] == EVENT["fsvol_id"]
    assert result["clone_name"].startswith("verify_vol_data_")


# --------------------------------------------------------------------------
# Ambiguous AD answers
#
# The check can only prove the bad case when ONTAP answers clearly (zero
# discovered controllers). Three answers are ambiguous: the field is absent,
# there are no domain records, or the endpoint errors. Each is
# indistinguishable from an unreachable domain and shares its downstream
# symptom.
#
# Default behaviour proceeds, because refusing to verify a restore candidate
# during an incident because an older ONTAP omits a field is worse than
# proceeding. Two things stop that being a silent pass: the verdict travels on
# the event so the scan step can name AD as the likely cause, and strict mode
# turns the ambiguity into an immediate failure.
# --------------------------------------------------------------------------

AMBIGUOUS = [
    ({"records": [{}]}, "field_absent", "discovered_servers field absent"),
    ({"records": []}, "no_domain_records", "no CIFS domain records"),
    (RuntimeError("ONTAP API GET /protocols/cifs/domains failed: HTTP 404"),
     "domains_api_error", "domains endpoint errors"),
]


@pytest.mark.parametrize("domains_response,reason,label", AMBIGUOUS)
def test_unverified_ad_state_proceeds_warns_and_records_the_verdict(
    clone_module, caplog: pytest.LogCaptureFixture, domains_response, reason, label
) -> None:
    clone_module["_request"] = Recorder({
        "/protocols/cifs/services": CIFS_ENABLED,
        "/protocols/cifs/domains": domains_response,
        **_clone_ok(),
    })

    with caplog.at_level("WARNING"):
        result = clone_module["lambda_handler"](dict(EVENT), None)

    assert result["volume_uuid"] == "clone-uuid-1", f"{label}: proceeds by default"
    assert result["ad_check"] == f"unverified:{reason}", (
        f"{label}: the verdict must travel on the event, otherwise the scan step "
        f"cannot tell an AD problem from a policy problem. Got {result.get('ad_check')!r}"
    )
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("UNVERIFIED" in w and "svm-prod-01" in w for w in warnings), (
        f"{label}: an unverified pass on an AD-joined SVM must be visible at "
        f"WARNING and name the SVM. Got: {warnings}"
    )


@pytest.mark.parametrize("domains_response,reason,label", AMBIGUOUS)
def test_strict_mode_fails_closed_on_every_ambiguous_answer(
    clone_module, monkeypatch: pytest.MonkeyPatch, domains_response, reason, label
) -> None:
    """Strict mode is the opt-in for deployments where every SVM is AD-joined
    and a false pass is the greater risk."""
    monkeypatch.setenv("STRICT_AD_CHECK", "true")
    recorder = Recorder({
        "/protocols/cifs/services": CIFS_ENABLED,
        "/protocols/cifs/domains": domains_response,
        **_clone_ok(),
    })
    clone_module["_request"] = recorder

    with pytest.raises(RuntimeError, match="AD CONNECTIVITY FAILURE .strict mode."):
        clone_module["lambda_handler"](dict(EVENT), None)

    assert not any(
        m == "POST" and p == "/storage/volumes" for m, p in recorder.calls
    ), f"{label}: strict mode raised but the clone was still created"


def test_strict_mode_does_not_affect_the_healthy_or_non_ad_paths(
    clone_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strict mode must only change the ambiguous verdicts. If it also blocked
    reachable-DC or non-AD SVMs it would be unusable."""
    monkeypatch.setenv("STRICT_AD_CHECK", "true")

    clone_module["_request"] = Recorder({
        "/protocols/cifs/services": CIFS_ENABLED,
        "/protocols/cifs/domains": {"records": [{"discovered_servers": [{"name": "dc1"}]}]},
        **_clone_ok(),
    })
    assert clone_module["lambda_handler"](dict(EVENT), None)["ad_check"] == "verified"

    clone_module["_request"] = Recorder({
        "/protocols/cifs/services": {"records": []}, **_clone_ok(),
    })
    assert clone_module["lambda_handler"](dict(EVENT), None)["ad_check"] == "not_applicable"


def test_verdict_is_recorded_for_the_healthy_and_non_ad_paths(clone_module) -> None:
    clone_module["_request"] = Recorder({
        "/protocols/cifs/services": CIFS_ENABLED,
        "/protocols/cifs/domains": {"records": [{"discovered_servers": [{"name": "dc1"}]}]},
        **_clone_ok(),
    })
    assert clone_module["lambda_handler"](dict(EVENT), None)["ad_check"] == "verified"


# --------------------------------------------------------------------------
# Both implementations of this workflow must agree that the guard exists
# --------------------------------------------------------------------------


def test_library_and_template_both_carry_the_ad_precheck() -> None:
    """This workflow is implemented twice: inline in the template, and in
    shared/python/restore_verification.py, which build-layer.sh ships as a
    Lambda layer. The library previously had no AD check at all, so which guard
    you got depended on which path you deployed.

    Asserting on both keeps them from drifting apart again silently.
    """
    template = TEMPLATE.read_text(encoding="utf-8")
    library = (REPO_ROOT / "shared/python/restore_verification.py").read_text(
        encoding="utf-8"
    )

    for name, text in (("template", template), ("library", library)):
        assert "AD CONNECTIVITY FAILURE" in text, f"{name} lost its AD pre-check"
        assert "/protocols/cifs/services" in text, f"{name} no longer detects AD join"
        assert "discovered_servers" in text, (
            f"{name} no longer checks domain controller reachability"
        )
        assert "not_applicable" in text and "unverified" in text, (
            f"{name} no longer records a verdict, so a later AccessDenied cannot "
            "be attributed"
        )

    assert "strict_ad_check" in library, "library lost its strict mode"
    assert "STRICT_AD_CHECK" in template, "template lost its strict mode"


# --------------------------------------------------------------------------
# The scan step must name the right cause for AccessDenied
# --------------------------------------------------------------------------


class _Denied(Exception):
    """Stands in for botocore ClientError(AccessDenied)."""

    def __init__(self, code: str = "AccessDenied"):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


def _scan_raising(scan_module, error: Exception):
    """Make the paginator raise on iteration."""
    class _Paginator:
        def paginate(self, **_):
            raise error
            yield  # pragma: no cover - generator marker

    class _S3:
        def get_paginator(self, _name):
            return _Paginator()

    scan_module["s3"] = _S3()
    scan_module["ClientError"] = _Denied
    return scan_module


@pytest.mark.parametrize("ad_check", ["unverified:field_absent", "unknown"])
def test_scan_blames_ad_when_reachability_was_never_confirmed(
    scan_module, ad_check: str
) -> None:
    """This is the misdiagnosis being fixed. AccessDenied here used to be
    attributed to access point policy propagation, but this workflow never sets
    an access point policy, so the reader searched for something absent."""
    _scan_raising(scan_module, _Denied())
    event = {**EVENT, "access_point_arn": "arn:aws:s3:::ap", "ad_check": ad_check}

    with pytest.raises(RuntimeError) as excinfo:
        scan_module["lambda_handler"](event, None)

    message = str(excinfo.value)
    assert "AD DC reachability was never confirmed" in message
    assert f"ad_check={ad_check}" in message
    assert "before investigating IAM or the access point policy" in message


def test_scan_rules_out_ad_when_reachability_was_confirmed(scan_module) -> None:
    """The inverse matters as much: with AD confirmed, pointing at AD wastes the
    responder's time."""
    _scan_raising(scan_module, _Denied())
    event = {**EVENT, "access_point_arn": "arn:aws:s3:::ap", "ad_check": "verified"}

    with pytest.raises(RuntimeError) as excinfo:
        scan_module["lambda_handler"](event, None)

    message = str(excinfo.value)
    assert "AD is not the cause" in message
    assert "IAM identity policy" in message


def test_scan_does_not_swallow_other_client_errors(scan_module) -> None:
    _scan_raising(scan_module, _Denied("NoSuchBucket"))
    event = {**EVENT, "access_point_arn": "arn:aws:s3:::ap", "ad_check": "verified"}
    with pytest.raises(_Denied):
        scan_module["lambda_handler"](event, None)


def test_retry_comment_no_longer_blames_a_policy_this_workflow_never_sets() -> None:
    """The state machine retries AccessDenied five times. Its comment used to
    attribute the error to put_access_point_policy propagation -- an API this
    workflow never calls, so a reader following that lead finds nothing and
    concludes propagation delay.

    Asserted on the attribution, not the mere mention: the current comment names
    the API in order to correct the record, which is the opposite problem.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    retry_comment = text.split('"ErrorEquals": ["AccessDenied"]')[1][:1200]

    assert "propagation takes a few seconds after put_access_point_policy" not in retry_comment, (
        "the retry comment still attributes AccessDenied to access point policy "
        "propagation"
    )
    assert "never calls that API" in retry_comment, (
        "the retry comment should state that this workflow does not set an access "
        "point policy, so the next reader does not go looking for one"
    )
    assert "ad_check" in retry_comment, (
        "the retry comment should point at the AD verdict as the other cause"
    )
