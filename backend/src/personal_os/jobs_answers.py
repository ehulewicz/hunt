from __future__ import annotations

import hashlib
import re


def normalize_answer_key(value: str) -> str:
    """Stable key for deterministic question matching across harmless punctuation."""
    return re.sub(r"[^a-z0-9+#]+", " ", value.casefold()).strip()


def answer_storage_key(normalized_key: str) -> str:
    digest = hashlib.sha256(normalized_key.encode()).hexdigest()[:24]
    return f"long-form-{digest}"
