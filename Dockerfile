# One shared image for all three long-running/one-shot processes (migrate,
# web, sync worker) -- same package, different `command:` per Compose
# service, so there's no reason to build/maintain two images.
#
# Multi-stage build following uv's own recommended Docker pattern: copy the
# uv binary from its official distroless image, install dependencies before
# copying application source (so a source-only change doesn't invalidate the
# dependency layer), then install the project itself.
FROM ghcr.io/astral-sh/uv:0.5 AS uv

FROM python:3.12-slim
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

# Dependencies first, without the project itself, so this layer only
# rebuilds when pyproject.toml/uv.lock change, not on every source edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# Now the project itself.
COPY src/ ./src/
COPY README.md ./
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

# Overridden per-service in docker-compose.yml (migrate/web/sync-worker all
# share this image); this default is the most useful single-container
# behavior if someone runs the image directly.
CMD ["threadbare-web"]
