"""Output G0. Gate."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Route = Literal["lookup", "agent"]


class GateOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    route: Route
    reason: str = ""
