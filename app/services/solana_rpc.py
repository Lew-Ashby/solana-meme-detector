import httpx
import logging
import asyncio
from typing import Optional
from cachetools import TTLCache

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

holder_cache = TTLCache(maxsize=200, ttl=120)


class SolanaRPCClient:
    RAYDIUM_AUTHORITY = "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1"
    ORCA_AUTHORITY = "9W959DqEETiGZocYWCQPaJ6sBmUzgfxXfqGeTEdp3aQP"

    KNOWN_LP_PROGRAMS = [
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
        "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",
        "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
        "LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo",
    ]

    BURN_ADDRESSES = [
        "1nc1nerator11111111111111111111111111111111",
        "11111111111111111111111111111111",
    ]

    def __init__(self):
        api_key = settings.helius_api_key
        if api_key:
            self.rpc_url = f"{settings.helius_rpc_url}/?api-key={api_key}"
        else:
            self.rpc_url = settings.solana_rpc_url
        self.client = httpx.AsyncClient(timeout=30.0)

    async def get_holder_distribution(self, mint_address: str) -> dict:
        cache_key = f"holders_{mint_address}"
        if cache_key in holder_cache:
            logger.debug(f"Returning cached holder data for {mint_address}")
            return holder_cache[cache_key]

        result = {
            "total_holders": 0,
            "top_10_percent": 50.0,
            "lp_locked_percent": 0.0,
            "holder_data": []
        }

        largest_accounts = await self._get_token_largest_accounts(mint_address)
        if not largest_accounts:
            logger.warning(f"No holder data found for {mint_address}, using defaults")
            holder_cache[cache_key] = result
            return result

        supply_info = await self._get_token_supply(mint_address)
        total_supply = 0
        if supply_info:
            amount_str = supply_info.get("amount", "0")
            total_supply = int(amount_str) if amount_str else 0

        if total_supply == 0:
            for acc in largest_accounts:
                amount_str = acc.get("amount", "0")
                total_supply += int(amount_str) if amount_str else 0

        if total_supply == 0:
            holder_cache[cache_key] = result
            return result

        top_10_amount = 0
        lp_locked_amount = 0
        holder_data = []

        for i, account in enumerate(largest_accounts[:10]):
            amount_str = account.get("amount", "0")
            amount = int(amount_str) if amount_str else 0
            address = account.get("address", "")
            percentage = (amount / total_supply) * 100 if total_supply > 0 else 0

            is_lp = self._is_likely_lp_or_locked(address)

            holder_data.append({
                "rank": i + 1,
                "address": address,
                "amount": amount,
                "percentage": round(percentage, 2),
                "is_lp_or_locked": is_lp
            })

            if is_lp:
                lp_locked_amount += amount
            else:
                top_10_amount += amount

        top_10_percent = (top_10_amount / total_supply) * 100 if total_supply > 0 else 0
        lp_locked_percent = (lp_locked_amount / total_supply) * 100 if total_supply > 0 else 0

        result = {
            "total_holders": len(largest_accounts),
            "top_10_percent": round(top_10_percent, 2),
            "lp_locked_percent": round(lp_locked_percent, 2),
            "holder_data": holder_data
        }

        holder_cache[cache_key] = result
        logger.info(f"Token {mint_address}: top_10={result['top_10_percent']}%, lp_locked={result['lp_locked_percent']}%")

        return result

    async def _get_token_largest_accounts(self, mint_address: str) -> list[dict]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint_address]
        }

        for attempt in range(3):
            response = await self.client.post(self.rpc_url, json=payload)

            if response.status_code == 429:
                wait_time = 2 ** attempt
                logger.warning(f"Rate limited on getTokenLargestAccounts, waiting {wait_time}s (attempt {attempt + 1}/3)")
                await asyncio.sleep(wait_time)
                continue

            if response.status_code != 200:
                logger.error(f"RPC error {response.status_code} for getTokenLargestAccounts: {mint_address}")
                return []

            data = response.json()

            if "error" in data:
                logger.error(f"RPC error: {data['error']}")
                return []

            return data.get("result", {}).get("value", [])

        logger.error(f"Failed to get largest accounts after 3 attempts: {mint_address}")
        return []

    async def _get_token_supply(self, mint_address: str) -> Optional[dict]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenSupply",
            "params": [mint_address]
        }

        for attempt in range(3):
            response = await self.client.post(self.rpc_url, json=payload)

            if response.status_code == 429:
                wait_time = 2 ** attempt
                logger.warning(f"Rate limited on getTokenSupply, waiting {wait_time}s")
                await asyncio.sleep(wait_time)
                continue

            if response.status_code != 200:
                return None

            data = response.json()

            if "error" in data:
                return None

            return data.get("result", {}).get("value", {})

        return None

    def _is_likely_lp_or_locked(self, token_account_address: str) -> bool:
        if token_account_address in self.BURN_ADDRESSES:
            return True

        if self.RAYDIUM_AUTHORITY in token_account_address:
            return True
        if self.ORCA_AUTHORITY in token_account_address:
            return True

        for lp_program in self.KNOWN_LP_PROGRAMS:
            if lp_program in token_account_address:
                return True

        return False

    async def close(self):
        await self.client.aclose()


solana_rpc_client = SolanaRPCClient()
