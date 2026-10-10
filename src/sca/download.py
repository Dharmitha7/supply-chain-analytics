"""Download the Olist dataset from Kaggle into data/raw (needs a free Kaggle account + API token).

    python -m sca.download

Token: https://www.kaggle.com/settings -> "Create New Token", save as ~/.kaggle/kaggle.json (never commit it).
The data cannot be redistributed, which is why it is fetched rather than stored in the repo.
"""
import subprocess
import sys

from .config import RAW_DIR

DATASET = "olistbr/brazilian-ecommerce"


def run() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "kaggle", "datasets", "download", "-d", DATASET, "-p", str(RAW_DIR), "--unzip"]
    subprocess.run(cmd, check=True)
    print(f"Downloaded {DATASET} into {RAW_DIR}")


if __name__ == "__main__":
    run()
