"""Canonical FSx for ONTAP audit log parser for vendor Lambda handlers.

Why this module exists
----------------------
ONTAP writes file-access audit logs in one of two formats, selected at
`vserver audit create -format {evtx|xml}`. **JSON is not an ONTAP audit output
format.** A handler that only splits the payload on newlines and calls
`json.loads` therefore produces one useless record per line of XML — or, if it
skips unparseable lines, nothing at all.

This module gives every vendor integration one tested implementation instead of
eight divergent ones.

The XML trap
------------
ONTAP's XML output follows the Windows Event Log schema, so every element carries
``xmlns="http://schemas.microsoft.com/win/2004/08/events/event"``.
ElementTree reports those tags as ``{uri}Event``, so a plain ``iter("Event")``
matches nothing. Namespaces are stripped before any tag matching here.

Output schema
-------------
`parse_audit_log` returns a list of dicts using the lowercase normalized keys the
vendor formatters already read, with the original fields preserved under ``raw``:

    timestamp, event_type, source, svm, user, client_ip, operation, path, result, raw

Usage
-----
    from ontap_audit_parser import parse_audit_log
    events = parse_audit_log(raw_bytes, s3_key)

Handlers should import this defensively so a packaging miss degrades instead of
breaking the function::

    try:
        from ontap_audit_parser import parse_audit_log
    except ImportError:
        parse_audit_log = None
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

logger = logging.getLogger(__name__)

# EVTX files begin with this magic. ONTAP's default audit format is evtx.
EVTX_MAGIC = b"ElfFile\x00"

__all__ = [
    "parse_audit_log",
    "parse_xml",
    "parse_json_lines",
    "normalize_event",
    "detect_format",
]


def detect_format(data: bytes, key: str = "") -> str:
    """Identify the audit log format.

    The key extension is trusted first because it is cheap, then content is
    sniffed — ONTAP rotates files with names that do not always carry a
    meaningful suffix.

    Returns:
        One of "evtx", "xml", "json", or "unknown".
    """
    lowered = key.lower()
    if lowered.endswith(".evtx"):
        return "evtx"
    if lowered.endswith(".xml"):
        return "xml"
    if lowered.endswith(".json") or lowered.endswith(".json.gz"):
        return "json"

    if data.startswith(EVTX_MAGIC):
        return "evtx"

    head = data[:512].lstrip()
    if head.startswith(b"<?xml") or head.startswith(b"<"):
        return "xml"
    if head.startswith(b"{") or head.startswith(b"["):
        return "json"
    return "unknown"


def parse_audit_log(data: bytes, key: str = "") -> list[dict[str, Any]]:
    """Parse an FSx for ONTAP audit log file into normalized events.

    Args:
        data: Raw file bytes as read from the S3 Access Point.
        key: S3 object key, used for format detection and gzip handling.

    Returns:
        List of normalized event dicts. Never raises: a malformed or truncated
        rotation degrades to whatever could be extracted, because dropping the
        whole invocation would stall the checkpoint behind one bad file.
    """
    if not data:
        return []

    if key.lower().endswith(".gz"):
        try:
            data = gzip.decompress(data)
        except OSError as e:
            logger.warning("gzip decompress failed for %s: %s", key, e)

    fmt = detect_format(data, key)

    if fmt == "evtx":
        return parse_evtx(data)
    if fmt == "xml":
        return parse_xml(data.decode("utf-8", errors="replace"))
    if fmt == "json":
        return parse_json_lines(data.decode("utf-8", errors="replace"))

    # Unknown: try XML then JSON before giving up, so an unsuffixed rotation of
    # either real format is still parsed.
    text = data.decode("utf-8", errors="replace")
    events = parse_xml(text)
    if events:
        return events
    return parse_json_lines(text)


def parse_xml(text: str) -> list[dict[str, Any]]:
    """Parse ONTAP XML audit logs (Windows Event Log schema).

    Handles namespaced and bare documents, a wrapper root or none at all, and
    concatenated ``<Event>`` fragments with no declaration.
    """
    events: list[dict[str, Any]] = []
    if not text.strip():
        return events

    wrapped = _wrap_for_parsing(text)

    try:
        root = ET.fromstring(wrapped)
    except ET.ParseError as e:
        logger.warning("XML parse error (%s); falling back to fragment scan", e)
        return _parse_xml_fragments(text)

    _strip_namespaces(root)

    for event_elem in root.iter("Event"):
        events.append(normalize_event(_flatten_element(event_elem)))

    if events:
        return events

    # No <Event> elements. Descend through a single wrapper (e.g. <Events>) so N
    # records stay N records instead of being flattened into one.
    children = list(root)
    while len(children) == 1 and len(list(children[0])) > 0:
        children = list(children[0])
    for child in children:
        flat = _flatten_element(child)
        if flat:
            events.append(normalize_event(flat))
    return events


def parse_evtx(data: bytes) -> list[dict[str, Any]]:
    """Extract what is reliably available from an EVTX (binary) audit log.

    Full EVTX binary XML decoding needs a dedicated library that is not in the
    Lambda runtime. Rather than pretend, this reports the file as a single
    record carrying the chunk count so an operator can see the format is EVTX and
    switch the SVM to XML if they want full field extraction.

    To get parsed fields, configure the SVM with:
        vserver audit modify -vserver <svm> -format xml
    """
    if not data.startswith(EVTX_MAGIC):
        return []

    logger.warning(
        "EVTX audit log detected. Field extraction requires XML format — "
        "run: vserver audit modify -vserver <svm> -format xml"
    )
    return [
        normalize_event(
            {
                "event_type": "evtx_unparsed",
                "message": (
                    "EVTX binary audit log received. Configure the SVM with "
                    "-format xml for per-event field extraction."
                ),
                "file_size_bytes": len(data),
            }
        )
    ]


def parse_json_lines(text: str) -> list[dict[str, Any]]:
    """Parse newline-delimited JSON or a JSON array of audit records."""
    events: list[dict[str, Any]] = []
    text = text.strip()
    if not text:
        return events

    if text.startswith("["):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, list):
                return [normalize_event(e) for e in loaded if isinstance(e, dict)]
        except json.JSONDecodeError:
            pass  # Fall through to line-by-line

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            # Preserve the line rather than dropping it — an operator can still
            # see that something arrived in an unexpected shape.
            events.append(normalize_event({"message": line}))
            continue
        if isinstance(parsed, dict):
            events.append(normalize_event(parsed))
        else:
            events.append(normalize_event({"message": line}))
    return events


def normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Map ONTAP field names onto the normalized schema.

    Accepts EVTX/XML field names (`SubjectUserName`, `ObjectName`, `Keywords`…),
    ONTAP JSON names (`UserName`, `ClientIP`…) and already-normalized keys, so it
    is safe to call on any of them.
    """
    # ONTAP writes the literal string "Not Present" where a subject attribute is
    # unavailable, which happens for every operation that arrives over the S3
    # access path. Carrying it through makes an absent identity look like a user
    # named "Not Present", so it is treated as absent here.
    absent = {"", "Not Present", "-"}

    def pick(*names: str, default: str = "") -> Any:
        for n in names:
            if n in raw and raw[n] is not None and raw[n] not in absent:
                return raw[n]
        return default

    normalized = {
        "timestamp": pick("TimeCreated_SystemTime", "TimeCreated", "Timestamp", "timestamp"),
        "event_type": pick("EventID", "EventType", "event_type", default="audit"),
        "source": "fsxn-ontap",
        "svm": pick("Computer", "SVMName", "svm"),
        "user": pick("SubjectUserName", "UserName", "user"),
        "domain": pick("SubjectDomainName", "DomainName", "domain"),
        # SubjectIP is the name ONTAP actually emits in file-audit XML. The other
        # spellings are kept for JSON exports and already-normalized input.
        "client_ip": pick("SubjectIP", "IpAddress", "ClientIP", "client_ip"),
        # EventName carries the operation ("Create Object"); ObjectType only says
        # what kind of thing was touched ("File"), so it is the weaker fallback.
        "operation": pick("EventName", "ObjectType", "Operation", "operation"),
        # Which access path the operation arrived over. ONTAP uses CIFS and NFS
        # for the file protocols, and HTTP or S3 for the S3 access path — so this
        # is the field that tells an S3-access-point event apart from a file one.
        "access_protocol": pick("Source", "access_protocol"),
        "path": pick("ObjectName", "FileName", "HandleID", "path"),
        "result": pick("Keywords", "Result", "result"),
        "raw": raw,
    }
    # Carry a message through when the source had one — some vendors surface it
    # directly as the log body.
    if "message" in raw:
        normalized["message"] = raw["message"]
    return normalized


# ─── Internals ──────────────────────────────────────────────────────────────


def _wrap_for_parsing(text: str) -> str:
    """Wrap fragments in a synthetic root and drop the XML declaration.

    ONTAP may emit several top-level <Event> elements, which is not a
    well-formed document on its own.
    """
    stripped = text.strip()
    if stripped.startswith("<?xml"):
        # Remove the declaration; it is invalid anywhere but position zero.
        end = stripped.find("?>")
        if end != -1:
            stripped = stripped[end + 2:].lstrip()
    return f"<AuditEvents>{stripped}</AuditEvents>"


def _strip_namespaces(root: ET.Element) -> None:
    """Rewrite ``{uri}Tag`` to ``Tag`` in place across the whole tree."""
    for elem in root.iter():
        if isinstance(elem.tag, str) and "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]


def _flatten_element(elem: ET.Element) -> dict[str, Any]:
    """Flatten an element subtree into a dict of ONTAP field names.

    Handles ONTAP's ``<Data Name="key">value</Data>`` pattern and promotes
    attributes to ``Tag_Attr`` keys (e.g. ``TimeCreated_SystemTime``).
    """
    result: dict[str, Any] = {}
    for child in elem.iter():
        tag = child.tag.split("}")[-1] if "}" in str(child.tag) else child.tag
        if tag == "Data" and "Name" in child.attrib:
            if child.text and child.text.strip():
                result[child.attrib["Name"]] = child.text.strip()
        elif child.text and child.text.strip():
            result[tag] = child.text.strip()
        for attr_name, attr_value in child.attrib.items():
            if attr_name != "Name":
                result[f"{tag}_{attr_name}"] = attr_value
    return result


def _parse_xml_fragments(text: str) -> list[dict[str, Any]]:
    """Last resort: pull out individually well-formed <Event>...</Event> spans.

    Used when the document as a whole will not parse — most often a rotation that
    was truncated mid-write. Events already written are still recoverable.
    """
    events: list[dict[str, Any]] = []
    # Match the <Event> element specifically. A plain find("<Event") also matches
    # the <Events> wrapper, which would pair it with the first </Event> and
    # discard the genuine first event as a mismatched fragment.
    for match in re.finditer(r"<Event(?=[\s/>])", text):
        start = match.start()
        end = text.find("</Event>", start)
        if end == -1:
            break
        fragment = text[start:end + len("</Event>")]
        try:
            elem = ET.fromstring(fragment)
        except ET.ParseError:
            continue
        _strip_namespaces(elem)
        events.append(normalize_event(_flatten_element(elem)))
    return events
