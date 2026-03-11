FROM python:3.12-slim as base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8000 \
    PATH="/home/appuser/.local/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends sudo \
    && rm -rf /var/lib/apt/lists/*

ARG APP_USER=appuser
ARG APP_UID=10001
ARG APP_GID=10001

RUN groupadd --gid "${APP_GID}" "${APP_USER}" \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/bash "${APP_USER}" \
    && echo "${APP_USER} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/${APP_USER}" \
    && chmod 0440 "/etc/sudoers.d/${APP_USER}"

WORKDIR /app

COPY pyproject.toml poetry.lock ./
RUN pip install --no-cache-dir "claude-agent-sdk>=0.1.44,<0.2.0"

COPY src ./src
COPY README.md ./
COPY entrypoint.sh ./
RUN chmod +x /app/entrypoint.sh
RUN chown -R "${APP_USER}:${APP_USER}" /app

EXPOSE 8000

USER ${APP_USER}

CMD ["./entrypoint.sh"]

FROM base as deploy

USER root
ARG APP_USER=appuser

RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm curl wget git \
    && pip install --no-cache-dir uv \
    && rm -rf /var/lib/apt/lists/*

USER ${APP_USER}
RUN uv tool install git+https://github.com/tolatolatop/bbs-cli.git
