from apps.notifications.services.locks import UserQueue, lock_key, queue_key, retry_key


def test_enqueue_first_acquires_lock_subsequent_do_not(redis_client):
    q = UserQueue()
    first = q.enqueue(1, 100)
    second = q.enqueue(1, 101)
    third = q.enqueue(1, 102)

    assert first.acquired_lock is True
    assert second.acquired_lock is False and third.acquired_lock is False
    assert redis_client.lrange(queue_key(1), 0, -1) == ["100", "101", "102"]
    assert redis_client.get(lock_key(1)) == first.token
    assert redis_client.ttl(lock_key(1)) > 0


def test_queues_are_isolated_per_user(redis_client):
    q = UserQueue()
    assert q.enqueue(1, 1).acquired_lock
    assert q.enqueue(2, 2).acquired_lock
    assert q.peek(1) == "1" and q.peek(2) == "2"


def test_release_if_empty_semantics(redis_client):
    q = UserQueue()
    res = q.enqueue(5, 50)
    token = res.token

    assert q.release_if_empty(5, token) == 0  # queue not empty
    assert q.pop_if_head(5, 50) is True
    assert q.pop_if_head(5, 50) is False  # already popped
    assert q.release_if_empty(5, "wrong-token") == -1
    assert q.release_if_empty(5, token) == 1
    assert redis_client.exists(lock_key(5)) == 0


def test_acquire_or_refresh_and_refresh(redis_client):
    q = UserQueue()
    assert q.acquire_or_refresh(9, "tok-a") is True  # free -> acquired
    assert q.acquire_or_refresh(9, "tok-a") is True  # owner -> refreshed
    assert q.acquire_or_refresh(9, "tok-b") is False  # held by someone else
    assert q.refresh(9, "tok-b") is False
    assert q.refresh(9, "tok-a") is True
    assert q.release(9, "tok-b") is False
    assert q.release(9, "tok-a") is True


def test_try_acquire_returns_token_only_when_free(redis_client):
    q = UserQueue()
    tok = q.try_acquire(3)
    assert tok is not None
    assert q.try_acquire(3) is None
    q.release(3, tok)
    assert q.try_acquire(3) is not None


def test_rebuild_and_push_head_if_missing(redis_client):
    q = UserQueue()
    q.enqueue(4, 1)
    q.enqueue(4, 2)
    assert q.rebuild(4, [10, 11, 12]) == 3
    assert redis_client.lrange(queue_key(4), 0, -1) == ["10", "11", "12"]
    assert q.push_head_if_missing(4, 11) is False
    assert q.push_head_if_missing(4, 9) is True
    assert q.peek(4) == "9"
    assert q.length(4) == 4


def test_merge_missing_keeps_supplied_order(redis_client):
    q = UserQueue()
    q.rebuild(8, [10, 30])
    assert q.merge_missing(8, [10, 30]) == 0
    assert q.merge_missing(8, [5, 10, 20, 30, 40]) == 3
    assert redis_client.lrange(queue_key(8), 0, -1) == ["5", "10", "20", "30", "40"]


def test_merge_missing_leaves_inflight_head_in_place(redis_client):
    q = UserQueue()
    q.rebuild(8, [99, 30])
    assert q.merge_missing(8, [10, 30]) == 1
    assert redis_client.lrange(queue_key(8), 0, -1) == ["99", "10", "30"]


def test_retry_slot_is_exclusive_until_expiry(redis_client):
    q = UserQueue()
    assert q.claim_retry_slot(1, 5) is True
    assert q.claim_retry_slot(1, 5) is False
    assert redis_client.pttl(retry_key(1)) > 0
