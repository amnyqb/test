"""Workflow cost ledger (F12).

The economic hypothesis is the part of this project most likely to fail
quietly, so every stage is metered from the first milestone: model tokens,
wall-clock, retries and - the one most often omitted - active human minutes.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from ddg.store import Store


class CostLedger:
    def __init__(self, store: Store, run_id: str) -> None:
        self.store = store
        self.run_id = run_id

    def record(
        self,
        stage: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        seconds: float = 0.0,
        retries: int = 0,
        human_minutes: float = 0.0,
        note: str = "",
    ) -> None:
        self.store.conn.execute(
            "INSERT INTO costs (run_id, stage, input_tokens, output_tokens, seconds,"
            " retries, human_minutes, note) VALUES (?,?,?,?,?,?,?,?)",
            (self.run_id, stage, input_tokens, output_tokens, seconds, retries,
             human_minutes, note),
        )

    @contextmanager
    def timed(self, stage: str, **kw: object) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.record(stage, seconds=time.perf_counter() - t0, **kw)  # type: ignore[arg-type]

    def totals(self) -> dict[str, float]:
        row = self.store.conn.execute(
            "SELECT COALESCE(SUM(input_tokens),0) i, COALESCE(SUM(output_tokens),0) o,"
            " COALESCE(SUM(seconds),0) s, COALESCE(SUM(retries),0) r,"
            " COALESCE(SUM(human_minutes),0) h FROM costs WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        return {
            "input_tokens": row["i"], "output_tokens": row["o"],
            "seconds": round(row["s"], 4), "retries": row["r"],
            "human_minutes": row["h"],
        }
