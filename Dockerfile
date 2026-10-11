FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.5 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install "poetry==${POETRY_VERSION}"

WORKDIR /app

COPY pyproject.toml poetry.lock* ./
# Official wheel host (files.pythonhosted.org) is sometimes unresolvable on
# restricted networks. Fall back to a mirror that rehosts the same files.
# Adding a source changes pyproject.toml, so the lock hash has to be refreshed.
RUN poetry install --no-root --with dev \
    || { poetry source add --priority=default mirror https://mirror-pypi.runflare.com/simple/ \
         && poetry lock --no-update \
         && poetry install --no-root --with dev; }

COPY . .

RUN addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--config", "gunicorn.conf.py"]
