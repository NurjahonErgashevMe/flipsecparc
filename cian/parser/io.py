from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Iterator

from .models import FailedHouse, InputHouse, ParsedHouse

log = logging.getLogger(__name__)


def load_input_houses(
    path: Path,
    *,
    moscow_only: bool,
    limit: int | None = None,
    offset: int = 0,
) -> list[InputHouse]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("input.json должен быть массивом объектов")

    houses: list[InputHouse] = []
    for item in raw:
        house = InputHouse.from_dict(item)
        if moscow_only and not house.is_moscow():
            continue
        if house.jil_type and house.jil_type != "Жилой":
            continue
        houses.append(house)

    if offset:
        houses = houses[offset:]
    if limit is not None:
        houses = houses[:limit]
    return houses


def iter_input_houses(
    path: Path,
    *,
    moscow_only: bool,
) -> Iterator[InputHouse]:
    """Потоковая загрузка для очень больших файлов (ijson не требуется — читаем целиком)."""
    yield from load_input_houses(path, moscow_only=moscow_only)


class ResultWriter:
    """Потокобезопасная запись JSONL."""

    def __init__(
        self,
        success_path: Path,
        failed_path: Path,
    ) -> None:
        self._success_path = success_path
        self._failed_path = failed_path
        self._lock = threading.Lock()
        
        success_path.parent.mkdir(parents=True, exist_ok=True)
        failed_path.parent.mkdir(parents=True, exist_ok=True)

        self._success_ids: set[int] = set()
        self._failed_ids: set[int] = set()
        self._load_state()

    @property
    def processed_ids(self) -> set[int]:
        return self._success_ids | self._failed_ids

    def _load_state(self) -> None:
        if self._success_path.is_file():
            with self._success_path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        self._success_ids.add(int(data["source"]["house_id"]))
                    except Exception:
                        pass
                        
        if self._failed_path.is_file():
            with self._failed_path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        self._failed_ids.add(int(data["source"]["house_id"]))
                    except Exception:
                        pass

    def _commit(self, house_id: int, path: Path, line: str, is_success: bool) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if is_success:
            self._success_ids.add(house_id)
        else:
            self._failed_ids.add(house_id)

    def flush_checkpoint(self) -> None:
        pass

    def write_success(self, result: ParsedHouse) -> None:
        line = json.dumps(result.to_dict(), ensure_ascii=False)
        house_id = int(result.source["house_id"])
        with self._lock:
            self._commit(house_id, self._success_path, line, True)

    def write_failure(self, result: FailedHouse) -> None:
        line = json.dumps(result.to_dict(), ensure_ascii=False)
        house_id = int(result.source["house_id"])
        with self._lock:
            self._commit(house_id, self._failed_path, line, False)

    def is_done(self, house_id: int) -> bool:
        return house_id in self.processed_ids


def jsonl_to_json(jsonl_path: Path, json_path: Path) -> int:
    rows: list[dict[str, Any]] = []
    with jsonl_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(rows)
