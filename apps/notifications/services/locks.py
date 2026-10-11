import uuid
from dataclasses import dataclass

from django.conf import settings

from apps.common.redis import get_redis

# Keys are hash-tagged on the user id so they stay together in Redis Cluster.
# Release checks the list length in the same script, so an id pushed between
# "queue empty" and "release" is not left without a worker.

KEY_PREFIX = "notify:u"


def queue_key(user_id):
    return f"{KEY_PREFIX}:{{{user_id}}}:q"


def lock_key(user_id):
    return f"{KEY_PREFIX}:{{{user_id}}}:lock"


def retry_key(user_id):
    return f"{KEY_PREFIX}:{{{user_id}}}:retry"


def new_token():
    return uuid.uuid4().hex


# RPUSH id, then try to take the lock. Returns 1 if the caller must start a drain task.
_ENQUEUE = """
redis.call('RPUSH', KEYS[1], ARGV[1])
if redis.call('SET', KEYS[2], ARGV[2], 'NX', 'EX', ARGV[3]) then
  return 1
end
return 0
"""

# Take the lock if free, or confirm we still hold it (and refresh the lease).
_ACQUIRE_OR_REFRESH = """
local cur = redis.call('GET', KEYS[1])
if cur == false then
  redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
  return 1
end
if cur == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""

# Refresh the lease only if we still own it.
_REFRESH = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
  return 1
end
return 0
"""

#  1 -> released
#  0 -> queue not empty, keep going
# -1 -> lock lost
_RELEASE_IF_EMPTY = """
if redis.call('GET', KEYS[2]) ~= ARGV[1] then
  return -1
end
if redis.call('LLEN', KEYS[1]) > 0 then
  return 0
end
redis.call('DEL', KEYS[2])
return 1
"""

# Unconditional release by owner (used when we deliberately step away for backoff).
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  redis.call('DEL', KEYS[1])
  return 1
end
return 0
"""

# Pop the head only if it is the id we just finished (guards against double-pop after a retry).
_POP_IF_HEAD = """
local head = redis.call('LINDEX', KEYS[1], 0)
if head == ARGV[1] then
  redis.call('LPOP', KEYS[1])
  return 1
end
return 0
"""

# Replace the queue contents atomically (sweeper rebuild from the database).
_REBUILD = """
redis.call('DEL', KEYS[1])
if #ARGV > 0 then
  redis.call('RPUSH', KEYS[1], unpack(ARGV))
end
return #ARGV
"""

# Push an id to the head if it is not already present anywhere in the list.
_PUSH_HEAD_IF_MISSING = """
if redis.call('LPOS', KEYS[1], ARGV[1]) ~= false then
  return 0
end
redis.call('LPUSH', KEYS[1], ARGV[1])
return 1
"""

# Insert missing ARGV ids in order. An id already queued stays put. A missing
# id goes before the first later member, or at the tail.
_MERGE_MISSING = """
local pos = {}
for i = 1, #ARGV do
  pos[ARGV[i]] = i
end
local inserted = 0
for i = 1, #ARGV do
  local id = ARGV[i]
  if redis.call('LPOS', KEYS[1], id) == false then
    local len = redis.call('LLEN', KEYS[1])
    local placed = false
    for j = 0, len - 1 do
      local cur = redis.call('LINDEX', KEYS[1], j)
      local curpos = pos[cur]
      if curpos ~= nil and curpos > i then
        redis.call('LINSERT', KEYS[1], 'BEFORE', cur, id)
        placed = true
        break
      end
    end
    if not placed then
      redis.call('RPUSH', KEYS[1], id)
    end
    inserted = inserted + 1
  end
end
return inserted
"""


@dataclass(frozen=True)
class EnqueueResult:
    acquired_lock: bool
    token: str


class UserQueue:
    def __init__(self, client=None):
        self._client = client
        self._scripts_client = None
        self._scripts = {}

    @property
    def client(self):
        return self._client or get_redis()

    def _script(self, name, body):
        client = self.client
        if self._scripts_client is not client:
            self._scripts = {}
            self._scripts_client = client
        if name not in self._scripts:
            self._scripts[name] = client.register_script(body)
        return self._scripts[name]

    @property
    def lock_ttl(self):
        return int(settings.NOTIFICATIONS["USER_LOCK_TTL_SECONDS"])

    def enqueue(self, user_id, notification_id):
        token = new_token()
        acquired = self._script("enqueue", _ENQUEUE)(
            keys=[queue_key(user_id), lock_key(user_id)],
            args=[str(notification_id), token, self.lock_ttl],
        )
        return EnqueueResult(acquired_lock=bool(acquired), token=token)

    def acquire_or_refresh(self, user_id, token):
        return bool(
            self._script("acquire_or_refresh", _ACQUIRE_OR_REFRESH)(
                keys=[lock_key(user_id)], args=[token, self.lock_ttl]
            )
        )

    def try_acquire(self, user_id):
        token = new_token()
        if self.client.set(lock_key(user_id), token, nx=True, ex=self.lock_ttl):
            return token
        return None

    def refresh(self, user_id, token):
        return bool(
            self._script("refresh", _REFRESH)(keys=[lock_key(user_id)], args=[token, self.lock_ttl])
        )

    def peek(self, user_id):
        head = self.client.lindex(queue_key(user_id), 0)
        return None if head is None else str(head)

    def pop_if_head(self, user_id, notification_id):
        return bool(
            self._script("pop_if_head", _POP_IF_HEAD)(
                keys=[queue_key(user_id)], args=[str(notification_id)]
            )
        )

    def release_if_empty(self, user_id, token):
        return int(
            self._script("release_if_empty", _RELEASE_IF_EMPTY)(
                keys=[queue_key(user_id), lock_key(user_id)], args=[token]
            )
        )

    def release(self, user_id, token):
        return bool(self._script("release", _RELEASE)(keys=[lock_key(user_id)], args=[token]))

    def claim_retry_slot(self, user_id, delay_seconds):
        """Allow a single delayed re-drain per user per backoff window."""
        ttl_ms = max(int(delay_seconds * 1000), 1)
        return bool(self.client.set(retry_key(user_id), "1", nx=True, px=ttl_ms))

    def clear_retry_slot(self, user_id):
        self.client.delete(retry_key(user_id))

    def rebuild(self, user_id, notification_ids):
        return int(
            self._script("rebuild", _REBUILD)(
                keys=[queue_key(user_id)],
                args=[str(notification_id) for notification_id in notification_ids],
            )
        )

    def push_head_if_missing(self, user_id, notification_id):
        return bool(
            self._script("push_head_if_missing", _PUSH_HEAD_IF_MISSING)(
                keys=[queue_key(user_id)], args=[str(notification_id)]
            )
        )

    def merge_missing(self, user_id, notification_ids):
        """Insert absent ids, keeping the order of ``notification_ids``."""
        if not notification_ids:
            return 0
        return int(
            self._script("merge_missing", _MERGE_MISSING)(
                keys=[queue_key(user_id)],
                args=[str(notification_id) for notification_id in notification_ids],
            )
        )

    def length(self, user_id):
        return int(self.client.llen(queue_key(user_id)))

    def is_locked(self, user_id):
        return bool(self.client.exists(lock_key(user_id)))


user_queue = UserQueue()
