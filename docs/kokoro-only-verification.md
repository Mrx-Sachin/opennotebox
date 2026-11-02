# Kokoro-only speech verification

Worktree: `/home/mrx/.t3/worktrees/meta_deepread/t3-e968282f`.

Piper has been removed from the application configuration, voice catalog, speech implementation, and Docker dependency and model downloads. Kokoro is the only speech engine. The available voices are Heart, Bella, Michael, Fenrir, Emma, and George.

Old saved voice preferences are updated to Kokoro Heart when read. Valid Kokoro choices are preserved. New requests for removed or unknown voices return HTTP 422. Previously generated audio and live model volumes are untouched.

## Settings and backend proof

- [Settings screenshot](../_scratch/kokoro-only/settings-kokoro-only.png) shows all six Kokoro voices and the saved Michael selection.
- [Actual settings/API results](../_scratch/kokoro-only/settings-and-backend-proof.json) record migration of an old Ryan preference to Heart, saving Michael through the real settings UI, and retaining Michael after reload. Eighteen requests selecting removed or unknown voices were rejected across the settings, text playback, and streaming endpoints.
- [Source removal check](../_scratch/kokoro-only/source-removal-proof.json) confirms the old engine files are absent, configuration uses `tts_url`, and application source and Docker dependency declarations contain no Piper references. Historical podcast transcript parsing still recognizes the old speaker name Ryan; it cannot select or invoke that voice.
- [Baseline before removal](../_scratch/kokoro-only/before-removal.json) shows that the previous backend exposed Piper voices and accepted a Ryan preference.

The settings screenshot uses the production frontend build and the actual auth, settings, and voice catalog routes against a disposable SQLite database. Unrelated document gallery requests use fixtures. No production database was modified.

## Actual speech

- [Generated speech](../_scratch/kokoro-only/kokoro-only-speech.wav).
- [Audio validation](../_scratch/kokoro-only/real-audio-proof.json) confirms HTTP 200, mono PCM WAV at 24 kHz, nonzero audio samples, and rejection of removed voices at the speech service.
- [Running service inventory](../_scratch/kokoro-only/running-service-proof.json) shows no importable Piper package or installed Piper distribution, six Kokoro voices, and only the Kokoro model and voice embeddings.
- [Clean image build](../_scratch/kokoro-only/tts-image-build.log) passed for `deepread-kokoro-only:candidate`. Actual speech and dependency checks ran from this image with [no mounted source or models](../_scratch/kokoro-only/running-service-mounts.json). The [image ID](../_scratch/kokoro-only/candidate-image-id.txt) records the exact artifact tested.
- [Service source checksums](../_scratch/kokoro-only/running-service-source.sha256) match the worktree source to the code executed inside the test container.

Speech inference was tested on CPU in an isolated container. GPU behavior and serving capacity were not measured.

## Regression checks

- [Full backend suite](../_scratch/kokoro-only/all-backend-tests.log): 524 passed, 2 skipped.
- [Speech HTTP contract tests](../_scratch/kokoro-only/tts-tests.log): 5 passed, including invalid voices, cancellation, timeout, and number normalization.
- [Browser regressions](../_scratch/kokoro-only/browser-regressions.log): 47 passed, covering settings, continuous reading, audio cleanup, and podcast playback. These regression tests use API fixtures; the separate settings proof above uses real settings routes.
- [Production frontend build](../_scratch/kokoro-only/frontend-build.log): passed, including lint and TypeScript checks.
- [Project-configured Python lint](../_scratch/kokoro-only/project-lint.log): passed. `git diff --check` passed.
- CI now includes the Kokoro HTTP contract and settings voice browser tests.

Existing warnings remain outside this change: backend Docling deprecations and SQLite expression-index reflection, speech-service FastAPI startup/shutdown deprecations, and settings CLI discovery requests blocked by the existing Content Security Policy. The tested voice settings operations passed.

## Rerun the checks

From the worktree:

```bash
cd backend
/home/mrx/.local/venv/bin/python -m pytest -q
cd ..
/home/mrx/.local/venv/bin/python -m pytest -q tts/test_kokoro_endpoint.py
/home/mrx/.local/venv/bin/python _scratch/kokoro-only/verify_removal.py
cd frontend
npm run build
```

The additional scripts under `_scratch/kokoro-only` preserve the isolated API, browser, audio, and dependency checks. Start `serve_verification_api.py` with the specified interpreter to seed a fresh disposable database before running `verify_settings.mjs`. The browser proof expects the built frontend at port 3135 and the isolated API at port 8135. Audio verification expects the speech container at port 5135.

Build and run the speech artifact with:

```bash
docker build -t deepread-kokoro-only:candidate tts
docker run --rm --name deepread-kokoro-only-proof --cpus=2 --memory=3g --memory-swap=3g -p 127.0.0.1:5135:5111 -e TTS_MAX_CONCURRENCY=1 -e TTS_IDLE_RELEASE_SECONDS=0 -e OMP_NUM_THREADS=2 deepread-kokoro-only:candidate
```

After the service is ready, run `/home/mrx/.local/venv/bin/python _scratch/kokoro-only/verify_audio.py` in a second terminal. This check uses CPU inference and does not request GPU access.

This worktree has not been deployed. The original checkout and existing installations remain unchanged.
