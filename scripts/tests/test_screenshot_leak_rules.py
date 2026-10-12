"""Rules and metadata parser of the screenshot leak gate, without OCR.

Why this exists
---------------
scripts/check_screenshot_leaks.py has two halves. The OCR half needs tesseract
and is proven by `--selftest` (run by `make screenshots`, where tesseract is
installed). This file covers the half that needs neither tesseract nor
anything but the standard library and Pillow: which text is a leak, which
placeholders are not, and what the PNG metadata reader flags.

Nothing here is skipped when a tool is absent. A detection suite that skips
without its tool reports green while detecting nothing; `ci.yaml` already
carries that lesson for gitleaks.

Every synthetic account ID, resource ID and e-mail address is assembled at run
time. A source line holding a whole one would trip .gitleaks.toml on this file.
"""

from __future__ import annotations

import importlib.util
import re
import struct
import sys
import tomllib
import zlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check_screenshot_leaks.py"
GITLEAKS = REPO_ROOT / ".gitleaks.toml"


def _load():
    spec = importlib.util.spec_from_file_location("check_screenshot_leaks", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def chk():
    assert CHECKER.is_file(), f"{CHECKER} is missing"
    return _load()


ACCT = "".join(("2109", "8765", "4321"))
ACCT_DASHED = "-".join(("2109", "8765", "4321"))
HEX = "".join(("1a2b3c", "4d5e6f", "7a8b9"))
LOCAL = "jane.roe"
EMAIL = LOCAL + "@" + "contoso.com"


def rules(chk, text: str) -> list[str]:
    return [m.rule for m in chk.find_matches(text)]


def fullwidth(text: str) -> str:
    """ASCII to full-width forms, as a CJK UI or OCR output can produce."""
    return "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in text)


# --------------------------------------------------------------------------
# Account IDs
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"arn:aws:states:ap-northeast-1:{ACCT}:execution:wf:run-1",
        f"arn:aws:iam::{ACCT}:role/service-role/demo",
        f"arn : aws : iam :: {ACCT} : role/demo",
        f"arn:aws:states:ap-northeast- 1:{ACCT[:1]} {ACCT[1:11]} {ACCT[11:]}:execution",
        f"arn:aws-cn:iam::{ACCT}:root",
        f"am:aws'states:ap-northeast-1:{ACCT}:execution",
        f"IAM role ARN\narn:aws:iam::\n{ACCT}:role/x",
        fullwidth(f"arn:aws:iam::{ACCT}:role/x"),
    ],
    ids=[
        "plain",
        "empty-region",
        "spaced-separators",
        "spaced-region-and-digits",
        "china-partition",
        "ocr-read-arn-as-am-and-colon-as-quote",
        "wrapped-after-separator",
        "full-width",
    ],
)
def test_account_id_in_an_arn_is_found(chk, text: str) -> None:
    assert rules(chk, text) == [chk.RULE_ACCOUNT]


@pytest.mark.parametrize(
    "text",
    [
        "arn:aws:iam::123456789012:role/demo",
        "arn:aws:iam::000000000000:role/demo",
        "arn:aws:s3:::bucket-without-an-account",
        "arn:aws:iam::12345678901:role/demo",  # 11 digits
        f"arn:aws:iam::{ACCT}3:role/demo",  # 13 digits
        f"{ACCT}",  # a bare 12-digit token has no account context
        f"Request ID: {ACCT}",
        f"[ Delete ]  {ACCT}",
        "arn:aws:kms:ap-northeast-1:key/00000000-0000-0000-0000-000000000000",
    ],
    ids=[
        "placeholder-123456789012",
        "placeholder-000000000000",
        "no-account-field",
        "eleven-digits",
        "thirteen-digits",
        "bare-token",
        "request-id-label",
        "button-label",
        "all-zero-kms-key-uuid",
    ],
)
def test_account_id_negatives(chk, text: str) -> None:
    assert rules(chk, text) == []


def test_a_placeholder_word_on_the_line_does_not_hide_a_real_arn(chk) -> None:
    """.gitleaks.toml exempts any line containing 'placeholder' or 'example'.
    On OCR text that would let one such word hide a real ARN, so it is not adopted."""
    text = f"example placeholder: arn:aws:iam::{ACCT}:role/demo"
    assert rules(chk, text) == [chk.RULE_ACCOUNT]


@pytest.mark.parametrize(
    "text",
    [ACCT_DASHED, f"Signed in as {ACCT_DASHED} (admin)", fullwidth(ACCT_DASHED), ACCT_DASHED.replace("-", "–")],
    ids=["bare-dashed", "in-sentence", "full-width", "en-dash"],
)
def test_dashed_account_id_is_found(chk, text: str) -> None:
    assert rules(chk, text) == [chk.RULE_ACCOUNT]


@pytest.mark.parametrize(
    "text",
    [
        "1234-5678-9012",
        "0000-0000-0000",
        "2026-10-12",
        "Call 03-1234-5678",
        "00000000-1234-5678-9012-000000000000",
        "20261012-2109-8765-4321-aaaaaaaaaaaa",
    ],
    ids=["placeholder", "all-zero", "date", "phone", "uuid-zero", "uuid-digits"],
)
def test_dashed_negatives(chk, text: str) -> None:
    assert rules(chk, text) == []


@pytest.mark.parametrize(
    "text",
    [
        f"Account ID: {ACCT}",
        f"account id {ACCT}",
        f"Account {ACCT}",
        f"Account number: {ACCT_DASHED}",
        f"Account ID\n{ACCT}",
        fullwidth(f"Account ID: {ACCT}"),
        f"アカウント ID: {ACCT}",
        f"アカウント：{fullwidth(ACCT)}",
        f"アカウント番号 {ACCT_DASHED}",
        f"ア カ ウ ン ト ID {ACCT}",
    ],
    ids=[
        "en-id",
        "en-lowercase",
        "en-account-only",
        "en-number-dashed",
        "en-value-on-next-line",
        "en-full-width",
        "ja-id",
        "ja-full-width-colon-and-digits",
        "ja-number-dashed",
        "ja-spaced-katakana",
    ],
)
def test_labelled_account_id_is_found(chk, text: str) -> None:
    assert rules(chk, text) == [chk.RULE_ACCOUNT]


@pytest.mark.parametrize(
    "text",
    [
        "Account ID:",
        "Account settings",
        "アカウント",
        "Account ID: 123456789012",
        "アカウント ID: 000000000000",
        f"Request ID {ACCT}",
        f"Accounts {ACCT}",
    ],
    ids=["label-only", "other-text", "ja-label-only", "en-placeholder", "ja-placeholder", "request-id", "plural"],
)
def test_label_negatives(chk, text: str) -> None:
    assert rules(chk, text) == []


# --------------------------------------------------------------------------
# Resource IDs
# --------------------------------------------------------------------------

PREFIXES = ("fs", "fsvol", "svm", "vpc", "vpce", "subnet", "sg", "eni", "i")


@pytest.mark.parametrize("prefix", PREFIXES)
def test_resource_id_per_prefix_is_found(chk, prefix: str) -> None:
    assert rules(chk, f"Resource: {prefix}-{HEX}") == [chk.RULE_RESOURCE]


def test_resource_id_with_an_ocr_confusion_is_found(chk) -> None:
    confused = HEX.replace("1", "l", 1)
    assert rules(chk, f"fs-{confused}") == [chk.RULE_RESOURCE]


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("suffix", ["0123456789abcdef0", "0123456789abcdef9", "0fedcba9876543210",
                                    "0abcdef123456789a", "aaaa1111aaaa1111a", "bbbb2222bbbb2222b",
                                    "cccc3333cccc3333c", "dddd4444dddd4444d"])
def test_placeholder_resource_ids_are_not_findings(chk, prefix: str, suffix: str) -> None:
    assert rules(chk, f"{prefix}-{suffix}") == []


def test_placeholder_with_ocr_confusions_is_still_a_placeholder(chk) -> None:
    assert rules(chk, "fs-O123456789abcdefO") == []


@pytest.mark.parametrize(
    "text",
    [f"fs-{HEX[:-1]}", f"fs-{HEX}0", f"xfs-{HEX}", f"fs-{HEX}-1", "fs-1a2b3c4d", "fsx-" + HEX],
    ids=["sixteen-hex", "eighteen-hex", "boundary-before", "boundary-after", "legacy-eight-hex", "other-prefix"],
)
def test_resource_id_negatives(chk, text: str) -> None:
    assert rules(chk, text) == []


# --------------------------------------------------------------------------
# E-mail
# --------------------------------------------------------------------------


def test_email_on_a_real_looking_domain_is_found(chk) -> None:
    assert rules(chk, f"Contact: {EMAIL}") == [chk.RULE_EMAIL]


@pytest.mark.parametrize(
    "domain",
    [
        "malicious.net",
        "corp.local",
        "example.com",
        "example.org",
        "example.net",
        "users.noreply.github.com",
        "mail.example.com",
        "host.test",
        "host.invalid",
        "printer.local",
    ],
)
def test_allowed_email_domains_are_not_findings(chk, domain: str) -> None:
    assert rules(chk, "someone" + "@" + domain) == []


def test_every_listed_allowed_domain_is_tested_above(chk) -> None:
    """Adding a domain to the allowlist must come with a case here."""
    tested = {"malicious.net", "corp.local", "example.com", "example.org", "example.net",
              "users.noreply.github.com"}
    assert set(chk.ALLOWED_EMAIL_DOMAINS) == tested


def test_image_file_names_are_not_email_addresses(chk) -> None:
    assert rules(chk, "logo" + "@" + "2x.png") == []


def test_ip_literal_is_not_an_email_domain(chk) -> None:
    assert rules(chk, "user" + "@" + ".".join(("10", "0", "3", "17"))) == []


def test_reserved_domain_followed_by_a_file_name_tail_is_not_a_finding(chk) -> None:
    assert rules(chk, "/share/customers/taro" + "@" + "example.com-invoice.pdf") == []


def test_a_real_looking_domain_with_the_same_tail_is_still_a_finding(chk) -> None:
    assert rules(chk, "/share/customers/" + LOCAL + "@" + "contoso.com-invoice.pdf") == [chk.RULE_EMAIL]


def test_a_reserved_name_inside_a_longer_label_is_not_exempt(chk) -> None:
    """'notexample.com' must not pass because it ends in 'example.com'."""
    assert rules(chk, "someone" + "@" + "notexample.com") == [chk.RULE_EMAIL]


# --------------------------------------------------------------------------
# Scope: what is deliberately not checked
# --------------------------------------------------------------------------


def test_private_ip_addresses_are_not_checked(chk) -> None:
    ips = [".".join(("10", "0", "3", "17")), ".".join(("172", "16", "8", "4")),
           ".".join(("192", "168", "1", "20")), ".".join(("203", "0", "113", "9"))]
    assert rules(chk, "Hosts " + " ".join(ips)) == []


def test_user_names_are_not_checked(chk) -> None:
    assert rules(chk, "Signed in as jdoe-admin / Alice Example") == []


# --------------------------------------------------------------------------
# Alignment with .gitleaks.toml
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def toml_rules() -> dict[str, dict]:
    data = tomllib.loads(GITLEAKS.read_text(encoding="utf-8"))
    return {r["id"]: r for r in data["rules"]}


def test_shared_rule_ids_exist_in_gitleaks_toml(chk, toml_rules) -> None:
    assert chk.RULE_ACCOUNT in toml_rules
    assert chk.RULE_RESOURCE in toml_rules


def test_account_placeholders_match_the_toml(chk, toml_rules) -> None:
    regexes = toml_rules[chk.RULE_ACCOUNT]["allowlist"]["regexes"]
    digit_literals = {r for r in regexes if re.fullmatch(r"\d{12}", r)}
    assert digit_literals == set(chk.PLACEHOLDER_ACCOUNT_IDS), (
        "the gate and .gitleaks.toml disagree on which 12-digit values are placeholders"
    )


def test_resource_placeholders_match_the_toml(chk, toml_rules) -> None:
    tail = r"(?:$|[^A-Za-z0-9-])"
    suffixes = set()
    for r in toml_rules[chk.RULE_RESOURCE]["allowlist"]["regexes"]:
        assert r.startswith("-") and r.endswith(tail), f"unrecognised allowlist shape: {r}"
        suffixes.add(r[1 : -len(tail)])
    assert suffixes == set(chk.PLACEHOLDER_RESOURCE_SUFFIXES), (
        "the gate and .gitleaks.toml disagree on resource-ID placeholders"
    )


def test_resource_prefixes_cover_the_toml_prefixes(chk, toml_rules) -> None:
    regex = toml_rules[chk.RULE_RESOURCE]["regex"]
    group = re.search(r"\(\?:((?:\w+\|)+\w+)\)-\[0-9a-f\]\{17\}", regex)
    assert group, "could not read the prefix list out of the aws-resource-id regex"
    toml_prefixes = set(group.group(1).split("|"))
    assert toml_prefixes <= set(PREFIXES)
    assert {"vpce", "eni"} <= set(PREFIXES)  # the gate adds these two on purpose


# --------------------------------------------------------------------------
# Redaction: output never republishes the value
# --------------------------------------------------------------------------


def test_evidence_never_contains_the_matched_value(chk) -> None:
    samples = [
        f"arn:aws:states:ap-northeast-1:{ACCT}:execution:wf",
        ACCT_DASHED,
        f"Account ID: {ACCT}",
        f"fs-{HEX}",
        EMAIL,
    ]
    for text in samples:
        for m in chk.find_matches(text):
            assert ACCT not in m.evidence
            assert ACCT_DASHED not in m.evidence
            assert HEX not in m.evidence
            assert LOCAL not in m.evidence
            assert "*" in m.evidence


def test_account_evidence_keeps_the_arn_shape(chk) -> None:
    (m,) = chk.find_matches(f"arn:aws:iam::{ACCT}:role/x")
    assert m.evidence == "aws:iam::" + "*" * 12


def test_report_output_is_redacted(chk, capsys) -> None:
    finding = chk.Finding("docs/x.png", chk.RULE_ACCOUNT, "aws:iam::" + "*" * 12, ["original"], (1, 2, 3, 4))
    chk.report(chk.ScanResult([finding], ["docs/y.png: boom"], 2), [], annotate=True)
    out = capsys.readouterr()
    assert "docs/x.png: aws-account-id-suspicious at x=1 y=2 3x4 [original]" in out.out
    assert "::error file=docs/x.png" in out.out
    assert "ERROR docs/y.png: boom" in out.err


# --------------------------------------------------------------------------
# OCR text assembly (pure functions, fed canned TSV)
# --------------------------------------------------------------------------

TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def tsv_row(block: int, par: int, line: int, left: int, top: int, w: int, h: int, text: str, conf: float = 90.0) -> str:
    return f"5\t1\t{block}\t{par}\t{line}\t1\t{left}\t{top}\t{w}\t{h}\t{conf}\t{text}"


def test_parse_tsv_keeps_only_words(chk) -> None:
    tsv = "\n".join(
        [TSV_HEADER, "1\t1\t0\t0\t0\t0\t0\t0\t100\t100\t-1\t", tsv_row(1, 1, 1, 5, 6, 7, 8, "hello"),
         "5\t1\t1\t1\t1\t2\t9\t9\t9\t9\t10\t   "]
    )
    words = chk.parse_tsv(tsv)
    assert [w.text for w in words] == ["hello"]
    assert (words[0].left, words[0].top, words[0].width, words[0].height) == (5, 6, 7, 8)


def test_parse_tsv_rejects_a_malformed_row(chk) -> None:
    bad = "\n".join([TSV_HEADER, "5\t1\t1\t1\t1\t1\tx\t6\t7\t8\t90\ttext"])
    with pytest.raises(chk.ToolError):
        chk.parse_tsv(bad)


def test_words_map_back_to_original_coordinates(chk) -> None:
    """A match on a 2x pass with a 10 px border reports original-image pixels."""
    spec = chk.PassSpec("gray2x", scale=2, prepare_gray=True)
    words = chk.parse_tsv("\n".join([
        TSV_HEADER,
        tsv_row(1, 1, 1, 10, 10, 20, 10, "Role"),
        tsv_row(1, 1, 1, 110, 30, 400, 20, f"arn:aws:iam::{ACCT}:role/x"),
    ]))
    (finding,) = chk.words_to_findings("docs/x.png", words, spec, had_border=True)
    assert finding.box == (50, 10, 200, 10)
    assert finding.passes == ["gray2x"]
    assert ACCT not in finding.evidence


def test_a_match_spanning_two_lines_is_boxed_over_both(chk) -> None:
    spec = chk.PassSpec("original")
    words = chk.parse_tsv("\n".join([
        TSV_HEADER,
        tsv_row(1, 1, 1, 100, 100, 300, 20, "arn:aws:states:ap-northeast-1:"),
        tsv_row(1, 1, 2, 100, 130, 200, 20, f"{ACCT}:execution:wf"),
    ]))
    (finding,) = chk.words_to_findings("docs/x.png", words, spec, had_border=False)
    assert finding.box == (100, 100, 300, 50)


def test_findings_from_several_passes_at_one_place_merge(chk) -> None:
    a = chk.Finding("p.png", chk.RULE_ACCOUNT, "e", ["original"], (100, 100, 50, 10))
    b = chk.Finding("p.png", chk.RULE_ACCOUNT, "e", ["gray2x"], (102, 101, 50, 10))
    c = chk.Finding("p.png", chk.RULE_ACCOUNT, "e", ["original"], (100, 300, 50, 10))
    d = chk.Finding("p.png", chk.RULE_RESOURCE, "e", ["inverted2x"], (100, 100, 50, 10))
    merged = chk.merge_findings([a, b, c, d])
    assert len(merged) == 3
    assert merged[0].passes == ["original", "gray2x"]


# --------------------------------------------------------------------------
# PNG metadata
# --------------------------------------------------------------------------


SIGNATURE = b"\x89PNG\r\n\x1a\n"


def chunk(ctype: bytes, body: bytes, crc: int | None = None) -> bytes:
    real = zlib.crc32(ctype + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + ctype + body + struct.pack(">I", real if crc is None else crc)


def png(*extra: bytes, with_iend: bool = True) -> bytes:
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\xff\xff")
    out = SIGNATURE + chunk(b"IHDR", ihdr) + b"".join(extra) + chunk(b"IDAT", idat)
    return out + (chunk(b"IEND", b"") if with_iend else b"")


def text_chunk(key: str, value: str) -> bytes:
    return chunk(b"tEXt", key.encode("latin-1") + b"\x00" + value.encode("latin-1"))


def ztxt_chunk(key: str, value: str) -> bytes:
    return chunk(b"zTXt", key.encode("latin-1") + b"\x00\x00" + zlib.compress(value.encode("latin-1")))


def itxt_chunk(key: str, value: str, compressed: bool) -> bytes:
    body = zlib.compress(value.encode()) if compressed else value.encode()
    head = key.encode() + b"\x00" + (b"\x01" if compressed else b"\x00") + b"\x00" + b"\x00" + b"\x00"
    return chunk(b"iTXt", head + body)


UNIX_HOME = "/Users/" + "someone" + "/Desktop/shot.png"
WIN_HOME = "C:" + "\\Users\\" + "someone" + "\\shot.png"


def test_software_key_alone_is_not_a_finding(chk) -> None:
    assert chk.png_metadata_findings(png(text_chunk("Software", "mask_screenshots.py"))) == []


def test_a_png_without_any_text_chunk_is_clean(chk) -> None:
    assert chk.png_metadata_findings(png()) == []


def test_icc_phys_and_time_chunks_are_not_findings(chk) -> None:
    extra = chunk(b"pHYs", struct.pack(">IIB", 2835, 2835, 1)) + chunk(b"tIME", bytes(7))
    assert chk.png_metadata_findings(png(extra)) == []


@pytest.mark.parametrize(
    "make",
    [
        lambda: text_chunk("Comment", UNIX_HOME),
        lambda: text_chunk("Comment", WIN_HOME),
        lambda: ztxt_chunk("Comment", UNIX_HOME),
        lambda: itxt_chunk("Comment", UNIX_HOME, compressed=False),
        lambda: itxt_chunk("Comment", UNIX_HOME, compressed=True),
        lambda: itxt_chunk("XML:com.adobe.xmp", WIN_HOME, compressed=True),
    ],
    ids=["text-unix", "text-windows", "ztxt", "itxt-plain", "itxt-compressed", "xmp-itxt"],
)
def test_home_paths_in_any_text_chunk_are_findings(chk, make) -> None:
    details = chk.png_metadata_findings(png(make()))
    assert len(details) == 1 and "home-directory path" in details[0]


def test_identifiers_in_a_text_value_are_findings(chk) -> None:
    arn_detail = chk.png_metadata_findings(png(text_chunk("Description", f"arn:aws:iam::{ACCT}:role/x")))
    mail_detail = chk.png_metadata_findings(png(text_chunk("Author", EMAIL)))
    assert chk.RULE_ACCOUNT in arn_detail[0]
    assert chk.RULE_EMAIL in mail_detail[0]


def test_metadata_evidence_never_contains_the_value(chk) -> None:
    (detail,) = chk.png_metadata_findings(png(text_chunk("Description", f"arn:aws:iam::{ACCT}:role/x")))
    assert ACCT not in detail and "someone" not in detail
    (detail,) = chk.png_metadata_findings(png(text_chunk("Comment", UNIX_HOME)))
    assert "someone" not in detail


def test_an_unusual_key_is_not_printed(chk) -> None:
    key = "/Users/" + "someone"
    (detail,) = chk.png_metadata_findings(png(text_chunk(key, UNIX_HOME)))
    assert "someone" not in detail and "<key>" in detail


@pytest.mark.parametrize("body", [b"", b"\x00\x01\x02"], ids=["empty", "payload"])
def test_any_exif_chunk_is_a_finding(chk, body: bytes) -> None:
    details = chk.png_metadata_findings(png(chunk(b"eXIf", body)))
    assert len(details) == 1 and "eXIf" in details[0]


def test_bad_signature_raises(chk) -> None:
    with pytest.raises(chk.ImageReadError):
        chk.read_png_chunks(b"GIF89a" + bytes(40))


def test_bad_crc_raises(chk) -> None:
    bad = png(chunk(b"tEXt", b"Software\x00x", crc=0))
    with pytest.raises(chk.ImageReadError, match="CRC"):
        chk.read_png_chunks(bad)


@pytest.mark.parametrize("cut", [12, 30, -3, -12], ids=["in-ihdr", "in-idat-header", "in-iend-crc", "no-iend"])
def test_truncated_stream_raises(chk, cut: int) -> None:
    data = png(text_chunk("Software", "x"))
    with pytest.raises(chk.ImageReadError):
        chk.read_png_chunks(data[:cut])


def test_a_stream_ending_before_iend_raises(chk) -> None:
    with pytest.raises(chk.ImageReadError, match="IEND"):
        chk.read_png_chunks(png(with_iend=False))


def test_decompression_is_capped(chk) -> None:
    bomb = chunk(b"zTXt", b"Comment\x00\x00" + zlib.compress(b"A" * (3 * 1024 * 1024)))
    with pytest.raises(chk.ImageReadError, match="1 MiB"):
        chk.png_metadata_findings(png(bomb))


def test_a_corrupt_compressed_chunk_raises(chk) -> None:
    broken = chunk(b"zTXt", b"Comment\x00\x00" + b"not zlib data")
    with pytest.raises(chk.ImageReadError):
        chk.png_metadata_findings(png(broken))


def test_exif_in_a_non_png_file_is_a_finding(chk, tmp_path: Path) -> None:
    from PIL import Image

    exif = Image.Exif()
    exif[0x010E] = "synthetic description"
    target = tmp_path / "photo.jpg"
    Image.new("RGB", (16, 16), "white").save(target, exif=exif)
    details = chk.metadata_details(target)
    assert any("EXIF" in d for d in details)


def test_a_clean_non_png_file_has_no_metadata_findings(chk, tmp_path: Path) -> None:
    from PIL import Image

    target = tmp_path / "plain.jpg"
    Image.new("RGB", (16, 16), "white").save(target)
    assert chk.metadata_details(target) == []


def test_a_jpeg_comment_with_a_home_path_is_a_finding(chk, tmp_path: Path) -> None:
    from PIL import Image

    target = tmp_path / "note.jpg"
    Image.new("RGB", (16, 16), "white").save(target, comment=UNIX_HOME.encode())
    details = chk.metadata_details(target)
    assert any("home-directory path" in d for d in details)


# --------------------------------------------------------------------------
# The tracked corpus itself
# --------------------------------------------------------------------------


def test_every_tracked_image_has_readable_clean_metadata(chk) -> None:
    """Metadata needs no OCR, so it can be checked on the real tree under pytest."""
    images = chk.tracked_images()
    assert images, "git tracks no images; the discovery would make this test vacuous"
    problems = {}
    for path in images:
        details = chk.metadata_details(path)
        if details:
            problems[chk.display_path(path)] = details
    assert not problems, f"tracked images carry identifying metadata: {problems}"
