# Contributing

Thanks for helping! This is a small hobby project, so keep changes focused.

- **Bugs and ideas:** open an [issue](https://github.com/jepperonn/mfp-mcp/issues/new/choose). **Never paste cookies, passcodes, tokens or your server URL**. Replace them with `<redacted>`.
- **Security problems:** see [SECURITY.md](SECURITY.md). Don't open a public issue.
- **MyFitnessPal changed something?** Run the live smoke test (below). The first failing step shows which part changed. Update [docs/how-mfp-works.md](docs/how-mfp-works.md) with what you find.

## Development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy src && pytest
```

The normal tests use a fake MyFitnessPal and need no account. The live smoke test is opt-in. It writes only to the diary date 2001-01-01 and removes what it created:

```bash
MFP_LIVE_COOKIES=~/mfp-cookie.txt pytest tests/live -v   # the file holds your browser's cookie header
```

Keep that cookie file outside the repository. It is git-ignored if named `cookie*.txt`, but don't rely on that.

**Dependencies** are pinned with hashes in `requirements.lock`, which the Docker image uses. After changing `pyproject.toml`, run:

```bash
uv lock && uv export --frozen --no-dev --no-emit-project --no-header -o requirements.lock
```

CI runs lint, type checks, tests, the lockfile check and a Docker build on every pull request.
