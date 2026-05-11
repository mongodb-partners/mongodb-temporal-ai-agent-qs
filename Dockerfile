# syntax=docker/dockerfile:1.6
FROM python:3.13-slim

# Install system build deps for any source-only wheels.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Install uv. Pin a version so image builds are reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.8.2 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local

WORKDIR /app

# Resolve dependencies from the lockfile alone before copying the rest of the
# source so the dependency layer is cacheable. README is needed because
# pyproject.toml's [project].readme references it during metadata resolution.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the application; the project is run as a set of modules
# (uvicorn api.main:app, python -m temporal.run_worker, streamlit run app.py)
# so we don't need to install the project itself into site-packages. Modules
# resolve from the working directory at runtime.
COPY . .

# Run as non-root.
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Default entrypoint — overridden per-service in docker-compose.yml.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
