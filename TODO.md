# Voice concurrency audit and remaining work

Branch: `feat/voice-concurrency`
Worktree: `/home/mrx/.local/state/deepread/worktrees/voice-concurrency`
Preview: http://127.0.0.1:3030
Test account: `demo` / `demo123`

Original plan: `/home/mrx/local/github/meta_deepread/.agents/plans/2026-10-29-narration/plan.md`

The earlier checklist overstated completion. A one-account burst benchmark is
not evidence for sustained narration by several independent accounts.

## Implemented, subject to audit checks

- [x] Preserve useful numeric content in retrieval indexing.
- [x] Reuse Piper models and move the main synthesis call off the API event loop.
- [x] Accept the `release_all` compatibility spelling at the release endpoint.
- [x] Exercise audio claim/release with disposable real Redis.
- [x] Fix packaging, cooperative Piper cancellation, timeout and sentence pauses.
- [x] Isolate the voice model bridge on host port 8768.
- [x] Verify nine real speech requests, WAV content, cache rows and file cleanup.
- [x] Add and exercise the project verification skill against the existing preview.

## Release gates still open

- [ ] Fixed corpus covering clean PDF, scan, mixed layout and financial tables.
- [ ] Real upload/index/chat verification for numeric facts and citations.
- [ ] Bounded admission with playback/prefetch/background priority and aging.
- [ ] Cancellation of queued work and evidence for abandoned synthesis behavior.
- [ ] Three independent accounts continuously listening to different documents.
- [ ] Mixed workload: narration, upload, background audio, seeks and tab close.
- [ ] Five listeners as an exploratory test, not a supported-capacity promise.
- [ ] Measure memory recovery, retained model memory and parsing peak memory.
- [ ] Verify generated audio speed, intelligibility and sentence boundary pauses.
- [ ] Repeated, alternating before/after measurements with workload validation.
- [ ] Verify OOM/model recycle and queue/worker failure behavior.

## Evidence

Updated audit report: `/home/mrx/local/github/meta_deepread/temp/voice-concurrency-proof.html`.
Historical JSON in `benchmarks/results_baseline.json` and
`benchmarks/results_phase3_inmemory.json` measures same-account request bursts.
Its ratios are preliminary observations, not a verified multi-user speedup.
Compilation and isolated unit tests do not close the release gates above.
