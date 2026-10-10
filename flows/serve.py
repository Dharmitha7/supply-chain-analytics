"""Long-running process for the Docker worker: registers daily_refresh with a 06:00 cron schedule."""
from pipeline import daily_refresh

if __name__ == "__main__":
    daily_refresh.serve(name="daily-refresh-0600", cron="0 6 * * *", tags=["supply-chain"])
