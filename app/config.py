import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    admin_telegram_ids: str = os.getenv("ADMIN_TELEGRAM_IDS", "")
    database_path: str = os.getenv("DATABASE_PATH", "./data/payout_manager.db")
    currency: str = os.getenv("CURRENCY", "USD")
    max_retries: int = int(os.getenv("MAX_RETRIES", "3"))
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    @property
    def admin_ids(self):
        return {
            int(x.strip())
            for x in self.admin_telegram_ids.split(",")
            if x.strip()
        }


settings = Settings()
