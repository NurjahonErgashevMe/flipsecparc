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
    """Потокобезопасная запись JSONL + чекпоинт обработанных house_id."""

    def __init__(
        self,
        success_path: Path,
        failed_path: Path,
        checkpoint_path: Path,
    ) -> None:
        self._success_path = success_path
        self._failed_path = failed_path
        self._checkpoint_path = checkpoint_path
        self._lock = threading.Lock()
        self._processed_ids = self._load_checkpoint()
        self._writes_since_checkpoint = 0
        self._checkpoint_every = 25
        success_path.parent.mkdir(parents=True, exist_ok=True)
        failed_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def processed_ids(self) -> set[int]:
        return self._processed_ids

    def _load_checkpoint(self) -> set[int]:
        if not self._checkpoint_path.is_file():
            return set()
        try:
            data = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
            return {int(x) for x in data.get("processed_house_ids", [])}
        except (json.JSONDecodeError, TypeError, ValueError):
            log.warning("Повреждён checkpoint, начинаем с нуля")
            return set()

    def _save_checkpoint(self) -> None:
        payload = {
            "processed_house_ids": sorted(self._processed_ids),
            "count": len(self._processed_ids),
        }
        self._checkpoint_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def _commit(self, house_id: int, path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        self._processed_ids.add(house_id)
        self._writes_since_checkpoint += 1
        if self._writes_since_checkpoint >= self._checkpoint_every:
            self._save_checkpoint()
            self._writes_since_checkpoint = 0

    def flush_checkpoint(self) -> None:
        with self._lock:
            if self._writes_since_checkpoint:
                self._save_checkpoint()
                self._writes_since_checkpoint = 0

    def write_success(self, result: ParsedHouse) -> None:
        line = json.dumps(result.to_dict(), ensure_ascii=False)
        house_id = int(result.source["house_id"])
        with self._lock:
            self._commit(house_id, self._success_path, line)

    def write_failure(self, result: FailedHouse) -> None:
        line = json.dumps(result.to_dict(), ensure_ascii=False)
        house_id = int(result.source["house_id"])
        with self._lock:
            self._commit(house_id, self._failed_path, line)

    def is_done(self, house_id: int) -> bool:
        return house_id in self._processed_ids


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
