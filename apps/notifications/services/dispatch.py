def schedule_drain(user_id, token, countdown=0):
    from apps.notifications.tasks import drain_user_queue

    # Celery's JSON serializer cannot encode a UUID instance.
    kwargs = {"user_id": str(user_id), "token": token}
    if countdown > 0:
        drain_user_queue.apply_async(kwargs=kwargs, countdown=countdown)
    else:
        drain_user_queue.apply_async(kwargs=kwargs)
