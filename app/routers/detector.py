import json
import logging
from datetime import datetime, timezone
from urllib.parse import parse_qs
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import get_settings
from app.models.schemas import TokenInfo, DetectorResponse
from app.services.dex_screener import dex_screener_client
from app.services.helius import helius_client
from app.services.solana_rpc import solana_rpc_client
from app.services.trust_scorer import trust_scorer

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()
limiter = Limiter(key_func=get_remote_address)


def parse_apix_query_field(query_string: str) -> dict:
    if not query_string:
        return {}
    parsed = parse_qs(query_string)
    return {key: values[0] if values else None for key, values in parsed.items()}


def safe_parse_int(value: Optional[str], default: int, min_val: int = 1, max_val: int = 50) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
        return max(min_val, min(parsed, max_val))
    except (ValueError, TypeError):
        return default


def safe_parse_float(value: Optional[str], default: float, min_val: float = 0.0) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
        return max(min_val, parsed)
    except (ValueError, TypeError):
        return default


async def process_tokens(tokens_data: list[dict], limit: int) -> list[TokenInfo]:
    enriched_tokens = []
    seen_contracts = set()

    for token_data in tokens_data:
        contract_address = token_data["contract_address"]

        if contract_address in seen_contracts:
            continue
        seen_contracts.add(contract_address)

        metadata = await helius_client.get_token_metadata(contract_address)
        holder_data = await solana_rpc_client.get_holder_distribution(contract_address)
        age_hours = token_data["age_minutes"] / 60

        trust_score, risk_level, risk_factors = trust_scorer.calculate_trust_score(
            mint_authority_enabled=metadata["mint_authority_enabled"],
            freeze_authority_enabled=metadata["freeze_authority_enabled"],
            lp_locked_percent=holder_data["lp_locked_percent"],
            top_10_holder_percent=holder_data["top_10_percent"],
            age_hours=age_hours
        )

        token_info = TokenInfo(
            name=token_data["name"],
            symbol=token_data["symbol"],
            contract_address=contract_address,
            created_at=token_data.get("created_at"),
            age_minutes=token_data["age_minutes"],
            trust_score=trust_score,
            risk_level=risk_level,
            liquidity_usd=token_data["liquidity_usd"],
            price_usd=token_data.get("price_usd"),
            market_cap_usd=token_data.get("market_cap_usd"),
            volume_24h_usd=token_data.get("volume_24h_usd"),
            risk_factors=risk_factors,
            dex=token_data["dex"],
            pair_address=token_data["pair_address"],
            pair_url=token_data["pair_url"]
        )

        enriched_tokens.append(token_info)

        if len(enriched_tokens) >= limit:
            break

    enriched_tokens.sort(key=lambda x: x.trust_score, reverse=True)
    return enriched_tokens


def get_ready_response() -> dict:
    return {
        "status": "ready",
        "message": "Solana Meme Coin Detector API - Detects recently launched meme coins with trust scores",
        "version": settings.version,
        "parameters": {
            "limit": {
                "type": "number",
                "description": f"Number of tokens to return (default: {settings.default_tokens_limit}, max: {settings.max_tokens_limit})",
                "required": False,
                "example": 10
            },
            "min_liquidity": {
                "type": "number",
                "description": f"Minimum liquidity in USD (default: {settings.default_min_liquidity})",
                "required": False,
                "example": 5000
            }
        },
        "apix_format": {
            "query": "limit=10&min_liquidity=5000"
        },
        "trust_score_info": {
            "range": "0-100 (0=Extreme Risk, 100=Safe)",
            "factors": [
                "Mint Authority (25%): Can creator mint more tokens?",
                "Freeze Authority (20%): Can creator freeze accounts?",
                "LP Locked (25%): Is liquidity locked/burned?",
                "Holder Concentration (20%): Do top 10 wallets hold too much?",
                "Token Age (10%): How old is the token?"
            ]
        }
    }


@router.post("/solana-meme-detector", response_model=None)
@limiter.limit("30/minute")
async def detect_meme_coins(request: Request):
    request_id = getattr(request.state, "request_id", "unknown")

    raw_body = await request.body()

    if settings.debug:
        logger.debug(f"[{request_id}] Raw body length: {len(raw_body) if raw_body else 0}")

    body_json = {}
    if raw_body and raw_body != b'':
        try:
            body_json = json.loads(raw_body)
        except json.JSONDecodeError as e:
            logger.warning(f"[{request_id}] Invalid JSON body: {e}")
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Invalid JSON body"}
            )

    params = {}
    if "query" in body_json and isinstance(body_json["query"], str):
        params = parse_apix_query_field(body_json["query"])
        logger.info(f"[{request_id}] APIX query params: {params}")
    else:
        params = body_json

    limit_raw = params.get("limit") or params.get("Limit") or params.get("count")
    min_liq_raw = params.get("min_liquidity") or params.get("minLiquidity") or params.get("min_liq")

    is_empty_request = not raw_body or raw_body == b'{}' or body_json == {}
    if not limit_raw and not min_liq_raw and is_empty_request:
        return JSONResponse(status_code=200, content=get_ready_response())

    limit = safe_parse_int(limit_raw, settings.default_tokens_limit, 1, settings.max_tokens_limit)
    min_liquidity = safe_parse_float(min_liq_raw, settings.default_min_liquidity, 0.0)

    logger.info(f"[{request_id}] Processing: limit={limit}, min_liquidity={min_liquidity}")

    tokens_data, cached, cached_at = await dex_screener_client.get_latest_solana_tokens(
        limit=limit * 2,
        min_liquidity=min_liquidity
    )

    if not tokens_data:
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "count": 0,
                "tokens": [],
                "message": "No new meme coins found matching criteria",
                "parameters_used": {"limit": limit, "min_liquidity": min_liquidity}
            }
        )

    enriched_tokens = await process_tokens(tokens_data, limit)

    cache_age = None
    if cached and cached_at:
        cache_age = int((datetime.now(timezone.utc) - cached_at).total_seconds())

    response = DetectorResponse(
        status="success",
        count=len(enriched_tokens),
        tokens=enriched_tokens,
        cached=cached,
        cache_age_seconds=cache_age
    )

    return response


@router.get("/solana-meme-detector", response_model=None)
@limiter.limit("30/minute")
async def detect_meme_coins_get(
    request: Request,
    limit: Optional[int] = None,
    min_liquidity: Optional[float] = None
):
    request_id = getattr(request.state, "request_id", "unknown")

    if limit is None:
        limit = settings.default_tokens_limit
    limit = max(1, min(limit, settings.max_tokens_limit))

    if min_liquidity is None:
        min_liquidity = settings.default_min_liquidity
    min_liquidity = max(0.0, min_liquidity)

    logger.info(f"[{request_id}] GET request: limit={limit}, min_liquidity={min_liquidity}")

    tokens_data, cached, cached_at = await dex_screener_client.get_latest_solana_tokens(
        limit=limit * 2,
        min_liquidity=min_liquidity
    )

    if not tokens_data:
        return JSONResponse(
            status_code=200,
            content={
                "status": "success",
                "count": 0,
                "tokens": [],
                "message": "No new meme coins found matching criteria"
            }
        )

    enriched_tokens = await process_tokens(tokens_data, limit)

    cache_age = None
    if cached and cached_at:
        cache_age = int((datetime.now(timezone.utc) - cached_at).total_seconds())

    response = DetectorResponse(
        status="success",
        count=len(enriched_tokens),
        tokens=enriched_tokens,
        cached=cached,
        cache_age_seconds=cache_age
    )

    return response
