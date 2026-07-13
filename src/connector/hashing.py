"""Канонизированный SHA-256 для журнала неизменяемости.

Единый способ хэширования сырых внешних данных (ответы нод, выписки
депозитариев, строки выписок): порядок ключей не влияет на хэш —
сверка при проверках воспроизводима.
"""

from __future__ import annotations

import hashlib
import json


def canonical_sha256(payload: dict | list) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
