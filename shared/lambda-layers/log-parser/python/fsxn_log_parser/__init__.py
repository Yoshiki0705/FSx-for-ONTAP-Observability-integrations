"""FSx for ONTAP Log Parser Lambda Layer.

Provides utilities for parsing FSx for NetApp ONTAP audit logs
in EVTX, XML, and JSON formats.
"""

__version__ = "1.1.0"

from .parser import (
    FIELD_MAPPING,
    METRIC_EVENTS_PARSED,
    METRIC_PARSE_DURATION,
    METRIC_PARSE_ERRORS,
    SOURCE,
    AuditEvent,
    FormatDetector,
    MetricsCallback,
    ParseError,
    ParseResult,
    detect_format,
    normalize_event,
    parse,
    parse_evtx,
    parse_json_log,
    parse_xml_log,
    register_format,
    validate_event,
)

# Declared explicitly because these names are re-exports, not local usages.
# Without __all__ a linter reads them as unused imports and the obvious "fix" is
# to delete them -- which would remove the layer's public API. CI's own smoke
# test imports parse, detect_format, validate_event and __version__ from this
# module, so that deletion would surface as an ImportError at deploy time
# rather than here.
__all__ = [
    "FIELD_MAPPING",
    "METRIC_EVENTS_PARSED",
    "METRIC_PARSE_DURATION",
    "METRIC_PARSE_ERRORS",
    "SOURCE",
    "AuditEvent",
    "FormatDetector",
    "MetricsCallback",
    "ParseError",
    "ParseResult",
    "__version__",
    "detect_format",
    "normalize_event",
    "parse",
    "parse_evtx",
    "parse_json_log",
    "parse_xml_log",
    "register_format",
    "validate_event",
]
