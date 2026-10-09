"""Record what cleaning removed or masked, so it can be reported, not assumed.

Two kinds of action, kept distinct as in project 1:

- **drop** removes whole rows that cannot be used at all (no usable time,
  a duplicate, outside the study window);
- **mask** blanks a single implausible value and keeps the rest of the row
  (an impossible taxi time should not cost the flight's delay label).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

REPORT_COLUMNS: tuple[str, ...] = ("source", "action", "rule", "column", "rows")


@dataclass
class ScreeningReport:
    records: list[dict[str, object]] = field(default_factory=list)

    def drop(
        self, frame: pd.DataFrame, condition: pd.Series, source: str, rule: str
    ) -> pd.DataFrame:
        condition = condition.fillna(False).astype(bool)
        if condition.any():
            self.records.append(
                {
                    "source": source,
                    "action": "drop",
                    "rule": rule,
                    "column": "",
                    "rows": int(condition.sum()),
                }
            )
        return frame.loc[~condition]

    def mask(
        self,
        frame: pd.DataFrame,
        column: str,
        condition: pd.Series,
        source: str,
        rule: str,
    ) -> pd.DataFrame:
        condition = condition.fillna(False).astype(bool)
        if condition.any():
            frame = frame.copy()
            frame.loc[condition, column] = pd.NA
            self.records.append(
                {
                    "source": source,
                    "action": "mask",
                    "rule": rule,
                    "column": column,
                    "rows": int(condition.sum()),
                }
            )
        return frame

    def outside(
        self,
        frame: pd.DataFrame,
        column: str,
        low: float,
        high: float,
        source: str,
    ) -> pd.DataFrame:
        """Mask values of ``column`` outside ``[low, high]``."""
        values = pd.to_numeric(frame[column], errors="coerce")
        return self.mask(
            frame, column, values.notna() & ~values.between(low, high), source, "range"
        )

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.records, columns=list(REPORT_COLUMNS))

    @property
    def total(self) -> int:
        return sum(int(r["rows"]) for r in self.records)
