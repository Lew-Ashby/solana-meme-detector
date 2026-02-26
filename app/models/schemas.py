from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class RiskLevel(str, Enum):
    EXTREME = "EXTREME"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    SAFE = "SAFE"


class RiskFactors(BaseModel):
    mint_authority_enabled: bool = Field(description="Whether mint authority is still active (can mint more tokens)")
    freeze_authority_enabled: bool = Field(description="Whether freeze authority is active (can freeze accounts)")
    lp_locked_percent: float = Field(description="Percentage of LP tokens that are locked/burned")
    top_10_holder_percent: float = Field(description="Percentage of supply held by top 10 holders")
    age_hours: float = Field(description="Age of the token in hours")


class TokenInfo(BaseModel):
    name: str
    symbol: str
    contract_address: str
    created_at: Optional[datetime] = None
    age_minutes: int
    trust_score: int = Field(ge=0, le=100, description="Trust score from 0-100")
    risk_level: RiskLevel
    liquidity_usd: float
    price_usd: Optional[float] = None
    market_cap_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    risk_factors: RiskFactors
    dex: str
    pair_address: str
    pair_url: str


class DetectorResponse(BaseModel):
    status: str
    count: int
    tokens: list[TokenInfo]
    cached: bool = False
    cache_age_seconds: Optional[int] = None
    disclaimer: str = "Trust scores are for informational purposes only. Always do your own research (DYOR) before investing. This is not financial advice."


class ReadyResponse(BaseModel):
    status: str = "ready"
    message: str
    version: str
    endpoints: dict
    example: dict
    apix_format: dict


class ErrorResponse(BaseModel):
    status: str = "error"
    error: str
    details: Optional[str] = None
