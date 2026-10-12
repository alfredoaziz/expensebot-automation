"""Remembers which Gmail message IDs were already handled, so each bank
email is reported exactly once — including the ones deliberately NOT
written to the sheet (own-account transfers, promos), which the sheet's
Dedupe Key column can't cover.

Stored as a small JSON file on a Docker volume ({"ids": [...]}) rather than
as a Gmail label, so the bot only needs the read-only Gmail scope.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Far more than the bot ever sees inside its Gmail search window — older
# IDs fall out of that window anyway, so trimming them is safe.
_MAX_IDS = 5000


class ProcessedStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._ids: list[str] = self._load()
        self._id_set = set(self._ids)

    def _load(self) -> list[str]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            logger.warning(
                "State Gmail %s rusak/tidak terbaca — mulai dari kosong.", self._path
            )
            return []
        ids = data.get("ids", []) if isinstance(data, dict) else []
        return [str(i) for i in ids][-_MAX_IDS:]

    def __contains__(self, message_id: str) -> bool:
        return message_id in self._id_set

    def add(self, message_id: str) -> None:
        if message_id in self._id_set:
            return
        self._ids = [*self._ids, message_id][-_MAX_IDS:]
        self._id_set = set(self._ids)
        self._save()

    def _save(self) -> None:
        """Best-effort atomic write — a failure only means an already
        reported email may be reported once more after a restart (the
        sheet's Dedupe Key still prevents a duplicate row)."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps({"ids": self._ids}), encoding="utf-8")
            tmp_path.replace(self._path)
        except OSError:
            logger.warning(
                "Gagal menyimpan state Gmail ke %s", self._path, exc_info=True
            )
