# Security

Your server holds a login to your MyFitnessPal account. Security problems matter, even in a small project.

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private vulnerability reporting: the **Security** tab of this repository → [**Report a vulnerability**](https://github.com/jepperonn/mfp-mcp/security/advisories/new). You'll get an answer within a week.

**Never include real secrets** in a report, issue or log excerpt: no MyFitnessPal cookies, passcodes, `SECRET_KEY` values or Claude tokens. Replace them with `<redacted>`. Also leave out your server URL if you can.

## If you leaked a secret

- **MyFitnessPal cookies:** log out of myfitnesspal.com in the browser you copied them from. That ends the session. Then log in again and paste a fresh cookie header on your server's `/setup` page.
- **Passcode:** run `./setup.sh --new-passcode`, then remove and re-add the connector in Claude.
- **`SECRET_KEY`:** set a new one with `openssl rand -base64 32 | sed 's/^/SECRET_KEY=/' | fly secrets import`. Stored cookies can no longer be decrypted after that, so paste them again on `/setup`.

## What the server protects, and how

- **Access:** everything is behind the passcode: the OAuth login for Claude and the `/setup` page. After 5 wrong passcodes, that address is blocked for 10 minutes.
- **Stored data:**
  - MyFitnessPal cookies are encrypted at rest (Fernet) with a key derived from the `SECRET_KEY` secret.
  - OAuth access/refresh tokens and authorization codes are stored only as SHA-256 hashes.
- **Requests:** only requests for the app's own host name are accepted (DNS-rebinding protection).
- **Logs:** cookies, passcodes and tokens are never logged.

## What you are responsible for

- Keep the passcode private. Anyone with the passcode and your URL can read and change your diary.
- Run your own server for your own account only. Don't share it.
- Run exactly one machine, and keep `SECRET_KEY` secret.
