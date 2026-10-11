import random
import time

from django.conf import settings

from apps.notifications.models import NotificationType

# Simulated send: 50-300 ms, about a 10% transient failure rate, and a path
# that sleeps past the caller's timeout.


class ProviderError(Exception):
    """Transient failure: the send may succeed if retried."""


class ProviderTimeout(ProviderError):
    """The provider did not answer within the allotted time."""


class ProviderPermanentError(Exception):
    """Non-retriable failure (invalid recipient, blocked content, ...)."""


class SendResult:
    def __init__(self, provider_message_id, latency_seconds):
        self.provider_message_id = provider_message_id
        self.latency_seconds = latency_seconds


class SimulatedProvider:
    def __init__(self, channel, rng=None):
        self.channel = channel
        self._rng = rng or random.Random()

    def send(self, *, user_id, message, timeout):
        cfg = settings.NOTIFICATIONS
        latency = (
            self._rng.uniform(cfg["SIMULATED_MIN_LATENCY_MS"], cfg["SIMULATED_MAX_LATENCY_MS"])
            / 1000.0
        )
        if latency > timeout:
            time.sleep(timeout)
            raise ProviderTimeout(f"{self.channel} provider exceeded {timeout:.2f}s")

        time.sleep(latency)
        if self._rng.random() < cfg["SIMULATED_FAILURE_RATE"]:
            raise ProviderError(f"{self.channel} provider returned a transient error")
        return SendResult(
            provider_message_id=f"{self.channel}-{user_id}-{int(time.time() * 1000)}",
            latency_seconds=latency,
        )


_PROVIDERS = {
    NotificationType.SMS: SimulatedProvider(NotificationType.SMS),
    NotificationType.EMAIL: SimulatedProvider(NotificationType.EMAIL),
    NotificationType.PUSH: SimulatedProvider(NotificationType.PUSH),
}


def get_provider(channel):
    try:
        return _PROVIDERS[channel]
    except KeyError as exc:
        raise ProviderPermanentError(f"no provider registered for channel {channel!r}") from exc


def register_provider(channel, provider):
    _PROVIDERS[channel] = provider
