FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV QUEUECTL_DATABASE_URL=sqlite:////data/queuectl.db

WORKDIR /app

COPY pyproject.toml README.md ./
COPY queuectl ./queuectl

RUN pip install --no-cache-dir .

RUN mkdir -p /data
VOLUME ["/data"]

ENTRYPOINT ["queuectl"]

