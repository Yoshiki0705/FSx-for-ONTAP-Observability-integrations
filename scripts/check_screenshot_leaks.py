#!/usr/bin/env python3
"""OCR-scan tracked screenshots for identifiers that must not be published.

The failure this closes
-----------------------
Commit ffe730c added a Step Functions screenshot and said "Account ID masked".
The pixels still showed a real account ID, in the execution ARN and in the IAM
role ARN. Nothing in the repository could have noticed:

  - gitleaks and TruffleHog read text and decoded text, not pixels. A secret
    scanner has no way to see a number that exists only as rendered glyphs.
  - docs/screenshots/mask_screenshots.py paints regions of screenshots that are
    registered by file name. For any other file it processes nothing, prints
    success and exits 0, so a clean run says nothing about a new screenshot.
  - CI had no image check, and the manual OCR one-liner in
    docs/agent/non-obvious-patterns.md depends on someone remembering to run it.

This gate OCRs every tracked image on every run and fails on four things:
AWS account IDs, AWS resource IDs, e-mail addresses, and identifying PNG
metadata. Rule ids and placeholders match .gitleaks.toml
(aws-account-id-suspicious, aws-resource-id) so one vocabulary covers text and
pixels.

What it does NOT check
----------------------
  - IP addresses of any kind. Private addresses are allowed in screenshots, and
    public-address detection is deferred: dotted-number OCR false positives
    (versions, timestamps) have not been measured.
  - User names, request IDs and other UUIDs.
  - Text OCR cannot read. Recall depends on the OCR engine and a miss is
    possible; a clean run is evidence that the patterns were not found, not
    that the image is safe.
  - A bare 12-digit token without account context. One was measured as a
    false positive (a button label misread as digits, confidence 54.6).

Exit codes
----------
  0  every image scanned, nothing found
  1  at least one finding, or a stale allowlist entry (errors, if any, are
     printed too, so a retry-on-2 habit cannot hide a confirmed leak)
  2  tool or usage error: tesseract, a language pack or Pillow missing, an
     unreadable image, a path that matches no image, a broken allowlist, or a
     failed --selftest. Fail closed: nothing is reported as clean.

Output is redacted. A finding prints the path, the rule, the position and the
matched text with every digit and hex character replaced by "*", because CI
logs of a public repository are permanent and printing the value would
republish it.

Usage
-----
  python3 scripts/check_screenshot_leaks.py --selftest   # proves it can fail
  python3 scripts/check_screenshot_leaks.py              # every tracked image
  python3 scripts/check_screenshot_leaks.py PATH_OR_GLOB ...
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import glob
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import zlib
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALLOWLIST = ROOT / "scripts" / "screenshot-leak-allowlist.json"

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

INSTALL_HINT = (
    "Install the OCR engine with English and Japanese data: "
    "Debian/Ubuntu: sudo apt-get install -y tesseract-ocr tesseract-ocr-eng "
    "tesseract-ocr-jpn; macOS: brew install tesseract tesseract-lang. "
    "Pillow comes from requirements-dev.txt (make install)."
)

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".tif", ".tiff", ".bmp"})
OCR_LANGS = ("eng", "jpn")

# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

RULE_ACCOUNT = "aws-account-id-suspicious"
RULE_RESOURCE = "aws-resource-id"
RULE_EMAIL = "email-address"
RULE_METADATA = "image-metadata"
RULE_IDS = (RULE_ACCOUNT, RULE_RESOURCE, RULE_EMAIL, RULE_METADATA)

# .gitleaks.toml allowlists these two account IDs on the line level. Compared
# on digits only, so the dashed form 1234-5678-9012 is covered as well.
PLACEHOLDER_ACCOUNT_IDS = frozenset({"123456789012", "000000000000"})

# The suffix (after "<prefix>-") of every resource-ID placeholder that
# .gitleaks.toml's aws-resource-id rule allowlists. Each is a full-match regex.
# scripts/tests/test_screenshot_leak_rules.py parses the TOML and fails if the
# two lists differ in either direction. The line-level words "placeholder" and
# "example" that the account-ID rule exempts are deliberately NOT adopted: one
# such word anywhere on an OCR line would hide a real ARN on that line.
PLACEHOLDER_RESOURCE_SUFFIXES = (
    r"0123456789abcdef[0-9a-f]",
    r"0fedcba9876543210",
    r"0abcdef123456789a",
    r"aaaa1111aaaa1111a",
    r"bbbb2222bbbb2222b",
    r"cccc3333cccc3333c",
    r"dddd4444dddd4444d",
)
_PLACEHOLDER_RESOURCE_RES = tuple(re.compile(s) for s in PLACEHOLDER_RESOURCE_SUFFIXES)

# E-mail domains that identify no one: the project's synthetic identities and
# reserved documentation names (RFC 2606 / 6761). Matched on the whole domain
# or as a parent domain ("a.example.com" is covered by "example.com").
ALLOWED_EMAIL_DOMAINS = frozenset(
    {
        "malicious.net",
        "corp.local",
        "example.com",
        "example.org",
        "example.net",
        "users.noreply.github.com",
    }
)
ALLOWED_EMAIL_SUFFIXES = (".test", ".invalid", ".local")
# "logo@2x.png" is an image name, not an address.
FILE_EXTENSION_TLDS = frozenset(
    {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico", "tif", "tiff", "css", "js"}
)

_DIGITS12 = r"\d(?:[ ]?\d){11}"  # OCR sometimes inserts one space inside a number
_DASHED12 = r"\d{4}-\d{4}-\d{4}"
_ID_BODY = rf"(?:{_DIGITS12}|{_DASHED12})"

# <partition>:<service>:<region or empty>:<account> from an ARN. The match is
# anchored on the partition, not on the literal "arn": measured on rendered
# samples, tesseract reads "arn" as "am" or "arm" at some font sizes, and an
# ARN is the only place this four-field shape holds a 12-digit account. Tolerant
# to OCR spaces around the separators and inside the service and region fields
# ("ap-northeast- 1"), and to ' or ; read in place of a colon. (?!\d) rejects a
# 13th digit.
_SEP = r"\s*[:;'’]\s*"
_ARN_RE = re.compile(
    r"(?<![A-Za-z0-9])aws(?:-[a-z]+)*" + _SEP + r"[a-z0-9 -]+" + _SEP + r"[a-z0-9 -]*" + _SEP
    + rf"(?P<id>{_DIGITS12})(?!\d)",
    re.IGNORECASE,
)
_DASHED_RE = re.compile(rf"(?<![\d-])(?P<id>{_DASHED12})(?![\d-])")
_LABEL_EN_RE = re.compile(
    r"(?<![A-Za-z])account(?:\s*(?:id|number|no\.?))?\s*[:#]?\s*"
    rf"(?P<id>{_ID_BODY})(?![\d-])",
    re.IGNORECASE,
)
# OCR with the jpn model often spaces out katakana, hence the \s* between kana.
_LABEL_JA_RE = re.compile(
    r"ア\s*カ\s*ウ\s*ン\s*ト(?:\s*(?:ID|番号))?\s*[:：]?\s*" rf"(?P<id>{_ID_BODY})(?![\d-])",
    re.IGNORECASE,
)
# Resource IDs: the prefixes .gitleaks.toml lists plus vpce and eni. The body is
# 17 hex characters; O/o/I/l are accepted as the usual OCR confusions for 0/1
# and mapped back before the placeholder check.
_RESOURCE_RE = re.compile(
    r"(?<![A-Za-z0-9-])(?P<prefix>fsvol|fs|svm|vpce|vpc|subnet|sg|eni|i)-"
    r"(?P<id>[0-9a-foil]{17})(?![A-Za-z0-9-])",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])(?P<local>[A-Za-z0-9._%+-]+)@"
    r"(?P<domain>(?:[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,})(?![A-Za-z0-9-])"
)
_HOME_PATH_RE = re.compile(r"(?:/Users/|[A-Za-z]:\\Users\\)", re.IGNORECASE)
_DASH_VARIANTS = dict.fromkeys(map(ord, "‐‑‒–—―−﹣－"), "-")


@dataclasses.dataclass(frozen=True)
class Match:
    """A rule hit in a piece of text. Holds the redacted evidence, never the value."""

    rule: str
    start: int
    end: int
    evidence: str


def normalize_text(text: str) -> str:
    """Fold full-width forms (digits, colon) and dash variants to ASCII.

    NFKC turns "１２３" and "：" into "123" and ":"; the dash table covers the
    en dash, minus sign and friends that OCR emits inside dashed numbers.
    """
    return unicodedata.normalize("NFKC", text).translate(_DASH_VARIANTS)


def _redact(text: str, start: int, end: int) -> str:
    return text[:start] + "*" * (end - start) + text[end:]


def _account_match(text: str, m: re.Match[str]) -> Match | None:
    digits = re.sub(r"\D", "", m.group("id"))
    if digits in PLACEHOLDER_ACCOUNT_IDS:
        return None
    s, e = m.span()
    gs, ge = m.span("id")
    evidence = _redact(text[s:e], gs - s, ge - s)
    evidence = re.sub(r"\s+", " ", evidence)
    return Match(RULE_ACCOUNT, s, e, evidence)


def _is_placeholder_resource(hex_id: str) -> bool:
    mapped = hex_id.lower().translate(str.maketrans("oil", "011"))
    return any(p.fullmatch(mapped) for p in _PLACEHOLDER_RESOURCE_RES)


def _email_allowed(domain: str) -> bool:
    d = domain.lower()
    if d.rsplit(".", 1)[-1] in FILE_EXTENSION_TLDS:
        return True
    if d in ALLOWED_EMAIL_DOMAINS or d.endswith(ALLOWED_EMAIL_SUFFIXES):
        return True
    # "name@example.com-invoice.pdf" is a file name: the domain pattern runs on
    # through the hyphen, but the address itself ended at a reserved domain.
    return any(d.endswith("." + a) or d.startswith(a + "-") for a in ALLOWED_EMAIL_DOMAINS)


def match_normalized(text: str) -> list[Match]:
    """Find rule hits in text that is already normalized (offsets stay valid)."""
    found: dict[tuple[str, int, int], Match] = {}
    for pattern in (_ARN_RE, _DASHED_RE, _LABEL_EN_RE, _LABEL_JA_RE):
        for m in pattern.finditer(text):
            hit = _account_match(text, m)
            if hit is not None:
                found.setdefault((hit.rule, *m.span("id")), hit)
    for m in _RESOURCE_RE.finditer(text):
        if _is_placeholder_resource(m.group("id")):
            continue
        s, e = m.span()
        gs, ge = m.span("id")
        evidence = _redact(text[s:e], gs - s, ge - s)
        found.setdefault((RULE_RESOURCE, gs, ge), Match(RULE_RESOURCE, s, e, evidence))
    for m in _EMAIL_RE.finditer(text):
        if _email_allowed(m.group("domain")):
            continue
        s, e = m.span()
        evidence = f"{m.group('local')[0]}***@{m.group('domain')}"
        found.setdefault((RULE_EMAIL, s, e), Match(RULE_EMAIL, s, e, evidence))
    return sorted(found.values(), key=lambda h: (h.start, h.rule))


def find_matches(text: str) -> list[Match]:
    """Normalize text, then return every rule hit."""
    return match_normalized(normalize_text(text))


# --------------------------------------------------------------------------
# Errors and findings
# --------------------------------------------------------------------------


class ToolError(Exception):
    """The gate itself cannot run (tool, language pack, usage). Maps to exit 2."""


class ImageReadError(ToolError):
    """An image or its metadata cannot be read. Fail closed, never skip."""


class AllowlistError(ToolError):
    """The allowlist file is malformed."""


Box = tuple[int, int, int, int]  # x, y, width, height in original-image pixels


@dataclasses.dataclass
class Finding:
    path: str
    rule: str
    evidence: str
    passes: list[str]
    box: Box | None = None

    def location(self) -> str:
        if self.box is None:
            return "metadata"
        x, y, w, h = self.box
        return f"x={x} y={y} {w}x{h}"


# --------------------------------------------------------------------------
# PNG metadata (stdlib only, so it needs neither Pillow nor tesseract)
# --------------------------------------------------------------------------

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_TEXT_BYTES = 1024 * 1024


def read_png_chunks(data: bytes) -> list[tuple[str, bytes]]:
    """Return (type, body) for each chunk, validating signature, length and CRC."""
    if not data.startswith(PNG_SIGNATURE):
        raise ImageReadError("bad PNG signature")
    pos = len(PNG_SIGNATURE)
    chunks: list[tuple[str, bytes]] = []
    while pos < len(data):
        if pos + 8 > len(data):
            raise ImageReadError("truncated PNG chunk header")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctype = data[pos + 4 : pos + 8]
        body_end = pos + 8 + length
        if body_end + 4 > len(data):
            raise ImageReadError(f"truncated PNG chunk {ctype.decode('latin-1')!r}")
        (crc,) = struct.unpack(">I", data[body_end : body_end + 4])
        if zlib.crc32(data[pos + 4 : body_end]) & 0xFFFFFFFF != crc:
            raise ImageReadError(f"bad CRC in PNG chunk {ctype.decode('latin-1')!r}")
        chunks.append((ctype.decode("latin-1"), data[pos + 8 : body_end]))
        pos = body_end + 4
        if ctype == b"IEND":
            break
    if not chunks or chunks[-1][0] != "IEND":
        raise ImageReadError("PNG has no IEND chunk (truncated)")
    return chunks


def _inflate(data: bytes) -> bytes:
    """zlib-decompress with a hard cap so a crafted chunk cannot exhaust memory."""
    try:
        out = zlib.decompressobj().decompress(data, MAX_TEXT_BYTES + 1)
    except zlib.error as exc:
        raise ImageReadError(f"corrupt compressed text chunk: {exc}") from exc
    if len(out) > MAX_TEXT_BYTES:
        raise ImageReadError("compressed text chunk expands past 1 MiB")
    return out


def png_text_entries(chunks: Iterable[tuple[str, bytes]]) -> list[tuple[str, str, str]]:
    """Decode tEXt / zTXt / iTXt chunks into (chunk type, key, value)."""
    entries: list[tuple[str, str, str]] = []
    for ctype, body in chunks:
        try:
            if ctype == "tEXt":
                key, _, value = body.partition(b"\x00")
                entries.append((ctype, key.decode("latin-1"), value.decode("latin-1")))
            elif ctype == "zTXt":
                key, _, rest = body.partition(b"\x00")
                entries.append((ctype, key.decode("latin-1"), _inflate(rest[1:]).decode("latin-1")))
            elif ctype == "iTXt":
                key, _, rest = body.partition(b"\x00")
                flag = rest[0:1]
                value_part = rest[2:]
                _lang, _, value_part = value_part.partition(b"\x00")
                _translated, _, value_part = value_part.partition(b"\x00")
                if flag == b"\x01":
                    value_part = _inflate(value_part)
                entries.append(
                    (ctype, key.decode("latin-1"), value_part.decode("utf-8", errors="replace"))
                )
        except (IndexError, UnicodeDecodeError) as exc:
            raise ImageReadError(f"malformed {ctype} chunk: {exc}") from exc
    return entries


def _safe_key(key: str) -> str:
    """A text-chunk key is attacker- or user-controlled; print it only if it is tame."""
    return key if re.fullmatch(r"[A-Za-z0-9 _.-]{1,32}", key) else "<key>"


def _value_reasons(value: str) -> list[str]:
    reasons: list[str] = []
    if _HOME_PATH_RE.search(value):
        reasons.append("home-directory path")
    for hit in find_matches(value):
        if hit.rule not in reasons:
            reasons.append(hit.rule)
    return reasons


def png_metadata_findings(data: bytes) -> list[str]:
    """Describe identifying metadata in PNG bytes. Evidence never includes values.

    A text chunk is a finding only when its VALUE matches a rule or contains a
    home-directory path: the key "Software" alone (12 tracked files carry
    Software=mask_screenshots.py) is not. Any eXIf chunk is a finding.
    """
    chunks = read_png_chunks(data)
    details: list[str] = []
    for ctype, _body in chunks:
        if ctype == "eXIf":
            details.append("eXIf chunk present (EXIF can carry device, time and location)")
    for ctype, key, value in png_text_entries(chunks):
        reasons = _value_reasons(value)
        if reasons:
            details.append(f"{ctype} chunk '{_safe_key(key)}' value matches: {', '.join(reasons)}")
    return details


def _import_pillow() -> Any:
    try:
        import PIL
        from PIL import Image, ImageDraw, ImageFont, ImageOps  # noqa: F401
    except ImportError as exc:
        raise ToolError(f"Pillow is not installed ({exc}). {INSTALL_HINT}") from exc
    return PIL


def other_metadata_findings(path: Path) -> list[str]:
    """Metadata of non-PNG formats via Pillow: any EXIF, and string values in info."""
    _import_pillow()
    from PIL import Image

    details: list[str] = []
    try:
        with Image.open(path) as im:
            if len(im.getexif()) > 0:
                details.append("EXIF block present (can carry device, time and location)")
            candidates: list[tuple[str, str]] = []
            for key, value in im.info.items():
                if key in {"icc_profile", "exif", "dpi", "jfif", "jfif_version", "jfif_unit"}:
                    continue
                if isinstance(value, bytes) and len(value) <= MAX_TEXT_BYTES:
                    candidates.append((str(key), value.decode("utf-8", errors="replace")))
                elif isinstance(value, str):
                    candidates.append((str(key), value))
            tag_v2 = getattr(im, "tag_v2", None)
            if tag_v2 is not None:
                candidates.extend((f"tiff-tag-{k}", v) for k, v in tag_v2.items() if isinstance(v, str))
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageReadError(f"cannot read image: {exc}") from exc
    for key, value in candidates:
        reasons = _value_reasons(value)
        if reasons:
            details.append(f"metadata '{_safe_key(key)}' value matches: {', '.join(reasons)}")
    return details


def metadata_details(path: Path) -> list[str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ImageReadError(f"cannot read file: {exc}") from exc
    if data.startswith(PNG_SIGNATURE):
        return png_metadata_findings(data)
    return other_metadata_findings(path)


# --------------------------------------------------------------------------
# Allowlist
# --------------------------------------------------------------------------

ALLOWLIST_KEYS = frozenset({"path", "rule", "reason"})
MIN_REASON_LENGTH = 20


@dataclasses.dataclass(frozen=True)
class AllowEntry:
    path: str
    rule: str
    reason: str


def parse_allowlist(raw: Any) -> list[AllowEntry]:
    """Validate the allowlist document. Every problem raises AllowlistError."""
    if not isinstance(raw, dict) or set(raw) != {"entries"} or not isinstance(raw["entries"], list):
        raise AllowlistError('allowlist must be {"entries": [...]} and nothing else')
    entries: list[AllowEntry] = []
    seen: set[tuple[str, str]] = set()
    for i, item in enumerate(raw["entries"]):
        where = f"allowlist entry {i}"
        if not isinstance(item, dict) or set(item) != ALLOWLIST_KEYS:
            raise AllowlistError(f"{where}: keys must be exactly {sorted(ALLOWLIST_KEYS)}")
        path, rule, reason = item["path"], item["rule"], item["reason"]
        if not all(isinstance(v, str) for v in (path, rule, reason)):
            raise AllowlistError(f"{where}: path, rule and reason must be strings")
        if not path or any(c in path for c in "*?[]") or path.startswith(("/", "./")):
            raise AllowlistError(f"{where}: path must be one exact repo-relative path (no wildcards)")
        if rule not in RULE_IDS:
            raise AllowlistError(f"{where}: rule must be one of {list(RULE_IDS)}")
        if len(reason.strip()) < MIN_REASON_LENGTH:
            raise AllowlistError(
                f"{where}: reason is mandatory and must say why this is safe "
                f"(at least {MIN_REASON_LENGTH} characters)"
            )
        if (path, rule) in seen:
            raise AllowlistError(f"{where}: duplicate (path, rule)")
        seen.add((path, rule))
        entries.append(AllowEntry(path, rule, reason.strip()))
    return entries


def load_allowlist(path: Path) -> list[AllowEntry]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AllowlistError(f"cannot read allowlist {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AllowlistError(f"allowlist {path} is not valid JSON: {exc}") from exc
    return parse_allowlist(raw)


def apply_allowlist(
    findings: Sequence[Finding], entries: Sequence[AllowEntry]
) -> tuple[list[Finding], list[AllowEntry]]:
    """Drop findings that match an entry's exact (path, rule); report unused entries."""
    keys = {(e.path, e.rule): e for e in entries}
    used: set[tuple[str, str]] = set()
    kept: list[Finding] = []
    for f in findings:
        key = (f.path, f.rule)
        if key in keys:
            used.add(key)
        else:
            kept.append(f)
    stale = [e for e in entries if (e.path, e.rule) not in used]
    return kept, stale


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def display_path(path: Path) -> str:
    """Repo-relative POSIX path when under the repo root, else the path as given."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def tracked_images(root: Path = ROOT) -> list[Path]:
    """Image files that git tracks. Fails closed if git cannot answer."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"cannot run git ls-files: {exc}") from exc
    if proc.returncode != 0:
        raise ToolError(f"git ls-files failed: {proc.stderr.decode(errors='replace').strip()}")
    names = [n for n in proc.stdout.decode("utf-8", errors="surrogateescape").split("\0") if n]
    return sorted(root / n for n in names if Path(n).suffix.lower() in IMAGE_EXTENSIONS)


def _walk_images(directory: Path) -> list[Path]:
    return sorted(
        p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def expand_inputs(args: Sequence[str]) -> list[Path]:
    """Resolve path/glob arguments. An argument that matches nothing is an error."""
    found: list[Path] = []
    for arg in args:
        if any(c in arg for c in "*?["):
            matches = [Path(p) for p in glob.glob(arg, recursive=True)]
        elif Path(arg).is_dir():
            matches = [Path(arg)]
        elif Path(arg).is_file():
            found.append(Path(arg))  # an explicit file is scanned whatever its extension
            continue
        else:
            raise ToolError(f"no such file or directory: {arg}")
        hits: list[Path] = []
        for p in sorted(matches):
            if p.is_dir():
                hits.extend(_walk_images(p))
            elif p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS:
                hits.append(p)
        if not hits:
            raise ToolError(f"no images matched: {arg}")
        found.extend(hits)
    return list(dict.fromkeys(found))


# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class PassSpec:
    """One OCR pass: how the image is prepared and how tesseract segments it."""

    name: str
    scale: int = 1
    prepare_gray: bool = False
    invert: bool = False
    psm: int = 3


PASSES: tuple[PassSpec, ...] = (
    PassSpec("original", psm=3),
    PassSpec("gray2x", scale=2, prepare_gray=True, psm=3),
    PassSpec("inverted2x", scale=2, prepare_gray=True, invert=True, psm=3),
)
BORDER = 10
DEFAULT_TIMEOUT = 120.0

_tesseract_path: str | None = None


def check_environment() -> str:
    """Verify tesseract, its eng and jpn data, and Pillow. Raises ToolError."""
    global _tesseract_path
    exe = shutil.which("tesseract")
    if exe is None:
        raise ToolError(f"tesseract not found on PATH. {INSTALL_HINT}")
    try:
        proc = subprocess.run(
            [exe, "--list-langs"], capture_output=True, text=True, check=False, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ToolError(f"tesseract --list-langs failed: {exc}. {INSTALL_HINT}") from exc
    listed = {ln.strip() for ln in (proc.stdout + "\n" + proc.stderr).splitlines()}
    missing = [lang for lang in OCR_LANGS if lang not in listed]
    if proc.returncode != 0 or missing:
        raise ToolError(f"tesseract language data missing: {', '.join(missing) or '?'}. {INSTALL_HINT}")
    _import_pillow()
    _tesseract_path = exe
    return exe


@dataclasses.dataclass(frozen=True)
class OcrWord:
    block: int
    par: int
    line: int
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int


def parse_tsv(tsv: str) -> list[OcrWord]:
    """Parse tesseract TSV output into level-5 (word) rows."""
    words: list[OcrWord] = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t", 11)
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        try:
            words.append(
                OcrWord(
                    int(cols[2]),
                    int(cols[3]),
                    int(cols[4]),
                    cols[11].strip(),
                    float(cols[10]),
                    int(cols[6]),
                    int(cols[7]),
                    int(cols[8]),
                    int(cols[9]),
                )
            )
        except ValueError as exc:
            raise ToolError(f"unparseable tesseract TSV row: {exc}") from exc
    return words


def page_text(words: Sequence[OcrWord]) -> tuple[str, list[tuple[int, int, OcrWord]]]:
    """Join words into normalized text (lines joined by newline) with word offsets."""
    parts: list[str] = []
    spans: list[tuple[int, int, OcrWord]] = []
    pos = 0
    prev_key: tuple[int, int, int] | None = None
    for w in words:
        key = (w.block, w.par, w.line)
        if prev_key is not None:
            sep = " " if key == prev_key else "\n"
            parts.append(sep)
            pos += 1
        norm = normalize_text(w.text)
        parts.append(norm)
        spans.append((pos, pos + len(norm), w))
        pos += len(norm)
        prev_key = key
    return "".join(parts), spans


def _union_box(words: Iterable[OcrWord]) -> Box:
    ws = list(words)
    x0 = min(w.left for w in ws)
    y0 = min(w.top for w in ws)
    x1 = max(w.left + w.width for w in ws)
    y1 = max(w.top + w.height for w in ws)
    return (x0, y0, x1 - x0, y1 - y0)


def _to_original(box: Box, spec: PassSpec, had_border: bool) -> Box:
    x, y, w, h = box
    offset = BORDER if had_border else 0
    s = spec.scale
    return (
        max(0, (x - offset) // s),
        max(0, (y - offset) // s),
        max(1, -(-w // s)),
        max(1, -(-h // s)),
    )


def words_to_findings(
    path: str, words: Sequence[OcrWord], spec: PassSpec, had_border: bool
) -> list[Finding]:
    text, spans = page_text(words)
    out: list[Finding] = []
    for hit in match_normalized(text):
        covered = [w for s, e, w in spans if s < hit.end and e > hit.start]
        box = _to_original(_union_box(covered), spec, had_border) if covered else None
        out.append(Finding(path, hit.rule, hit.evidence, [spec.name], box))
    return out


def _boxes_overlap(a: Box, b: Box, pad: int = 6) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw + pad < bx or bx + bw + pad < ax or ay + ah + pad < by or by + bh + pad < ay
    )


def merge_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Union findings across passes. Same rule at an overlapping position is one finding."""
    merged: list[Finding] = []
    for f in findings:
        for existing in merged:
            same = existing.rule == f.rule and (
                (existing.box is None and f.box is None and existing.evidence == f.evidence)
                or (
                    existing.box is not None
                    and f.box is not None
                    and _boxes_overlap(existing.box, f.box)
                )
            )
            if same:
                for name in f.passes:
                    if name not in existing.passes:
                        existing.passes.append(name)
                break
        else:
            merged.append(dataclasses.replace(f, passes=list(f.passes)))
    return merged


def _flatten_to_rgb(im: Any) -> Any:
    """Composite any alpha onto white and return an RGB image of the first frame."""
    from PIL import Image

    rgba = im.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def prepare_png(rgb: Any, spec: PassSpec) -> tuple[bytes, bool]:
    """Render the pass input as PNG bytes. Returns (bytes, had_border)."""
    from PIL import Image, ImageOps

    img = rgb
    had_border = False
    if spec.prepare_gray:
        img = ImageOps.autocontrast(img.convert("L"))
        fill = img.getpixel((0, 0))
        img = ImageOps.expand(img, border=BORDER, fill=fill)
        had_border = True
    if spec.scale != 1:
        img = img.resize((img.width * spec.scale, img.height * spec.scale), Image.Resampling.LANCZOS)
    if spec.invert:
        img = ImageOps.invert(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=1)
    return buf.getvalue(), had_border


def run_tesseract(png_bytes: bytes, psm: int, timeout: float) -> str:
    """OCR PNG bytes passed on stdin (no temp file holds screenshot content)."""
    exe = _tesseract_path or check_environment()
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    cmd = [exe, "stdin", "stdout", "-l", "+".join(OCR_LANGS), "--psm", str(psm), "tsv"]
    try:
        proc = subprocess.run(
            cmd, input=png_bytes, capture_output=True, check=False, timeout=timeout, env=env
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"tesseract timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise ToolError(f"cannot run tesseract: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()[:200]
        raise ToolError(f"tesseract exited {proc.returncode}: {detail}")
    return proc.stdout.decode("utf-8", errors="replace")


def ocr_findings(
    path: Path, passes: Sequence[PassSpec], timeout: float, label: str | None = None
) -> list[Finding]:
    """Run every pass on one image and union the findings."""
    _import_pillow()
    from PIL import Image

    name = label or display_path(path)
    try:
        with Image.open(path) as im:
            im.load()
            rgb = _flatten_to_rgb(im)
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageReadError(f"cannot read image: {exc}") from exc
    collected: list[Finding] = []
    for spec in passes:
        data, had_border = prepare_png(rgb, spec)
        words = parse_tsv(run_tesseract(data, spec.psm, timeout))
        collected.extend(words_to_findings(name, words, spec, had_border))
    return merge_findings(collected)


# --------------------------------------------------------------------------
# Scan
# --------------------------------------------------------------------------


@dataclasses.dataclass
class ScanResult:
    findings: list[Finding]
    errors: list[str]
    scanned: int


def scan_image(path: Path, passes: Sequence[PassSpec], timeout: float) -> list[Finding]:
    name = display_path(path)
    findings = [
        Finding(name, RULE_METADATA, detail, ["metadata"], None)
        for detail in metadata_details(path)
    ]
    findings.extend(ocr_findings(path, passes, timeout, name))
    return findings


def run_scan(
    paths: Sequence[Path],
    passes: Sequence[PassSpec] = PASSES,
    jobs: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ScanResult:
    """Scan images in parallel. A per-image error is recorded, not raised."""
    workers = max(1, min(jobs or os.cpu_count() or 2, len(paths) or 1))
    findings: list[Finding] = []
    errors: list[str] = []

    def one(p: Path) -> tuple[Path, list[Finding], str | None]:
        try:
            return p, scan_image(p, passes, timeout), None
        except ToolError as exc:
            return p, [], f"{display_path(p)}: {exc}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for _p, got, err in pool.map(one, paths):
            findings.extend(got)
            if err:
                errors.append(err)
    findings.sort(key=lambda f: (f.path, f.rule, f.box or (0, 0, 0, 0)))
    return ScanResult(findings, errors, len(paths))


def report(result: ScanResult, stale: Sequence[AllowEntry], annotate: bool) -> None:
    for f in result.findings:
        print(f"{f.path}: {f.rule} at {f.location()} [{', '.join(f.passes)}]: {f.evidence}")
        if annotate:
            print(f"::error file={f.path},title=Screenshot leak::{f.rule}: {f.evidence}")
    for e in stale:
        print(
            f"stale allowlist entry (suppressed nothing): path={e.path} rule={e.rule}. "
            "Remove it from scripts/screenshot-leak-allowlist.json."
        )
    for err in result.errors:
        print(f"ERROR {err}", file=sys.stderr)


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------

JA_CONSOLE_SAMPLE = ROOT / "docs" / "screenshots" / "ssd-auto-increase" / "15-sns-notify-subscriptions.png"

# Every synthetic value is assembled at run time. A source line holding a whole
# account ID, resource ID or private IP would trip .gitleaks.toml on this very
# file, and the file must stay scannable by the gate it implements.
SYN_ACCOUNT = "".join(("2109", "8765", "4321"))
SYN_ACCOUNT_DASHED = "-".join(("2109", "8765", "4321"))
SYN_HEX = "".join(("1a2b3c", "4d5e6f", "7a8b9"))
SYN_EMAIL = "jane.roe" + "@" + "contoso.com"
PLACEHOLDER_ARN_ACCOUNT = "123456789012"


def _dotted(*parts: str) -> str:
    return ".".join(parts)


@dataclasses.dataclass(frozen=True)
class Sample:
    name: str
    expect: frozenset[str]
    build: Callable[[Path], None]


def render_lines(
    path: Path,
    lines: Sequence[str],
    size: int = 20,
    fg: tuple[int, int, int] = (35, 47, 62),
    bg: tuple[int, int, int] = (255, 255, 255),
) -> None:
    """Render text to a PNG with Pillow's bundled font at the given pixel size."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=size)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    pad = max(12, size)
    line_h = int(size * 1.7)
    width = int(max(probe.textlength(ln, font=font) for ln in lines)) + 2 * pad
    img = Image.new("RGB", (width, line_h * len(lines) + 2 * pad), bg)
    draw = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        draw.text((pad, pad + i * line_h), ln, font=font, fill=fg)
    img.save(path)


def _text_sample(
    name: str, lines: Sequence[str], expect: Iterable[str], **style: Any
) -> Sample:
    return Sample(name, frozenset(expect), lambda p: render_lines(p, lines, **style))


def _png_with_text(path: Path, key: str, value: str) -> None:
    from PIL import Image, PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text(key, value)
    Image.new("RGB", (64, 32), "white").save(path, pnginfo=info)


def _png_with_exif(path: Path) -> None:
    from PIL import Image

    exif = Image.Exif()
    exif[0x010E] = "synthetic description"  # ImageDescription
    Image.new("RGB", (64, 32), "white").save(path, exif=exif)


def _ja_console_with_planted_arn(path: Path) -> None:
    """Extend the tracked JA console screenshot with a strip holding a planted ARN."""
    from PIL import Image, ImageDraw, ImageFont

    with Image.open(JA_CONSOLE_SAMPLE) as src:
        base = _flatten_to_rgb(src)
    strip_h = 90
    canvas = Image.new("RGB", (base.width, base.height + strip_h), (255, 255, 255))
    canvas.paste(base, (0, 0))
    font = ImageFont.load_default(size=22)
    text = f"arn:aws:iam::{SYN_ACCOUNT}:role/demo-role"
    ImageDraw.Draw(canvas).text((40, base.height + 28), text, font=font, fill=(35, 47, 62))
    canvas.save(path)


def _ja_console_unchanged(path: Path) -> None:
    shutil.copyfile(JA_CONSOLE_SAMPLE, path)


def selftest_samples() -> list[Sample]:
    """Cases that must FAIL (expect a rule) and cases that must PASS (expect none)."""
    arn = f"arn:aws:states:ap-northeast-1:{SYN_ACCOUNT}:execution:demo-workflow:run-1"
    iam = f"arn:aws:iam::{SYN_ACCOUNT}:role/service-role/demo-role"
    dark = {"fg": (225, 229, 235), "bg": (22, 25, 31)}
    fails: list[Sample] = [
        _text_sample("account-id-in-arn", [f"Execution ARN: {arn}"], [RULE_ACCOUNT]),
        _text_sample("account-id-in-arn-dark-theme", [f"Execution ARN: {arn}"], [RULE_ACCOUNT], **dark),
        _text_sample("account-id-in-arn-small-text", [f"Execution ARN: {arn}"], [RULE_ACCOUNT], size=12),
        _text_sample("account-id-in-iam-arn-empty-region", [f"IAM role: {iam}"], [RULE_ACCOUNT]),
        Sample("account-id-in-arn-on-ja-console", frozenset({RULE_ACCOUNT}), _ja_console_with_planted_arn),
        _text_sample("account-id-english-label", [f"Account ID: {SYN_ACCOUNT}"], [RULE_ACCOUNT]),
        _text_sample("account-id-dashed", [f"Signed in as {SYN_ACCOUNT_DASHED} (admin)"], [RULE_ACCOUNT]),
    ]
    for prefix in ("fs", "fsvol", "svm", "vpc", "vpce", "subnet", "sg", "eni", "i"):
        fails.append(
            _text_sample(f"resource-id-{prefix}", [f"Resource: {prefix}-{SYN_HEX}"], [RULE_RESOURCE])
        )
    fails.append(_text_sample("email-address", [f"Contact: {SYN_EMAIL}"], [RULE_EMAIL]))
    # Same shape as the reserved-domain case below, but a real-looking domain:
    # the file-name-tail exemption must not cover it.
    fails.append(
        _text_sample(
            "email-real-looking-domain-with-file-name-tail",
            ["/share/finance/customers/jane.roe" + "@" + "contoso.com-invoice.pdf"],
            [RULE_EMAIL],
        )
    )
    fails.append(
        Sample(
            "metadata-unix-home-path",
            frozenset({RULE_METADATA}),
            lambda p: _png_with_text(p, "Comment", "/Users/" + "someone" + "/Desktop/shot.png"),
        )
    )
    fails.append(
        Sample(
            "metadata-windows-home-path",
            frozenset({RULE_METADATA}),
            lambda p: _png_with_text(p, "Comment", "C:" + "\\Users\\" + "someone" + "\\shot.png"),
        )
    )
    fails.append(Sample("metadata-exif-chunk", frozenset({RULE_METADATA}), _png_with_exif))
    placeholders = [
        f"arn:aws:iam::{PLACEHOLDER_ARN_ACCOUNT}:role/demo-role",
        "arn:aws:iam::000000000000:role/demo-role",
        "Account 1234-5678-9012",
        "fs-0123456789abcdef0  svm-0123456789abcdef1  vpc-0123456789abcdef2",
        "subnet-aaaa1111aaaa1111a  sg-0fedcba9876543210  i-0abcdef123456789a",
    ]
    passes: list[Sample] = [
        _text_sample("placeholders", placeholders, []),
        _text_sample("placeholders-dark-theme", placeholders, [], **dark),
        _text_sample(
            "synthetic-attacker-addresses",
            ["attacker" + "@" + "malicious.net", "suspicious_user" + "@" + "corp.local"],
            [],
        ),
        _text_sample(
            "email-reserved-domain-with-file-name-tail",
            ["/share/finance/customers/taro" + "@" + "example.com-invoice.pdf"],
            [],
        ),
        _text_sample(
            "twelve-digit-number-without-account-context",
            [f"Request ID: {SYN_ACCOUNT}", f"[ Delete ]  {SYN_ACCOUNT}"],
            [],
        ),
        _text_sample(
            "private-ip-addresses-are-allowed",
            [
                "Management IP " + _dotted("10", "0", "3", "17"),
                "Peer " + _dotted("172", "16", "8", "4") + "  " + _dotted("192", "168", "1", "20"),
            ],
            [],
        ),
        Sample(
            "metadata-software-key-only",
            frozenset(),
            lambda p: _png_with_text(p, "Software", "mask_screenshots.py"),
        ),
        Sample("tracked-ja-console-unchanged", frozenset(), _ja_console_unchanged),
    ]
    return fails + passes


def selftest(jobs: int | None = None) -> int:
    """Render known-bad and known-good samples and require the right verdict for each."""
    try:
        check_environment()
        if not JA_CONSOLE_SAMPLE.is_file():
            raise ToolError(
                f"selftest needs the tracked JA console screenshot {display_path(JA_CONSOLE_SAMPLE)}"
            )
        samples = selftest_samples()
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="screenshot-selftest-") as tmp:
            paths: dict[str, Path] = {}
            for s in samples:
                p = Path(tmp) / f"{s.name}.png"
                s.build(p)
                paths[s.name] = p
            result = run_scan(list(paths.values()), PASSES, jobs, DEFAULT_TIMEOUT)
            if result.errors:
                raise ToolError("selftest scan errors: " + "; ".join(result.errors))
            by_name: dict[str, set[str]] = {}
            for f in result.findings:
                by_name.setdefault(Path(f.path).stem, set()).add(f.rule)
            problems: list[str] = []
            for s in samples:
                got = by_name.get(s.name, set())
                if not s.expect:
                    if got:
                        problems.append(f"{s.name}: false positive {sorted(got)}")
                elif not s.expect <= got:
                    problems.append(f"{s.name}: missed {sorted(s.expect - got)}")
        if problems:
            raise ToolError("selftest FAILED:\n  " + "\n  ".join(problems))
    except ToolError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    must_fail = sum(1 for s in samples if s.expect)
    print(
        f"selftest ok: {must_fail} planted cases detected, {len(samples) - must_fail} clean cases "
        f"passed, {time.monotonic() - started:.1f}s"
    )
    return EXIT_OK


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="OCR-scan tracked screenshots for account IDs, resource IDs, e-mail and metadata.",
        epilog="Exit codes: 0 clean, 1 findings, 2 tool or usage error.",
    )
    p.add_argument("paths", nargs="*", help="files, directories or globs (default: every tracked image)")
    p.add_argument("--selftest", action="store_true", help="prove the gate can fail, then exit")
    p.add_argument("--jobs", type=int, default=None, help="parallel OCR workers (default: CPU count)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds per tesseract call")
    p.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST, help="allowlist JSON")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest(args.jobs)
    started = time.monotonic()
    try:
        entries = load_allowlist(args.allowlist)
        full_scan = not args.paths
        images = tracked_images() if full_scan else expand_inputs(args.paths)
        if not images:
            raise ToolError("git tracks no images; refusing to report a clean result for zero images")
        check_environment()
    except ToolError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return EXIT_ERROR
    result = run_scan(images, PASSES, args.jobs, args.timeout)
    kept, stale = apply_allowlist(result.findings, entries)
    if not full_scan:
        stale = []  # a partial scan cannot tell whether an entry is still needed
    result.findings = kept
    report(result, stale, os.environ.get("GITHUB_ACTIONS") == "true")
    elapsed = time.monotonic() - started
    print(f"scanned {result.scanned} image(s) in {elapsed:.1f}s")
    if kept or stale:
        affected = len({f.path for f in kept})
        print(
            f"FAILED: {len(kept)} finding(s) in {affected} image(s), {len(stale)} stale allowlist "
            "entr(ies). Mask the pixels (not just the file name) and re-run; see "
            "docs/agent/non-obvious-patterns.md."
        )
        return EXIT_FINDINGS
    if result.errors:
        return EXIT_ERROR
    print("OK: no findings")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
