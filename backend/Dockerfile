FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Deps first for layer caching.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

EXPOSE 8000

# The Helm Deployment runs `alembic upgrade head` in an init container before
# this starts; the CronJob overrides the command with `python -m app.cli sync`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
