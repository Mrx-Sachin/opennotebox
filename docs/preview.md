# Running preview

The new version runs at http://localhost:3013/dashboard. Sign in using the
existing account credentials. The original application remains at
http://localhost:3003.

The preview uses its own database, Redis, JWT secret, cloned documents, and
streaming OmniRoute bridge. Changes made in the preview do not sync back to
the original installation. Accounts and files were copied on 2026-10-05. TTS
reuses the existing engine through the `opennotebox-preview-tts` Docker network.
The team Shared library is enabled here. The API reaches the Gemini model
through the host bridge on port 8767.

Run these from the repository directory:

```sh
# Start or restart the existing preview.
docker compose --env-file /home/mrx/.local/state/opennotebox/preview/preview.env \
  -f docker-compose.preview.yml up -d --no-build

# Stop the preview, keeping its database and documents.
docker compose --env-file /home/mrx/.local/state/opennotebox/preview/preview.env \
  -f docker-compose.preview.yml stop
```

Private preview configuration and copied files are stored under
`/home/mrx/.local/state/opennotebox/preview`. PostgreSQL and Redis use separate
`opennotebox-preview` Docker volumes. The preview gateway publishes only port
3013 on loopback; use an SSH tunnel for access from another computer.

If the original TTS container is recreated, reconnect it to the shared
network so preview narration can reach it:

```sh
docker network connect --alias tts opennotebox-preview-tts "$(docker compose ps -q tts)"
```

## Notebook homepage

The homepage groups documents into notebook folders. Create a notebook for a
topic or project, open it, and use Add existing documents to select related
PDFs. Uploads started inside a notebook go directly into that notebook.
Existing documents begin in Unfiled. All documents remains available as a
separate gallery. Renaming a notebook preserves its contents; deleting it
moves its documents to Unfiled and keeps their files, notes, and reading progress.
Grouping is manual; automatic similarity classification is not implemented.

The Shared library page shows PDFs explicitly published by signed-in team
members. Search by title or topic, filter by document type, preview a page,
select the card, and add it to a notebook. Unsharing hides the catalog entry;
people who already added that asset keep it in their own library. Notes,
annotations, chat and reading progress remain personal.

## Reusable browser QA

The private `opennotebox.qa` account is reused for live checks. Its credentials
stay in `~/.local/state/opennotebox/preview/testing/account.json` with mode 600.
Prepare the same account and small PDF fixtures, then run the opt-in check:

```sh
/home/mrx/.local/venv/bin/python e2e/prepare-preview-qa.py
cd e2e
E2E_BASE_URL=http://localhost:3013 E2E_LIVE_QA=1 npx playwright test tests/notebooks.live.spec.ts --project=chromium
E2E_BASE_URL=http://localhost:3013 E2E_LIVE_QA=1 npx playwright test tests/shared-library.live.spec.ts --project=chromium
```

These fixtures and QA notebooks belong to that account. The test does not
create a new account each run or alter another user's library.
