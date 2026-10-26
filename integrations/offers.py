"""When Tora offers to connect a service (CI-10).

Only when something they asked for needs it ("email Lee" without Gmail) -
never because a Gmail tab is open. At most once a session per service.
"Not now" holds for the session; "don't ask again" holds until they ask
to connect it themselves, which is the only thing that lifts it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

FILE = "connect_offers.json"


class Offers:
    def __init__(self, folder: Optional[Path] = None) -> None:
        self._path = Path(folder) / FILE if folder is not None else None
        self._this_session: set[str] = set()         # offered, or "not now"
        self._never: set[str] = set(self._load())

    def may_offer(self, service: str) -> bool:
        return service not in self._never and service not in self._this_session

    def offered(self, service: str) -> None:
        self._this_session.add(service)

    def declined(self, service: str, *, never: bool = False) -> None:
        self._this_session.add(service)
        if never:
            self._never.add(service)
            self._save()

    def asked_for(self, service: str) -> None:
        """They asked to connect it: any "don't ask again" is over."""
        if service in self._never:
            self._never.discard(service)
            self._save()

    def _load(self) -> list:
        if self._path is None:
            return []
        try:
            return list(json.loads(self._path.read_text(encoding="utf-8")).get("never") or [])
        except (OSError, ValueError):
            return []

    def _save(self) -> None:
        if self._path is None:
            return
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"never": sorted(self._never)}), encoding="utf-8")
        os.replace(tmp, self._path)


_offers: Optional[Offers] = None


def offers() -> Offers:
    global _offers
    if _offers is None:
        from config import data_dir
        _offers = Offers(data_dir())
    return _offers
