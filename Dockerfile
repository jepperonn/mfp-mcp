# Base image pinned by tag and digest, so a build today and in six months uses the same image.
# Dependabot proposes updates monthly.
FROM python:3.15.0-slim-trixie@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONPATH=/app/src DATA_DIR=/data PORT=8000

WORKDIR /app
# Exact, hash-checked dependency versions (regenerate with: uv lock && uv export --frozen --no-dev --no-emit-project --no-header -o requirements.lock)
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock && useradd --system --uid 1000 --home-dir /app mfp

# The app itself runs from source (PYTHONPATH), so no build backend is downloaded at build time.
COPY LICENSE ./
COPY src ./src
COPY --chmod=755 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "mfp_mcp.server"]
