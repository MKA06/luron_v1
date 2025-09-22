"""
Compatibility helpers for audio operations removed from Python 3.13.

Implements a minimal subset of audioop.ulaw2lin used by this project.

Based on the G.711 μ-law decoding algorithm.
"""

from __future__ import annotations

from typing import Final

try:  # Prefer stdlib if present (Python <= 3.12)
    import audioop as _audioop  # type: ignore

    def ulaw2lin(data: bytes, width: int) -> bytes:
        return _audioop.ulaw2lin(data, width)  # type: ignore[attr-defined]

except Exception:

    _BIAS: Final[int] = 0x84  # 132

    def _decode_sample(u: int) -> int:
        """Decode a single 8-bit μ-law byte to 16-bit signed PCM sample.

        Returns an int in the range [-32768, 32767].
        """
        u = (~u) & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = ((mantissa | 0x10) << (exponent + 3)) - _BIAS
        if sign:
            sample = -sample
        if sample > 32767:
            sample = 32767
        elif sample < -32768:
            sample = -32768
        return sample

    def ulaw2lin(data: bytes, width: int) -> bytes:
        """Convert G.711 μ-law bytes to linear PCM.

        Only ``width == 2`` (16-bit little-endian) is supported.
        """
        if width != 2:
            raise NotImplementedError("compat_audioop.ulaw2lin supports width=2 only")
        out = bytearray()
        for b in data:
            sample = _decode_sample(b)
            out += int(sample).to_bytes(2, byteorder="little", signed=True)
        return bytes(out)

