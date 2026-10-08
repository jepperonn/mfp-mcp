FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 DATA_DIR=/data PORT=8000

WORKDIR /app
# Exact, hash-checked dependency versions (regenerate with: uv lock && uv export --frozen --no-dev --no-emit-project --no-header -o requirements.lock)
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-deps . && useradd --system --uid 1000 --home-dir /app mfp

COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "mfp_mcp.server"]
