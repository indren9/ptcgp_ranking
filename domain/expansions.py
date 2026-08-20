from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import re

# Pocket: A1, B12, C123, A4a. TCG/Live: CRI, PRE, TWM, SSP, etc.
SET_CODE_RE = re.compile(r"^(?:[A-Z]\d{1,3}[a-z]?|[A-Z]{2,6}\d?)$")


def strip_expansion_code_prefix(code: Optional[str], name: Optional[str]) -> Optional[str]:
    if not name:
        return name
    clean = str(name).strip()
    if not code:
        return clean
    pattern = rf"^\s*{re.escape(str(code).strip())}\s*(?:[-–—:]\s*)+"
    stripped = re.sub(pattern, "", clean, count=1, flags=re.IGNORECASE).strip()
    return stripped or clean


@dataclass(frozen=True)
class Expansion:
    code: Optional[str]
    name: Optional[str]
    is_current: bool = False
    rotation: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", strip_expansion_code_prefix(self.code, self.name))

    def _display_name(self) -> Optional[str]:
        code = (self.code or "").strip()
        name = (self.name or "").strip()
        if not code and not name:
            return None
        if not code:
            return name
        if not name:
            return code

        return f"{code} — {name}"

    def label(self) -> str:
        display = self._display_name()
        return display or "AUTO (latest on site)"

__all__ = ["Expansion", "SET_CODE_RE", "strip_expansion_code_prefix"]
