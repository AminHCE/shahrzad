from apps.notifications.utils.provider import ProviderError, SendResult


class ScriptedProvider:
    """``True`` means success. An exception instance is raised."""

    def __init__(self, channel, outcomes=None, hook=None):
        self.channel = channel
        self.outcomes = list(outcomes or [])
        self.calls = []
        self.hook = hook

    def send(self, *, user_id, message, timeout):
        self.calls.append({"user_id": user_id, "message": message, "timeout": timeout})
        if self.hook:
            self.hook(user_id, message)
        outcome = self.outcomes.pop(0) if self.outcomes else True
        if isinstance(outcome, BaseException):
            raise outcome
        return SendResult(provider_message_id="fake", latency_seconds=0.0)


class AlwaysFailProvider(ScriptedProvider):
    def send(self, **kwargs):
        self.calls.append(kwargs)
        raise ProviderError("boom")
