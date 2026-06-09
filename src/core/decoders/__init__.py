"""Core protocol decoders."""

from .serial import JUSTFLOAT_TAIL, SerialProtocolParser

__all__ = [
    "JUSTFLOAT_TAIL",
    "SerialProtocolParser",
]
