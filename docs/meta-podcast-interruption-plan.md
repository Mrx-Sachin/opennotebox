# Meta-powered podcast questions

Status: proposed integration plan, 2026-11-01. Implementation has not started.

## Goal and scope

While listening to a generated podcast, the listener selects "Ask hosts", speaks or types a question, hears the same two voices explain it using the source document, and continues the original recording from the saved playback position. "I don't understand that" must work when the current exchange is known.

Assumptions, in priority order:

1. The hackathon permits hosted Meta APIs and third-party TTS. Confirm its actual rules before choosing the submission stack. Using a Meta model alone does not establish eligibility.
2. We will have API access to Muse Voice Transcribe and a Meta text model. Account access, regional availability, credits, and quotas must be tested before implementation depends on them.
3. The first release uses an explicit mic button and also accepts text questions. Continuous listening and automatic interruption are deferred.

Use Muse Voice Transcribe for speech recognition, an available Muse Spark text model for answering, and the existing Kokoro voices for speech output. Meta's current model catalogue lists `muse-spark-1.3`; verify access with the actual account before selecting it. Muse Voice Transcribe produces text, not spoken answers. This is an open-source application using hosted Meta inference, with local TTS.

Earlier responses overstated certainty. The quoted total latency, zero additional RAM, and guaranteed hackathon compliance were not verified. This plan replaces those claims with checks and measurements. Google's internal implementation has not been established by the available public sources.

## Existing foundations

- `backend/app/tasks/podcast_tasks.py` generates structured A/B dialogue, synthesizes each line, and stitches WAV clips with silence. It saves the transcript but no per-line audio timestamps. Voices currently map to `kokoro-am_michael` and `kokoro-af_bella`.
- `backend/app/models/__init__.py` already stores podcast transcript JSON. Add timing metadata there without introducing a new table for the initial feature.
- `frontend/src/services/ttsEngine.ts` owns a singleton audio player. The reader store and dashboard player preserve podcast playback across navigation. Interruption playback must use this owner.
- `backend/app/services/llm_settings.py` already resolves user-selected OpenAI-compatible backends. Add Meta through the existing backend registry.
- `backend/app/services/retrieval.py`, `citations.py`, and `library_access.py` provide document search, source provenance, and authorization boundaries. Reuse these utilities for podcast answers.
- `backend/app/services/llm_semaphore.py` distinguishes foreground requests from background generation. Questions belong to foreground admission.

## Delivery sequence

### 1. Prove Meta access and choose the model

Run one real question recording through `POST https://api.meta.ai/v1/asr/transcribe` using `muse-voice-transcribe-1.0` in `PUSH_TO_TALK` mode. Run one short dialogue prompt through the selected Meta text model using `https://api.meta.ai/v1`.

Keep the API key server-side. Add the text model as an admin-configured backend, then select it for the hackathon demo account. The existing active-backend setting also affects that account's other LLM features; do not change every user's selection. Ensure podcast generation and answers both use the selected Meta backend, with no silent fallback to the current Gemini configuration.

Give speech recognition separate server configuration, such as `META_ASR_MODEL`, `META_API_KEY`, and an enable flag. Pass the secret only to services that need it. Document the exact successful model IDs and account limits, without recording secrets.

Exit condition: real transcription and schema-valid text generation succeed. If either is unavailable, record that blocker before building dependent functionality.

### 2. Record the episode's actual timeline

At synthesis/stitch time, calculate each line's start and end from decoded WAV frame counts and the actual silence between clips. Save a transcript version, line timing metadata, and host voice IDs in the existing JSON. Validate compatible sample formats before calculating timing or stitching audio.

Use timings to identify the current exchange and correct transcript click-to-seek. Include recent exchanges in the question context. Keep the next exchange only as a return cue, never as evidence for the answer.

Existing episodes retain their original audio. Their player can save and restore an exact playback timestamp without knowing the corresponding transcript line. For contextual questions, allow the listener to select the relevant exchange, or ask a specific question against the source document. Do not present the current equal-line-duration approximation as exact alignment. Automatic alignment of old episodes is deferred.

Do not infer exact old-episode boundaries from silence alone. Speech clips can contain the same 300 ms digital silence that stitching inserts, making joins ambiguous. A synthetic two-clip check produced two indistinguishable silence runs for one actual join. Character-weighted timing is also an estimate and does not replace a known exchange for an ambiguous question.

Exit condition: line boundaries match the generated WAV, including silence; old episodes still play without regeneration.

### 3. Deliver typed questions through the whole pipeline

Add an authenticated `POST /api/podcasts/{id}/interrupt` taking a text question, playback position, and an optional selected exchange for older episodes. Resolve episode ownership and current library access on the server. Derive the document scope from the episode rather than trusting a client-supplied document ID.

Use a focused `podcast_interaction.py` service to:

1. Resolve the current exchange and bounded recent dialogue. For an ambiguous question such as "explain that", use this dialogue to form the retrieval query. If no exchange is known, ask for a selection instead of inventing the topic.
2. Search source passages within the accessible source document and the episode's section range when applicable. Reuse retrieval and citation helpers; the generated podcast transcript supplies conversational context, not factual evidence.
3. Call the selected Meta text model under foreground admission. Ask for a concise answer in a few A/B turns, with source references returned separately from the spoken text. Return an honest lack-of-evidence answer when needed.
4. Validate speakers, nonempty text, length limits, and references against the supplied source IDs. Treat source text as data. Keep a small bounded Q&A history for follow-up questions.
5. Synthesize the short dialogue with the saved host voices through the existing TTS service. Use async HTTP from the async request path; do not call the blocking podcast task helper directly or inherit its silent text truncation.

Return the answer transcript, page citations, and an authenticated temporary audio URL. Keep temporary ownership metadata server-side with an expiry. Release answer files on completion/cancellation, plus an expiry sweep for abandoned sessions. Reuse suitable audio cleanup utilities without marking the original episode audio as temporary.

Place the new prompt in `prompts/podcast/interrupt.jinja`; test both normal template loading and the deployment's fallback behavior. Do not rewrite unrelated prompt templates.

Exit condition: type a question in the preview, hear both hosts answer with source links, and resume the original episode from the saved position.

### 4. Add Muse microphone input

Add "Record" and "Done" controls plus an editable transcription field. Pause the episode before opening the microphone so its voices are not transcribed as the question. Stop every media track when recording ends or is cancelled. Microphone capture requires HTTPS or localhost and a granted browser permission.

For the first version, upload a short recording through an authenticated application endpoint such as `POST /api/podcasts/{id}/transcribe`. The backend forwards it to Muse and returns the transcript. The listener can correct names or technical terms and submit through the same text-question endpoint.

Meta's file endpoint accepts RIFF/WAVE, mono, 16-bit PCM at 16 or 24 kHz. Browser `MediaRecorder` output is commonly WebM/Opus or MP4/AAC. Capture, resample, and encode the required WAV explicitly in the browser; check the actual sample rate, channel count, and encoding on the backend. Renaming a browser recording to `.wav` is insufficient.

Start with a 30-second recording cap, a matching upload cap, bounded provider timeouts, and clear empty-audio, quota, and transcription errors. These are product limits, independent of Meta's higher API limits. A denied microphone permission leaves text input available. Explain that voice and relevant source excerpts are sent to Meta.

Exit condition: real microphone audio reaches Muse, the corrected transcript reaches the answer service, and the complete pause/answer/resume flow works.

### 5. Make playback transitions reliable

Extend `ttsEngine.ts` and the reader store with explicit interaction states: capturing, transcribing, answering, playing the answer, and returning to the episode. The engine stores the original episode identity, exact time, playback rate, volume, mute setting, and whether it was playing.

Retain the original audio source while playing the temporary answer through the same audio owner. The normal answer `ended` event must restore the saved episode rather than execute the ordinary end-of-episode stop path. Resume automatically only if the episode was playing before the interruption. A listener can always select "Return to podcast"; a failed request leaves the episode paused with its position intact.

Add an engine-owned interruption operation rather than calling the current `playAudioUrl(answerUrl)` directly. That method resets playback time, clears the active/expanded podcast for a different URL, and revokes the previous blob URL. Preserve the original episode's blob URL until the return operation completes, wait for metadata before restoring its position, and release only the superseded answer URL. Reusing a retained blob avoids another episode download but does not guarantee retention of the browser's decoded media buffer.

Handle rejected `audio.play()` promises on answer playback and resumption. If browser autoplay policy blocks either operation, retain the current state and provide a visible tap-to-play control. Verify this behavior on target phone browsers.

Use the existing generation/cancellation mechanism for async work. Switching episodes, starting narration, stopping playback, logging out, or deleting the active episode invalidates the pending answer. A late response cannot play or restart an old episode. Explicit seek actions during an interaction must cancel the return operation before seeking.

Keep interaction state above the modal lifecycle. Navigation or closing the studio must not silently discard the return position or restart audio; the stable bottom player must provide access to the active interaction. Avoid keyboard shortcuts stealing spaces or arrows while the question field is focused. Test phone layout, visible recording status, focus, and screen-reader labels.

Exit condition: no overlapping playback, no stale resumption, and existing podcast/narration navigation behavior still passes.

### 6. Verify the real flow and measure overhead

Use an isolated preview and the existing `demo` QA account. Add focused backend tests and extend the existing podcast Playwright tests. Use provider mocks for deterministic failure/race tests, then separately run at least one complete real Meta transcription and answer through real Kokoro playback.

Required scenarios:

- Interrupt in the middle of a line. Confirm the original audio pauses immediately, the correct recent dialogue is used, the answer plays, and the saved position is restored without skipping the remainder of the line.
- Ask "I don't understand that", then a follow-up. Inspect retrieved source passages and rendered citations, and listen for consistent host voices.
- Exercise an old episode without timestamps, a section episode, a silent recording, denied mic permission, provider timeout/429, malformed model output, and TTS failure.
- Cancel during each pending stage; switch episode, start narration, navigate, log out, and delete the episode while an answer is in flight. Confirm late responses cannot restart it and microphone tracks and temporary audio are released.
- Verify another account cannot transcribe against, question, or fetch answer audio for the episode. Recheck library revocation before delivery of a long-running answer.
- Run desktop and phone interaction tests plus the existing podcast playback/deletion and reading-audio lifecycle regressions. Verify actual media time progression and restored time, not only a play icon.

Instrument transcription, retrieval, each LLM call, TTS, download, and time from question submission to audible answer. Include cold and warm TTS, an active background generation job, and repeated sessions. Report observed latency distribution, peak process/container RAM, GPU memory, and temporary storage growth with hardware and concurrency recorded. Treat provider benchmarks as separate from our app's end-to-end performance.

Exit condition: reproducible verification evidence and measured overhead from the real integration. Compilation alone does not meet this condition.

## Resource expectations and release boundary

Hosted Muse transcription and hosted text inference require no local model-weight allocation. There is still additional RAM for capture, uploads, HTTP requests, retrieval, audio buffers, and concurrent sessions. Kokoro synthesis consumes the deployment's existing CPU/GPU capacity and can queue behind narration or episode generation. Check that contention before increasing worker counts or adding services.

For sizing, mono PCM16 at 24 kHz is 48,000 bytes per second. A 30-second question contains about 1.44 MB of PCM payload, excluding the WAV header and transport overhead. Decoded browser audio, intermediate copies, and TTS tensors consume additional memory; payload size is not a peak-RAM estimate.

Meta currently documents Muse transcription at $0.18 per processed audio hour. A 10-second question is $0.0005 for transcription at that rate, before text-model charges. Recheck pricing and hackathon credits before budgeting. Ordinary episode playback makes no transcription or answer-generation requests.

Release behind an enable flag after preview verification. Initial scope includes mic and text input, contextual answers in both voices, citations, cancellation, temporary cleanup, and reliable resumption. Streaming partial transcripts and playing an answer before all lines finish synthesizing are later optimizations, driven by measured delay. If added, relay the WebSocket through our backend, keep the key server-side, pace raw PCM in real time, and handle Muse's documented final-turn events.

## Primary references

- Meta speech API, formats, authentication, modes, limits, and pricing: https://dev.meta.ai/docs/speech-to-text
- Meta model catalogue and availability: https://dev.meta.ai/docs/models
- Meta OpenAI-compatible chat protocol: https://dev.meta.ai/docs/protocols/chat-completions

Documentation was read during planning. Account access, hackathon eligibility, real integration latency, and resource consumption remain implementation checks.
