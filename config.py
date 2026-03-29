from functools import lru_cache
from typing import List
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    mongo_uri: str = os.getenv("MONGO_URI", "mongodb://localhost:27017/expense_db")
    jwt_secret: str = os.getenv("JWT_SECRET", "change-me-in-production-min-32-chars!!")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_expire_minutes: int = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))
    stripe_secret_key: str = os.getenv("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    exchange_api_key: str = os.getenv("EXCHANGE_API_KEY", "")
    restcountries_api: str = os.getenv("RESTCOUNTRIES_API", "https://restcountries.com/v3.1/all?fields=name,currencies")
    upload_dir: str = os.getenv("UPLOAD_DIR", "uploads")
    max_file_size_mb: int = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
    allowed_file_types: str = os.getenv("ALLOWED_FILE_TYPES", "image/jpeg,image/png,image/webp,application/pdf")
    rate_limit_requests: int = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
    rate_limit_window: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

    @property
    def allowed_file_types_list(self) -> List[str]:
        return self.allowed_file_types.split(",")

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
