"""Pydantic schemas for API responses."""
from pydantic import BaseModel
from typing import Optional


class TZWLNBBScanRow(BaseModel):
    ticker: str
    universe: str
    close: Optional[float] = None
    volume: Optional[float] = None
    t_signal: str = ""
    z_signal: str = ""
    l_signal: str = ""
    preup_signal: str = ""
    predn_signal: str = ""
    lane1_label: str = ""
    lane3_label: str = ""
    ne_suffix: str = ""
    wick_suffix: str = ""
    has_t_signal: bool = False
    has_z_signal: bool = False
    has_l_signal: bool = False
    has_preup: bool = False
    has_predn: bool = False
    volume_bucket: str = ""
    bull_priority_code: int = 0
    bear_priority_code: int = 0
