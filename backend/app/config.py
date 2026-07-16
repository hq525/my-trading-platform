from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PT_", env_file=".env")

    db_path: str = "paper_trading.db"
    password: str = "change-me"
    secret_key: str = "dev-secret-change-me"
    alpaca_key_id: str = ""
    alpaca_secret: str = ""
    alpaca_trading_key_id: str = ""
    alpaca_trading_secret: str = ""
    alpaca_trading_base: str = "https://paper-api.alpaca.markets"
    alpaca_options_feed: str = "indicative"
    alpaca_contracts_base: str = "https://paper-api.alpaca.markets"
    starting_cash: Decimal = Decimal("100000")
