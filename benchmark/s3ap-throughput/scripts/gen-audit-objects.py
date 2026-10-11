#!/usr/bin/env python3
"""Generate valid, parseable ONTAP XML audit-log files of target sizes.

Why this script exists
----------------------
The "process" test mode of the benchmark Lambda (benchmark/s3ap-throughput/
handler.py) reads each object and then parses it with the canonical ONTAP audit
parser (shared/python/ontap_audit_parser.py). Random bytes would make
``detect_format`` return "unknown" and the parse return nothing, so the parse
leg would measure the cost of parsing an empty document rather than a real one.
This script writes XML the parser recognises: one namespaced ``<Event>`` block
(the Windows Event Log schema that ``vserver audit create -format xml`` emits),
repeated under one ``<Events>`` root until the target byte size is reached.

It writes files LOCALLY only and makes no AWS or boto3 call. Uploading the
generated files to an S3 Access Point is a separate, billed step done by the
operator.

Target files (the rows of the benchmark's size table):
    5 KB   x10
    200 KB x50
    200 KB x100
    2 MB   x100

Usage:
    python3 benchmark/s3ap-throughput/scripts/gen-audit-objects.py --out-dir ./objects
    python3 benchmark/s3ap-throughput/scripts/gen-audit-objects.py --verify

``--verify`` generates a 5 KB file in a temporary directory and asserts the
parser returns a non-zero event count, so the generator is known to produce
parseable data without needing AWS.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# One namespaced ONTAP audit event, matching the schema the parser tests use
# (shared/python/tests/test_ontap_audit_parser.py NAMESPACED_XML and
# shared/lambda-layers/log-parser/tests/conftest.py namespaced_ontap_xml). The
# IP is an RFC 5737 documentation address.
EVENT_BLOCK = """<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
  <System>
    <EventID>4663</EventID>
    <TimeCreated SystemTime="2026-08-07T04:00:00Z"/>
    <Computer>svm-bench-01</Computer>
  </System>
  <EventData>
    <Data Name="SubjectUserName">BENCH\\reader</Data>
    <Data Name="ObjectName">/vol/data/object.txt</Data>
    <Data Name="ObjectType">ReadData</Data>
    <Data Name="IpAddress">198.51.100.10</Data>
    <Data Name="Keywords">Audit Success</Data>
  </EventData>
</Event>
"""

XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'
EVENTS_OPEN = "<Events>\n"
EVENTS_CLOSE = "</Events>\n"

_BYTES_PER_KB = 1024
_BYTES_PER_MB = 1024 * 1024

# (filename, target_bytes, count) for each row of the benchmark size table.
TARGET_FILES: list[tuple[str, int, int]] = [
    ("audit-5kb-{i:03d}.xml", 5 * _BYTES_PER_KB, 10),
    ("audit-200kb-50-{i:03d}.xml", 200 * _BYTES_PER_KB, 50),
    ("audit-200kb-100-{i:03d}.xml", 200 * _BYTES_PER_KB, 100),
    ("audit-2mb-{i:03d}.xml", 2 * _BYTES_PER_MB, 100),
]


def build_xml(target_bytes: int) -> str:
    """Build a parseable ONTAP XML audit document of at least ``target_bytes``.

    Repeats ``EVENT_BLOCK`` under one ``<Events>`` root until the serialized
    document reaches ``target_bytes``, so the result is a well-formed document
    the parser reads as N events. At least one event is always emitted, so a
    target smaller than the fixed overhead still yields a parseable file.

    Args:
        target_bytes: Desired minimum size of the UTF-8 encoded document.

    Returns:
        The XML document as a string.
    """
    overhead = len(
        (XML_DECLARATION + EVENTS_OPEN + EVENTS_CLOSE).encode("utf-8")
    )
    block_bytes = len(EVENT_BLOCK.encode("utf-8"))
    remaining = max(target_bytes - overhead, 0)
    # Round up so the document meets or exceeds the target, with a floor of one
    # event so even a tiny target produces something parseable.
    count = max(1, -(-remaining // block_bytes))
    return XML_DECLARATION + EVENTS_OPEN + (EVENT_BLOCK * count) + EVENTS_CLOSE


def write_target_files(out_dir: Path) -> list[Path]:
    """Write every file of the benchmark size table into ``out_dir``.

    Args:
        out_dir: Directory to write the generated XML files into. Created if it
            does not exist.

    Returns:
        The paths written, in table order.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name_template, target_bytes, count in TARGET_FILES:
        document = build_xml(target_bytes)
        encoded = document.encode("utf-8")
        for i in range(count):
            path = out_dir / name_template.format(i=i)
            path.write_bytes(encoded)
            written.append(path)
    return written


def _load_parser():
    """Import ``parse_audit_log`` from shared/python for the verify path.

    Returns:
        The ``parse_audit_log`` callable.
    """
    shared_python = Path(__file__).resolve().parents[3] / "shared" / "python"
    if str(shared_python) not in sys.path:
        sys.path.insert(0, str(shared_python))
    from ontap_audit_parser import parse_audit_log

    return parse_audit_log


def verify() -> int:
    """Assert a generated 5 KB file parses to a non-zero event count.

    Writes a 5 KB document to a temporary directory and parses it with the
    canonical parser, proving the generator produces data the "process" test
    can actually measure. Makes no AWS call.

    Returns:
        0 on success.

    Raises:
        AssertionError: If the parser returns no events for a generated file.
    """
    parse_audit_log = _load_parser()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit-5kb-verify.xml"
        document = build_xml(5 * _BYTES_PER_KB)
        path.write_bytes(document.encode("utf-8"))
        events = parse_audit_log(path.read_bytes(), path.name)
        assert len(events) > 0, (
            "generated 5 KB file parsed to zero events; the generator is not "
            "producing parseable ONTAP XML"
        )
        print(
            f"verify: generated {path.name} "
            f"({len(document.encode('utf-8'))} bytes) parsed to "
            f"{len(events)} events"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and either write the table files or run --verify.

    Args:
        argv: Argument list for testing; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        help="Directory to write the generated XML files into.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Generate a 5 KB file and assert it parses to a non-zero count.",
    )
    args = parser.parse_args(argv)

    if args.verify:
        return verify()

    if args.out_dir is None:
        parser.error("provide --out-dir to write files, or --verify to self-check")

    written = write_target_files(args.out_dir)
    print(f"wrote {len(written)} files into {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
