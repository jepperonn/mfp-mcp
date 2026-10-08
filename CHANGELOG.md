# Changelog

All notable changes to this project are documented here. Versions follow [Semantic Versioning](https://semver.org).

## [1.0.0] - 2026-10-08

First public release.

- Remote MCP server (streamable HTTP, stateless) for Claude's custom connectors.
- OAuth with dynamic client registration and a passcode login. Clients and hashed tokens are stored in SQLite, so logins survive restarts; refresh tokens rotate with a short grace period.
- `/setup` page: paste the browser cookie header once; stored encrypted (Fernet).
- Keep-alive every 6 hours that renews the MyFitnessPal session and saves renewed cookies.
- Tools: `get_day`, `search_food`, `food_info`, `log_food`, `edit_entry`, `delete_entry`, `my_foods_list`, `my_foods_save`, `my_foods_delete`, `status`.
- `setup.sh` for deployment to Fly.io: checks prerequisites, remembers earlier answers (re-run to update), enforces one machine, waits for the health check; `--new-passcode` replaces a lost passcode.
- Reproducible image: base image pinned by digest, dependencies pinned with hashes.
