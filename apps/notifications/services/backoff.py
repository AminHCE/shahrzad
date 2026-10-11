import random

from django.conf import settings


def retry_delay(attempt):
    """Exponential backoff with full jitter: U(0, min(max, base * 2**(attempt-1)))."""
    cfg = settings.NOTIFICATIONS
    base = float(cfg["RETRY_BASE_DELAY_SECONDS"])
    cap = float(cfg["RETRY_MAX_DELAY_SECONDS"])
    if base <= 0 or cap <= 0:
        return 0.0
    upper = min(cap, base * (2 ** max(attempt - 1, 0)))
    return random.uniform(0, upper)
