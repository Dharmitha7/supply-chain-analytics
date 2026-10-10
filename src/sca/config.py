import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

RAW_DIR = Path(os.getenv("SCA_RAW_DIR", ROOT / "data" / "raw"))
SQL_DIR = ROOT / "sql"


def db_url() -> str:
    g = os.environ.get
    return (
        f"postgresql+psycopg://{g('POSTGRES_USER')}:{g('POSTGRES_PASSWORD')}"
        f"@{g('POSTGRES_HOST', 'localhost')}:{g('POSTGRES_PORT', '5432')}/{g('POSTGRES_DB')}"
    )
