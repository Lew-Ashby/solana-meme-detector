import httpx
import logging
import base64
import asyncio
from typing import Optional
from cachetools import TTLCache

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

metadata_cache = TTLCache(maxsize=500, ttl=300)


class HeliusClient:
    def __init__(self):
        self.api_key = settings.helius_api_key
        self.rpc_url = f"{settings.helius_rpc_url}/?api-key={self.api_key}" if self.api_key else settings.solana_rpc_url
        self.client = httpx.AsyncClient(timeout=30.0)

    async def get_token_metadata(self, mint_address: str) -> dict:
        cache_key = f"metadata_{mint_address}"
        if cache_key in metadata_cache:
            logger.debug(f"Returning cached metadata for {mint_address}")
            return metadata_cache[cache_key]

        result = {
            "mint_authority_enabled": True,
            "freeze_authority_enabled": True,
            "decimals": 9,
            "supply": 0
        }

        account_info = await self._get_account_info(mint_address)
        if account_info:
            parsed = self._parse_mint_account(account_info)
            result.update(parsed)

        metadata_cache[cache_key] = result
        logger.info(f"Token {mint_address}: mint_auth={result['mint_authority_enabled']}, freeze_auth={result['freeze_authority_enabled']}")

        return result

    async def _get_account_info(self, address: str) -> Optional[dict]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [
                address,
                {"encoding": "base64"}
            ]
        }

        logger.debug(f"Fetching account info for {address}")

        for attempt in range(3):
            response = await self.client.post(self.rpc_url, json=payload)

            if response.status_code == 429:
                wait_time = 2 ** attempt
                logger.warning(f"Rate limited on getAccountInfo, waiting {wait_time}s")
                await asyncio.sleep(wait_time)
                continue

            if response.status_code != 200:
                logger.error(f"HTTP error {response.status_code} for {address}")
                return None

            data = response.json()

            if "error" in data:
                logger.error(f"RPC error for {address}: {data['error']}")
                return None

            result = data.get("result")
            if not result or not result.get("value"):
                logger.warning(f"No account data found for {address}")
                return None

            return result["value"]

        logger.error(f"Failed to get account info after 3 attempts: {address}")
        return None

    def _parse_mint_account(self, account_info: dict) -> dict:
        result = {
            "mint_authority_enabled": True,
            "freeze_authority_enabled": True,
            "decimals": 9,
            "supply": 0
        }

        data = account_info.get("data")
        if not data or not isinstance(data, list) or len(data) < 1:
            return result

        raw_data = base64.b64decode(data[0])

        if len(raw_data) < 82:
            logger.warning(f"Mint account data too short: {len(raw_data)} bytes")
            return result

        mint_authority_option = raw_data[0]
        result["mint_authority_enabled"] = mint_authority_option == 1

        if len(raw_data) >= 36:
            supply_bytes = raw_data[36:44]
            result["supply"] = int.from_bytes(supply_bytes, byteorder='little')

        if len(raw_data) >= 45:
            result["decimals"] = raw_data[44]

        if len(raw_data) >= 50:
            freeze_authority_option = raw_data[46]
            result["freeze_authority_enabled"] = freeze_authority_option == 1

        return result

    async def close(self):
        await self.client.aclose()


helius_client = HeliusClient()
