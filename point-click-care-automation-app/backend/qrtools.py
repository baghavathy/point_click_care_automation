"""Decode TOTP secrets from uploaded QR-code images.

Authenticator QR codes encode an ``otpauth://totp/...?secret=XXXX`` URI.
We decode the image with OpenCV's QRCodeDetector (no external system library
needed) and pull the ``secret`` parameter out of the URI.

The user may instead paste the raw secret/hash directly, in which case this
module is not involved.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

# OpenCV auto-sizes its internal thread pool off the host's CPU count, which in
# a cgroup-limited container can wildly overshoot the actual CPU quota (seen:
# 20 "CPUs" reported vs. a 2-CPU quota) — spawning dozens of idle OS threads per
# process for a feature (QR decode) that runs rarely and doesn't need them.
cv2.setNumThreads(1)


def _decode(detector, img) -> Optional[str]:
    data, _points, _straight = detector.detectAndDecode(img)
    return data or None


def _read_qr(image_bytes: bytes) -> Optional[str]:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    detector = cv2.QRCodeDetector()

    data = _decode(detector, img)
    if data:
        return data

    # Tiny / dense QR images (e.g. one pixel per module) confuse the detector.
    # Add a quiet-zone border and progressively upscale, then retry.
    bordered = cv2.copyMakeBorder(
        img, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=(255, 255, 255)
    )
    for scale in (4, 8, 12):
        big = cv2.resize(
            bordered, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST
        )
        data = _decode(detector, big)
        if data:
            return data
    return None


def extract_secret(image_bytes: bytes) -> Optional[str]:
    """Return the base32 TOTP secret from a QR image, or None if not found."""
    payload = _read_qr(image_bytes)
    if not payload:
        return None
    payload = payload.strip()
    if payload.lower().startswith("otpauth://"):
        query = parse_qs(urlparse(payload).query)
        secret = query.get("secret", [None])[0]
        if secret:
            return secret.strip()
    # Some apps encode just the bare secret string in the QR.
    return payload
