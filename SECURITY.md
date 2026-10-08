# Security

This server holds a login to your MyFitnessPal account, so security problems matter even for a small project.

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private vulnerability reporting
(the **Security** tab of this repository → *Report a vulnerability*). You'll get an answer within a week.

## What the server protects, and how

- **Access:** everything is behind the passcode (OAuth login for Claude, and the `/setup` page), with rate limiting on wrong guesses.
- **Stored data:**
  - MyFitnessPal cookies are encrypted at rest with a key derived from the `SECRET_KEY` secret.
  - OAuth access/refresh tokens and authorization codes are stored only as SHA-256 hashes.
- **Requests:** only requests for the app's own host name are accepted (DNS-rebinding protection).

## What you are responsible for

- Keep the passcode long and private. Anyone with the passcode and the URL can read and change your diary.
- Run one machine only and keep `SECRET_KEY` secret.
