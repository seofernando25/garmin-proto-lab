"""Independent Garmin watch protocol compatibility research implementation.

Runtime code in this package is written from the protocol facts in
``spec/PROTOCOL.md``.  It does not import or execute Garmin application code.
"""

from .frame import Frame, ResponseStatus, decode_frame, encode_frame

__all__ = ["Frame", "ResponseStatus", "decode_frame", "encode_frame"]
