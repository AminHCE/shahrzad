import random

import pytest

from apps.notifications.utils.provider import (
    ProviderError,
    ProviderTimeout,
    SimulatedProvider,
)


def test_simulated_provider_latency_and_failure_rate(notification_settings):
    notification_settings["SIMULATED_MIN_LATENCY_MS"] = 0
    notification_settings["SIMULATED_MAX_LATENCY_MS"] = 1
    notification_settings["SIMULATED_FAILURE_RATE"] = 0.10
    provider = SimulatedProvider("sms", rng=random.Random(42))

    failures = 0
    for _ in range(500):
        try:
            provider.send(user_id=1, message="x", timeout=5.0)
        except ProviderError:
            failures += 1
    assert 0.05 < failures / 500 < 0.16


def test_simulated_provider_times_out(notification_settings):
    notification_settings["SIMULATED_MIN_LATENCY_MS"] = 50
    notification_settings["SIMULATED_MAX_LATENCY_MS"] = 60
    provider = SimulatedProvider("sms", rng=random.Random(1))
    with pytest.raises(ProviderTimeout):
        provider.send(user_id=1, message="x", timeout=0.01)
