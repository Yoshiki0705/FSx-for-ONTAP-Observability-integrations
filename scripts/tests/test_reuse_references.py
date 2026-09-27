"""Duplication_Check: net-new IaC must reference existing assets, not copy them.

Why this exists
---------------
The pipeline verification environments (integrations/pipeline-verification/) are
built on a hard constraint: reuse named existing assets, do not reinvent them
(spec requirement 2). Two structural invariants over the whole artifact set can
be machine-checked, and this gate is where they are:

  Property 1 — every same-repo Reuse_Reference resolves to a real path or stack
               reference in this repository. A sibling-repo reference is checked
               only when the sibling checkout is present.
  Property 4 — no net-new file reproduces an existing asset's CloudFormation
               resource blocks above a similarity threshold, unless it is
               declared net-new with a rationale (a genuinely new layer such as
               a Prometheus server or Kafka broker).

Execution point is a CI gate, not preflight: duplication detection is a static
check over the artifact file set and does not depend on a deploy environment.
This file lives in scripts/tests/, which is in the Makefile's PYTEST_DIRS, so it
runs under `make test-py` and `make drift` from the same path list locally and
in CI. No workflow change is needed to register it.

Guarding the guard
------------------
Every failure mode of this checker exits 0 and looks like a clean tree:

  - the YAML fails to load, so no references are checked;
  - the resource-block normaliser returns nothing, so similarity is always 0;
  - the existing-asset walk returns an empty set, so nothing to compare against.

So `selftest()` asserts both directions — a fabricated file that copies an
existing asset MUST be flagged, and a declared net-new file MUST NOT be — and
the tests below pin the real tree so a broken walk cannot pass vacuously.

Usage:
  python3 scripts/tests/test_reuse_references.py --selftest   # guard-the-guard
  python3 -m pytest scripts/tests/test_reuse_references.py     # full gate
"""

from __future__ import annotations

import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
REUSE_FILE = (
    REPO_ROOT
    / "integrations"
    / "pipeline-verification"
    / "shared"
    / "reuse-references.yaml"
)

# Existing assets net-new files are compared against. template*.yaml so EMS,
# FPolicy and remediation stacks are covered too, matching the Makefile's
# CFN_TEMPLATES shape.
EXISTING_ASSET_GLOBS = (
    "shared/templates/*.yaml",
    "integrations/*/template*.yaml",
)

# Jaccard similarity of normalized resource-block hash sets above which a
# net-new file is a suspected degraded copy. A net-new layer (Prometheus, Kafka,
# QuestDB) shares none of an existing asset's resource shapes, so it scores near
# 0; a file that copied an existing template scores near 1. 0.5 sits well clear
# of both. The rationale allowlist covers the legitimate case regardless.
SIMILARITY_THRESHOLD = 0.5

VALID_KINDS = {"same-repo-path", "sibling-repo-path"}
VALID_MECHANISMS = {
    "nested-stack",
    "cross-stack-import",
    "sam-include",
    "stack-reference",
    "doc-pointer",
}


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class Finding:
    """One problem the gate reports. severity is a short machine-readable tag."""

    severity: str
    message: str


UNRESOLVED = "unresolved-reference"
BAD_SCHEMA = "bad-schema"
DUPLICATE = "suspected-duplicate"

# Sibling-repo existence is inconclusive, never a failure, when the checkout is
# absent (requirement 2-15).
SKIPPED_SIBLING = "sibling-absent-skipped"


@dataclass
class ReuseModel:
    """Parsed reuse-references.yaml."""

    patterns: dict = field(default_factory=dict)
    sibling_candidate_dirs: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def load_model(text: str) -> ReuseModel:
    """Parse the reuse declaration. Raises on structurally invalid YAML."""
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("reuse-references.yaml must be a mapping at the top level")
    sibling = data.get("sibling_repo", {}) or {}
    return ReuseModel(
        patterns=data.get("patterns", {}) or {},
        sibling_candidate_dirs=list(sibling.get("candidate_dirs", []) or []),
    )


def sibling_root(model: ReuseModel, repo_root: Path = REPO_ROOT) -> Path | None:
    """First sibling checkout that exists, or None. Tries each candidate name."""
    for name in model.sibling_candidate_dirs:
        candidate = repo_root.parent / name
        if candidate.is_dir():
            return candidate
    return None


# --------------------------------------------------------------------------
# Property 1: reference resolution
# --------------------------------------------------------------------------


def _resolve_same_repo(ref: str, repo_root: Path) -> bool:
    """A same-repo ref resolves to a real path, or a real path#LogicalId.

    "<template>#<LogicalId>" is an IaC-level stack reference: the file must exist
    and declare that logical ID under Resources.
    """
    if "#" in ref:
        path_part, logical_id = ref.split("#", 1)
        target = repo_root / path_part
        if not target.is_file():
            return False
        try:
            doc = yaml.safe_load(_read_cfn(target)) or {}
        except yaml.YAMLError:
            return False
        return logical_id in (doc.get("Resources", {}) or {})
    return (repo_root / ref).exists()


def resolve_references(
    model: ReuseModel,
    repo_root: Path = REPO_ROOT,
    sibling: Path | None = "__auto__",  # type: ignore[assignment]
) -> list[Finding]:
    """Check every reference resolves. Property 1.

    same-repo-path: MUST resolve, always.
    sibling-repo-path: checked only when the sibling checkout is present.
    """
    if sibling == "__auto__":
        sibling = sibling_root(model, repo_root)

    findings: list[Finding] = []
    for pattern, spec in model.patterns.items():
        for entry in (spec or {}).get("reuse", []) or []:
            ref = entry.get("ref")
            kind = entry.get("kind")
            mechanism = entry.get("mechanism")
            if not ref or kind not in VALID_KINDS or mechanism not in VALID_MECHANISMS:
                findings.append(
                    Finding(
                        BAD_SCHEMA,
                        f"{pattern}: malformed reuse entry "
                        f"(ref={ref!r}, kind={kind!r}, mechanism={mechanism!r})",
                    )
                )
                continue

            if kind == "same-repo-path":
                if not _resolve_same_repo(ref, repo_root):
                    findings.append(
                        Finding(
                            UNRESOLVED,
                            f"{pattern}: same-repo reference does not resolve: {ref}",
                        )
                    )
            else:  # sibling-repo-path
                if sibling is None:
                    continue  # absent sibling: not a failure (requirement 2-15)
                rel = ref.split(":", 1)[1] if ":" in ref else ref
                if not (sibling / rel).exists():
                    findings.append(
                        Finding(
                            UNRESOLVED,
                            f"{pattern}: sibling reference does not resolve under "
                            f"{sibling}: {ref}",
                        )
                    )
    return findings


# --------------------------------------------------------------------------
# Property 4: degraded-copy detection via normalized resource-block similarity
# --------------------------------------------------------------------------

# ONTAP/CloudFormation intrinsic tags so PyYAML does not choke on !Ref, !Sub etc.
_CFN_TAGS = (
    "Ref Sub GetAtt GetAZs ImportValue Join Select Split Base64 Cidr "
    "FindInMap If Not And Or Equals Condition Transform Length ToJsonString"
).split()


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that tolerates CloudFormation short-form intrinsic tags."""


def _cfn_multi(loader, tag_suffix, node):  # noqa: ANN001
    if isinstance(node, yaml.ScalarNode):
        return {tag_suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {tag_suffix: loader.construct_sequence(node)}
    return {tag_suffix: loader.construct_mapping(node)}


for _t in _CFN_TAGS:
    _CfnLoader.add_constructor(f"!{_t}", lambda loader, node, s=_t: _cfn_multi(loader, s, node))


def _read_cfn(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _canonical(value) -> str:  # noqa: ANN001
    """Order-independent canonical string of a Properties value.

    Logical IDs and comments are already gone (comments never survive the YAML
    load; logical IDs are the keys we drop). Sorting dict keys means indentation
    and key order cannot hide a copy.
    """
    if isinstance(value, dict):
        return "{" + ",".join(f"{k}={_canonical(value[k])}" for k in sorted(value)) + "}"
    if isinstance(value, list):
        return "[" + ",".join(sorted(_canonical(v) for v in value)) + "]"
    return repr(value)


def resource_block_hashes(text: str) -> set[str]:
    """Normalized hash per resource block: Type + canonical Properties.

    Returns an empty set for a document with no Resources. The logical ID (the
    key under Resources) is deliberately excluded so renaming a copied resource
    does not evade detection.
    """
    try:
        doc = yaml.load(text, Loader=_CfnLoader) or {}
    except yaml.YAMLError:
        return set()
    resources = (doc.get("Resources") or {}) if isinstance(doc, dict) else {}
    hashes: set[str] = set()
    for _logical_id, body in resources.items():
        if not isinstance(body, dict):
            continue
        rtype = body.get("Type", "")
        props = body.get("Properties", {})
        blob = f"{rtype}|{_canonical(props)}"
        hashes.add(hashlib.sha256(blob.encode("utf-8")).hexdigest())
    return hashes


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def existing_asset_hashes(repo_root: Path = REPO_ROOT) -> dict[str, set[str]]:
    """Map each existing CloudFormation asset path to its resource-block hashes."""
    out: dict[str, set[str]] = {}
    for glob in EXISTING_ASSET_GLOBS:
        for path in sorted(repo_root.glob(glob)):
            # Do not compare pipeline-verification's own net-new files here.
            if "pipeline-verification" in path.parts:
                continue
            out[str(path.relative_to(repo_root))] = resource_block_hashes(
                _read_cfn(path)
            )
    return out


def declared_net_new(model: ReuseModel) -> dict[str, str]:
    """Map net-new file path -> rationale (empty string if none), across patterns."""
    out: dict[str, str] = {}
    for spec in model.patterns.values():
        for entry in (spec or {}).get("net_new", []) or []:
            if isinstance(entry, str):
                out[entry] = ""
            elif isinstance(entry, dict) and entry.get("path"):
                out[entry["path"]] = entry.get("rationale", "") or ""
    return out


def detect_duplicates(
    model: ReuseModel,
    repo_root: Path = REPO_ROOT,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[Finding]:
    """Report net-new files that reproduce an existing asset. Property 4.

    A net-new file declared with a non-empty rationale is allowlisted: the
    rationale records why it is a genuinely new layer. A file whose resource
    blocks are similar to an existing asset AND has no rationale is a suspected
    degraded copy.
    """
    existing = existing_asset_hashes(repo_root)
    findings: list[Finding] = []
    for rel_path, rationale in declared_net_new(model).items():
        target = repo_root / rel_path
        if not target.is_file():
            # A declared-but-absent net-new file is a Stage-0-legitimate state
            # (the template is added in a later stage). Nothing to compare.
            continue
        new_hashes = resource_block_hashes(_read_cfn(target))
        if not new_hashes:
            continue
        for asset_path, asset_hashes in existing.items():
            score = jaccard(new_hashes, asset_hashes)
            if score >= threshold and not rationale.strip():
                findings.append(
                    Finding(
                        DUPLICATE,
                        f"{rel_path} reproduces {asset_path} "
                        f"(similarity {score:.2f} >= {threshold}) with no net_new "
                        "rationale. Reference the existing asset, or declare the "
                        "rationale in reuse-references.yaml if this is a new layer.",
                    )
                )
    return findings


def run_all(
    model: ReuseModel, repo_root: Path = REPO_ROOT
) -> list[Finding]:
    return resolve_references(model, repo_root) + detect_duplicates(model, repo_root)


# --------------------------------------------------------------------------
# selftest: guard the guard
# --------------------------------------------------------------------------

# An existing asset every checkout has, used to fabricate a copy for the
# negative control below.
_SELFTEST_ASSET = "shared/templates/s3-access-point.yaml"


def selftest(repo_root: Path = REPO_ROOT) -> int:
    """Prove the checker fails on known-bad input and passes on known-good.

    Returns 0 on success, 1 if any assertion about the checker's own behavior is
    violated. Does not touch the tree.
    """
    failures: list[str] = []

    # 1. Reference resolution must flag a bad same-repo path.
    bad_ref = ReuseModel(
        patterns={
            "pattern-x": {
                "reuse": [
                    {
                        "ref": "shared/templates/does-not-exist.yaml",
                        "kind": "same-repo-path",
                        "mechanism": "nested-stack",
                    }
                ],
                "net_new": [],
            }
        }
    )
    if not any(f.severity == UNRESOLVED for f in resolve_references(bad_ref, repo_root, sibling=None)):
        failures.append("resolve_references did not flag a missing same-repo path")

    # 2. A good same-repo path must NOT be flagged.
    good_ref = ReuseModel(
        patterns={
            "pattern-x": {
                "reuse": [
                    {
                        "ref": _SELFTEST_ASSET,
                        "kind": "same-repo-path",
                        "mechanism": "nested-stack",
                    }
                ]
            }
        }
    )
    if resolve_references(good_ref, repo_root, sibling=None):
        failures.append("resolve_references flagged a resolvable same-repo path")

    # 3. An absent sibling reference must be skipped, not failed.
    sibling_ref = ReuseModel(
        patterns={
            "pattern-x": {
                "reuse": [
                    {
                        "ref": "ontap-edge-to-cloud-ai:cloud/nonexistent/thing.yaml",
                        "kind": "sibling-repo-path",
                        "mechanism": "doc-pointer",
                    }
                ]
            }
        }
    )
    if resolve_references(sibling_ref, repo_root, sibling=None):
        failures.append("an absent sibling reference was treated as a failure")

    # 4. Duplication: a fabricated file copying an existing asset, declared
    #    net-new WITHOUT a rationale, must be flagged.
    asset_text = _read_cfn(repo_root / _SELFTEST_ASSET)
    new_hashes = resource_block_hashes(asset_text)
    existing = existing_asset_hashes(repo_root)
    if not new_hashes:
        failures.append("resource_block_hashes returned nothing for a real template")
    else:
        best = max((jaccard(new_hashes, h) for h in existing.values()), default=0.0)
        if best < SIMILARITY_THRESHOLD:
            failures.append(
                "a verbatim copy of an existing asset scored below the threshold; "
                "the similarity metric is not detecting copies"
            )

    # 5. A net-new file sharing no resource shapes must score ~0 (no false alarm).
    novel = (
        "Resources:\n"
        "  PrometheusServer:\n"
        "    Type: AWS::EC2::Instance\n"
        "    Properties:\n"
        "      InstanceType: m6i.large\n"
        "      ImageId: ami-0123456789abcdef0\n"
    )
    novel_hashes = resource_block_hashes(novel)
    if not novel_hashes:
        failures.append("resource_block_hashes returned nothing for a net-new template")
    else:
        best_novel = max((jaccard(novel_hashes, h) for h in existing.values()), default=0.0)
        if best_novel >= SIMILARITY_THRESHOLD:
            failures.append(
                "a genuinely novel resource set scored above the threshold; the metric "
                "would flag legitimate net-new work"
            )

    for f in failures:
        print(f"selftest FAIL: {f}", file=sys.stderr)
    if failures:
        return 1
    print("selftest: 5 check(s) passed")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if not REUSE_FILE.is_file():
        print(f"ERROR: {REUSE_FILE} is missing", file=sys.stderr)
        return 1
    model = load_model(REUSE_FILE.read_text(encoding="utf-8"))
    findings = run_all(model)
    for f in findings:
        print(f"[{f.severity}] {f.message}", file=sys.stderr)
    if findings:
        print(f"\n{len(findings)} Duplication_Check finding(s).", file=sys.stderr)
        return 1
    print("Duplication_Check: all reuse references resolve; no degraded copies.")
    return 0


# ==========================================================================
# pytest suite
# ==========================================================================


@pytest.fixture(scope="module")
def model() -> ReuseModel:
    assert REUSE_FILE.is_file(), f"{REUSE_FILE} is missing"
    return load_model(REUSE_FILE.read_text(encoding="utf-8"))


# --- Guard the guard ------------------------------------------------------


def test_selftest_passes() -> None:
    assert selftest() == 0


def test_existing_asset_walk_is_nonempty() -> None:
    """An empty asset set would make every duplication comparison vacuous."""
    assets = existing_asset_hashes()
    assert len(assets) >= 10, (
        f"only {len(assets)} existing CloudFormation assets found; the glob is "
        "probably broken, which would make duplication detection pass without "
        "comparing against anything"
    )


def test_a_real_template_yields_resource_hashes() -> None:
    """A normaliser that returns nothing makes similarity always 0."""
    text = _read_cfn(REPO_ROOT / _SELFTEST_ASSET)
    assert resource_block_hashes(text), (
        "resource_block_hashes returned nothing for a real template; the parser "
        "or the Resources walk is broken"
    )


# --- The real tree (Property 1 and 4 over the actual artifacts) -----------


def test_reuse_file_loads(model: ReuseModel) -> None:
    assert model.patterns, "no patterns declared in reuse-references.yaml"


def test_every_pattern_is_declared(model: ReuseModel) -> None:
    for p in ("pattern-1", "pattern-2", "pattern-3", "pattern-4", "pattern-5"):
        assert p in model.patterns, f"{p} missing from reuse-references.yaml"


def test_all_same_repo_references_resolve(model: ReuseModel) -> None:
    """Property 1 over the real declaration."""
    findings = resolve_references(model)
    unresolved = [f.message for f in findings if f.severity in (UNRESOLVED, BAD_SCHEMA)]
    assert not unresolved, "unresolved / malformed reuse references:\n  " + "\n  ".join(
        unresolved
    )


def test_no_declared_net_new_is_a_degraded_copy(model: ReuseModel) -> None:
    """Property 4 over the real declaration."""
    findings = detect_duplicates(model)
    dupes = [f.message for f in findings if f.severity == DUPLICATE]
    assert not dupes, "suspected degraded copies:\n  " + "\n  ".join(dupes)


# --- Unit tests: task 2.4 (resolvable/unresolvable, threshold, rationale) --


def test_resolvable_same_repo_reference_is_not_flagged() -> None:
    m = ReuseModel(
        patterns={
            "p": {"reuse": [
                {"ref": _SELFTEST_ASSET, "kind": "same-repo-path", "mechanism": "nested-stack"}
            ]}
        }
    )
    assert resolve_references(m, sibling=None) == []


def test_unresolvable_same_repo_reference_is_flagged() -> None:
    m = ReuseModel(
        patterns={
            "p": {"reuse": [
                {"ref": "shared/templates/nope.yaml", "kind": "same-repo-path", "mechanism": "nested-stack"}
            ]}
        }
    )
    findings = resolve_references(m, sibling=None)
    assert [f.severity for f in findings] == [UNRESOLVED]


def test_stack_reference_logical_id_must_exist() -> None:
    """A '<template>#<LogicalId>' reference checks the logical ID is present."""
    # A logical ID that does not exist in the template must fail.
    m_bad = ReuseModel(
        patterns={
            "p": {"reuse": [
                {"ref": f"{_SELFTEST_ASSET}#NoSuchLogicalId", "kind": "same-repo-path", "mechanism": "stack-reference"}
            ]}
        }
    )
    assert any(f.severity == UNRESOLVED for f in resolve_references(m_bad, sibling=None))


def test_absent_sibling_reference_is_skipped_not_failed() -> None:
    m = ReuseModel(
        patterns={
            "p": {"reuse": [
                {"ref": "ontap-edge-to-cloud-ai:cloud/none.yaml", "kind": "sibling-repo-path", "mechanism": "doc-pointer"}
            ]}
        }
    )
    assert resolve_references(m, sibling=None) == []


def test_present_sibling_reference_is_checked(tmp_path: Path) -> None:
    """When a sibling checkout exists, a missing path in it IS a failure."""
    sib = tmp_path / "edge-to-cloud-ai"
    sib.mkdir()
    m = ReuseModel(
        patterns={
            "p": {"reuse": [
                {"ref": "ontap-edge-to-cloud-ai:cloud/none.yaml", "kind": "sibling-repo-path", "mechanism": "doc-pointer"}
            ]}
        }
    )
    findings = resolve_references(m, sibling=sib)
    assert [f.severity for f in findings] == [UNRESOLVED]
    # And a path that DOES exist in the sibling resolves.
    (sib / "cloud").mkdir()
    (sib / "cloud" / "none.yaml").write_text("Resources: {}\n", encoding="utf-8")
    assert resolve_references(m, sibling=sib) == []


def test_malformed_reuse_entry_is_flagged() -> None:
    m = ReuseModel(patterns={"p": {"reuse": [{"ref": "x", "kind": "bogus", "mechanism": "nested-stack"}]}})
    assert any(f.severity == BAD_SCHEMA for f in resolve_references(m, sibling=None))


def test_similarity_above_threshold_without_rationale_is_flagged(tmp_path: Path) -> None:
    """A net-new file copying an existing asset, no rationale -> flagged."""
    repo = _mini_repo(tmp_path)
    # net-new file is a verbatim copy of the existing asset.
    copy_path = repo / "integrations" / "pipeline-verification" / "pattern-2-prometheus" / "template.yaml"
    copy_path.parent.mkdir(parents=True)
    copy_path.write_text((repo / "shared/templates/existing.yaml").read_text(), encoding="utf-8")
    model = ReuseModel(
        patterns={"pattern-2": {"net_new": [
            {"path": "integrations/pipeline-verification/pattern-2-prometheus/template.yaml"}
        ]}}
    )
    findings = detect_duplicates(model, repo_root=repo)
    assert any(f.severity == DUPLICATE for f in findings)


def test_similarity_above_threshold_with_rationale_is_allowlisted(tmp_path: Path) -> None:
    """Same copy, but a rationale is declared -> allowlisted (not flagged)."""
    repo = _mini_repo(tmp_path)
    copy_path = repo / "integrations" / "pipeline-verification" / "pattern-2-prometheus" / "template.yaml"
    copy_path.parent.mkdir(parents=True)
    copy_path.write_text((repo / "shared/templates/existing.yaml").read_text(), encoding="utf-8")
    model = ReuseModel(
        patterns={"pattern-2": {"net_new": [
            {
                "path": "integrations/pipeline-verification/pattern-2-prometheus/template.yaml",
                "rationale": "Prometheus server is a new layer with no existing asset.",
            }
        ]}}
    )
    assert detect_duplicates(model, repo_root=repo) == []


def test_novel_resource_set_below_threshold_is_not_flagged(tmp_path: Path) -> None:
    """A genuinely new layer sharing no resource shapes -> not flagged even without rationale."""
    repo = _mini_repo(tmp_path)
    novel_path = repo / "integrations" / "pipeline-verification" / "pattern-2-prometheus" / "template.yaml"
    novel_path.parent.mkdir(parents=True)
    novel_path.write_text(
        "Resources:\n"
        "  PrometheusServer:\n"
        "    Type: AWS::EC2::Instance\n"
        "    Properties:\n"
        "      InstanceType: m6i.large\n",
        encoding="utf-8",
    )
    model = ReuseModel(
        patterns={"pattern-2": {"net_new": [
            {"path": "integrations/pipeline-verification/pattern-2-prometheus/template.yaml"}
        ]}}
    )
    assert detect_duplicates(model, repo_root=repo) == []


def test_logical_id_rename_does_not_evade_detection() -> None:
    """Renaming the resource's logical ID must not change its normalized hash."""
    a = "Resources:\n  Foo:\n    Type: AWS::S3::Bucket\n    Properties:\n      BucketName: x\n"
    b = "Resources:\n  Bar:\n    Type: AWS::S3::Bucket\n    Properties:\n      BucketName: x\n"
    assert resource_block_hashes(a) == resource_block_hashes(b)


def test_property_order_does_not_change_hash() -> None:
    """Key order / indentation must not let a copy slip through."""
    a = "Resources:\n  Foo:\n    Type: AWS::S3::Bucket\n    Properties:\n      A: 1\n      B: 2\n"
    b = "Resources:\n  Foo:\n    Type: AWS::S3::Bucket\n    Properties:\n      B: 2\n      A: 1\n"
    assert resource_block_hashes(a) == resource_block_hashes(b)


def test_jaccard_edges() -> None:
    assert jaccard(set(), set()) == 0.0
    assert jaccard({"a"}, set()) == 0.0
    assert jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert jaccard({"a", "b"}, {"a", "c"}) == pytest.approx(1 / 3)


def _mini_repo(tmp_path: Path) -> Path:
    """A throwaway repo root with one existing asset, for duplication unit tests."""
    repo = tmp_path / "repo"
    (repo / "shared" / "templates").mkdir(parents=True)
    (repo / "shared" / "templates" / "existing.yaml").write_text(
        "Resources:\n"
        "  Queue:\n"
        "    Type: AWS::SQS::Queue\n"
        "    Properties:\n"
        "      QueueName: audit\n"
        "      VisibilityTimeout: 300\n"
        "  Role:\n"
        "    Type: AWS::IAM::Role\n"
        "    Properties:\n"
        "      RoleName: audit-role\n",
        encoding="utf-8",
    )
    return repo


if __name__ == "__main__":
    sys.exit(main())
