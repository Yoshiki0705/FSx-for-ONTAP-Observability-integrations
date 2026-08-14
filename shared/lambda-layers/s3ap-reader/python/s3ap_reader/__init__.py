"""S3 Access Point Reader Lambda Layer.

Provides utilities for reading objects from S3 Access Points
with proper error handling and retry logic.
"""

__version__ = "0.1.0"

from .reader import S3AccessPointReader

# Re-export, not a local usage. See the note in fsxn_log_parser/__init__.py.
__all__ = ["S3AccessPointReader", "__version__"]
