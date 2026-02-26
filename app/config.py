import os
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "Solana Meme Coin Detector API"
    version: str = "1.0.0"
    debug: bool = False

    helius_api_key: str = ""
    helius_rpc_url: str = "https://mainnet.helius-rpc.com"
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"

    dex_screener_base_url: str = "https://api.dexscreener.com"

    cache_ttl_seconds: int = 60
    max_tokens_limit: int = 50
    default_tokens_limit: int = 10
    default_min_liquidity: float = 1000.0

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
