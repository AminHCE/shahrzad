"""Gunicorn configuration.

Threads are used (rather than pure sync workers) because the request path is dominated by
I/O: a single INSERT plus a Redis round-trip. Multiple processes guard against GIL contention
and give isolation; threads keep memory low for the 1000 rps target.
"""

import multiprocessing
import os

bind = "0.0.0.0:8000"
workers = int(os.getenv("WEB_CONCURRENCY", max(2, multiprocessing.cpu_count())))
threads = int(os.getenv("GUNICORN_THREADS", "4"))
worker_class = "gthread"
worker_tmp_dir = "/dev/shm"
max_requests = 5000
max_requests_jitter = 500
timeout = 30
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
