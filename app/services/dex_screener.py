import httpx
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional
from cachetools import TTLCache

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

token_cache = TTLCache(maxsize=100, ttl=settings.cache_ttl_seconds)


class DexScreenerClient:
    BASE_URL = "https://api.dexscreener.com"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def get_latest_solana_tokens(
        self,
        limit: int = 10,
        min_liquidity: float = 1000.0
    ) -> tuple[list[dict], bool, Optional[datetime]]:
        cache_key = f"latest_tokens_{limit}_{min_liquidity}"

        if cache_key in token_cache:
            cached_data = token_cache[cache_key]
            logger.info(f"Returning cached data for {cache_key}")
            return cached_data["tokens"], True, cached_data["cached_at"]

        enriched_tokens = await self._fetch_from_latest_boosted(limit, min_liquidity)

        if len(enriched_tokens) < limit:
            pairs_tokens = await self._fetch_from_token_profiles(limit - len(enriched_tokens), min_liquidity)
            enriched_tokens.extend(pairs_tokens)

        token_cache[cache_key] = {
            "tokens": enriched_tokens,
            "cached_at": datetime.now(timezone.utc)
        }

        logger.info(f"Final result: {len(enriched_tokens)} tokens with liquidity >= ${min_liquidity}")
        return enriched_tokens, False, None

    async def _fetch_from_latest_boosted(self, limit: int, min_liquidity: float) -> list[dict]:
        url = f"{self.BASE_URL}/token-boosts/latest/v1"
        logger.info(f"Fetching latest boosted tokens: {url}")

        response = await self.client.get(url)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch boosted tokens: {response.status_code}")
            return []

        all_tokens = response.json()
        solana_tokens = [t for t in all_tokens if t.get("chainId") == "solana"]
        logger.info(f"Found {len(solana_tokens)} Solana tokens from token-boosts")

        return await self._enrich_tokens(solana_tokens, limit, min_liquidity)

    async def _fetch_from_token_profiles(self, limit: int, min_liquidity: float) -> list[dict]:
        if limit <= 0:
            return []

        url = f"{self.BASE_URL}/token-profiles/latest/v1"
        logger.info(f"Fetching latest token profiles: {url}")

        response = await self.client.get(url)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch token profiles: {response.status_code}")
            return []

        all_tokens = response.json()
        solana_tokens = [t for t in all_tokens if t.get("chainId") == "solana"]
        logger.info(f"Found {len(solana_tokens)} Solana tokens from token-profiles")

        return await self._enrich_tokens(solana_tokens, limit, min_liquidity)

    async def _enrich_tokens(self, tokens: list[dict], limit: int, min_liquidity: float) -> list[dict]:
        enriched_tokens = []
        processed = 0

        for token in tokens[:min(limit * 5, 100)]:
            token_address = token.get("tokenAddress")
            if not token_address:
                continue

            processed += 1

            pair_data = await self.get_token_pairs(token_address)
            if not pair_data:
                logger.info(f"No pairs found for token {token_address[:8]}...")
                continue

            best_pair = self._select_best_pair(pair_data, min_liquidity)
            if not best_pair:
                logger.info(f"No pair with liquidity >= ${min_liquidity} for {token_address[:8]}...")
                continue

            enriched = self._enrich_token_data(token, best_pair)
            if enriched:
                enriched_tokens.append(enriched)
                logger.info(f"Added token: {enriched['symbol']} - ${enriched['liquidity_usd']:.0f} liquidity")

            if len(enriched_tokens) >= limit:
                break

            if processed % 10 == 0:
                await asyncio.sleep(0.5)

        return enriched_tokens

    async def get_token_pairs(self, token_address: str) -> Optional[list[dict]]:
        url = f"{self.BASE_URL}/tokens/v1/solana/{token_address}"

        for attempt in range(3):
            response = await self.client.get(url)

            if response.status_code == 429:
                wait_time = 2 ** attempt
                logger.warning(f"Rate limited on pairs fetch, waiting {wait_time}s")
                await asyncio.sleep(wait_time)
                continue

            if response.status_code == 404:
                return None

            if response.status_code != 200:
                logger.warning(f"Failed to fetch pairs: {response.status_code}")
                return None

            data = response.json()
            return data if isinstance(data, list) else None

        return None

    def _select_best_pair(self, pairs: list[dict], min_liquidity: float) -> Optional[dict]:
        valid_pairs = []
        for pair in pairs:
            liquidity = pair.get("liquidity", {})
            usd_liquidity = liquidity.get("usd", 0) if isinstance(liquidity, dict) else 0

            if usd_liquidity >= min_liquidity:
                valid_pairs.append((pair, usd_liquidity))

        if not valid_pairs:
            return None

        valid_pairs.sort(key=lambda x: x[1], reverse=True)
        return valid_pairs[0][0]

    def _enrich_token_data(self, token_profile: dict, pair: dict) -> Optional[dict]:
        token_address = token_profile.get("tokenAddress") or pair.get("baseToken", {}).get("address")
        if not token_address:
            return None

        base_token = pair.get("baseToken", {})
        liquidity = pair.get("liquidity", {})
        price_usd = pair.get("priceUsd")
        fdv = pair.get("fdv")
        volume = pair.get("volume", {})

        pair_created_at = pair.get("pairCreatedAt")
        created_datetime = None
        age_minutes = 0

        if pair_created_at:
            created_datetime = datetime.fromtimestamp(pair_created_at / 1000, tz=timezone.utc)
            age_delta = datetime.now(timezone.utc) - created_datetime
            age_minutes = int(age_delta.total_seconds() / 60)

        dex_id = pair.get("dexId", "unknown")
        pair_address = pair.get("pairAddress", "")

        return {
            "name": base_token.get("name") or token_profile.get("name", "Unknown"),
            "symbol": base_token.get("symbol") or token_profile.get("symbol", "???"),
            "contract_address": token_address,
            "created_at": created_datetime,
            "age_minutes": age_minutes,
            "liquidity_usd": liquidity.get("usd", 0) if isinstance(liquidity, dict) else 0,
            "price_usd": float(price_usd) if price_usd else None,
            "market_cap_usd": fdv,
            "volume_24h_usd": volume.get("h24", 0) if isinstance(volume, dict) else 0,
            "dex": dex_id,
            "pair_address": pair_address,
            "pair_url": f"https://dexscreener.com/solana/{pair_address}"
        }

    async def close(self):
        await self.client.aclose()


dex_screener_client = DexScreenerClient()
