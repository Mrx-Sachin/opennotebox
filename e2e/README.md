# Browser tests (no agent)

A Playwright suite that logs in as a dummy account, opens a tiny fixture
book, and walks the reader/notes/link paths a person would hit.

It is meant to run on a machine that already has the stack up. It does
not start Docker itself.

## Dummy account

| | |
| --- | --- |
| Username | `e2e.dummy` |
| Password | `E2eDummy123!` |
| Book | `e2e-sample.pdf` (2 pages, one URI link) |

`seed.py` creates the user, uploads the book if missing, waits for parse,
and plants two notes (a short green highlight and a long indigo one).

You can also sign in as this user in a normal browser.

## Run

```bash
docker compose up -d          # if the stack is not already running
./e2e/run.sh
```

First run installs Playwright and Chromium into `e2e/node_modules`.
Later runs just seed (idempotent) and test.

Useful flags:

```bash
./e2e/run.sh --headed         # watch the browser
./e2e/run.sh tests/notes.spec.ts
```

Override the dummy account or URLs with env vars: `E2E_USER`,
`E2E_PASSWORD`, `E2E_BASE_URL`, `E2E_API_URL`.

## What it covers

- Bad password is rejected
- Dummy account can sign in and sign out
- Sample PDF text layer renders
- Hovering a PDF link paints the indigo preview and shows the URL
- A plain click follows the link (Ctrl is not required)
- Note cards clip long quotes
- A long highlight paints more than the title span
- Selecting text opens a clipped floating bar
- Desktop does not show selection handles
- On a phone viewport, drag handles grow a long-pressed selection

These are the things that have already slipped because there was no
viewer test at all. They are not a full product suite.

## Not in CI yet

GitHub Actions does not boot the full Docker stack (API, worker, Traefik,
Postgres). Run this locally before merging reader/notes/PDF changes.
