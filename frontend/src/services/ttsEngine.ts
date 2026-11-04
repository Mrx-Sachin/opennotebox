import {
  getDocument,
  getPage,
  playSummary,
  getTtsCacheStatus,
  getAudioUrl,
  fetchAudioBlobUrl,
  streamTts,
  TtsStreamResult,
  releaseReadingAudio,
  beaconReleaseReadingAudio,
  heartbeatReadingAudio,
  Podcast,
  getPodcastAudioUrl,
} from "./api"
import { sentenceStartTimes, sentenceIndexAt, Sentence } from "@/lib/sentences"
import { dehyphenateForSpeech, buildPageSentences } from "@/lib/narrationFilter"
import { useReader } from "@/stores/reader"

/**
 * Singleton TTS engine with chunked, near-gapless page reading.
 *
 * A page's sentences are grouped into chunks on demand: whenever the user
 * clicks/seeks to a sentence, the chunk list is rebuilt starting exactly at
 * that sentence (see buildChunks) - a small entry chunk first so audio
 * arrives fast, then normal-sized chunks onward. The next chunks (and the
 * following page's first chunk) are synthesized ahead of playback so chunk
 * transitions only cost an <audio> src swap.
 *
 * Sentence highlighting: per-chunk start times are estimated by character
 * weight; the global sentence index = chunk.from + local index.
 */

// First chunk after a click/seek: deliberately small so the first audio
// the user hears arrives fast, rather than waiting out a full-size chunk
// built from a click near its end.
const ENTRY_CHUNK_TARGET_CHARS = 120
const CHUNK_TARGET_CHARS = 320

// Above this, further speed-up is applied as audio.playbackRate instead of
// requesting it from the synthesizer. Matches Kokoro's own hard cap (it
// asserts 0.5-2.0).
// Below this, the full requested speed is synthesized natively.
//
// This matters because playbackRate alone doesn't work at high multiples:
// it only speeds up ALREADY-SYNTHESIZED audio, so at 3x the amount of
// audio consumed per second of synthesis time triples while synthesis
// itself takes exactly as long as it would at 1x - prefetch just falls
// permanently behind and playback stalls waiting on it. Native synthesis
// speed instead makes the synthesizer itself produce less audio for the
// same text (measured on Kokoro: speed=2.0 roughly halves both the
// generated audio's duration AND the wall-clock time to generate it),
// so the chunk stays consumed at close to the same relative rate.
const SPEED_NATIVE_CAP = 2.0

function nativeSpeedFor(requested: number): number {
  return Math.min(Math.max(requested, 0.1), SPEED_NATIVE_CAP)
}

// Sentences longer than this are pre-split into pieces before being folded
// into chunk text (see splitLongSentence). This only changes how SYNTHESIS
// TEXT is assembled - the sentence list itself, and the indices PdfViewer's
// click-to-read depends on matching exactly, are never touched. A long
// sentence still occupies exactly one slot in `sentences`; it just gets
// voiced across more than one chunk.
const SENTENCE_HARD_SPLIT_CHARS = 350
const MIN_FRAGMENT_CHARS = 40
// In priority order: the more natural a pause sounds there, the earlier it
// is tried. Each is applied without its trailing/leading whitespace baked
// into the resulting pieces (trimmed after the cut).
const CLAUSE_SEPARATORS = [/;\s+/, /,\s+/, /\s+(?:and|but)\s+/i]

/** Find the occurrence of `sep` in `text` closest to its midpoint that
 * leaves both halves at least MIN_FRAGMENT_CHARS long. Returns null if no
 * such split exists (e.g. the separator never occurs, or only occurs too
 * close to one end). */
function splitAtBestBoundary(text: string, sep: RegExp): [string, string] | null {
  const re = new RegExp(sep.source, sep.flags.includes("g") ? sep.flags : sep.flags + "g")
  const mid = text.length / 2
  let best: number | null = null
  let match: RegExpExecArray | null
  while ((match = re.exec(text)) !== null) {
    const splitPoint = match.index + match[0].length
    if (splitPoint >= MIN_FRAGMENT_CHARS && text.length - splitPoint >= MIN_FRAGMENT_CHARS) {
      if (best === null || Math.abs(splitPoint - mid) < Math.abs(best - mid)) best = splitPoint
    }
    if (match[0].length === 0) re.lastIndex++ // guard against zero-length matches looping forever
  }
  return best === null ? null : [text.slice(0, best).trim(), text.slice(best).trim()]
}

/** Split one over-long sentence into speakable pieces for synthesis,
 * never mid-word. Tries clause separators in order (semicolon, comma,
 * "and"/"but"); falls back to the nearest whitespace to `max` if no clause
 * boundary exists at all. A 900-char run-on sentence used to become one
 * unsplit chunk the user had to wait out entirely (buildChunks never split
 * within a sentence) - this is what lets it play as several normal-sized
 * chunks instead, while the sentence list PdfViewer indexes against stays
 * exactly as it was. */
function splitLongSentence(text: string, max = SENTENCE_HARD_SPLIT_CHARS): string[] {
  if (text.length <= max) return [text]
  for (const sep of CLAUSE_SEPARATORS) {
    const halves = splitAtBestBoundary(text, sep)
    if (halves) return halves.flatMap((h) => splitLongSentence(h, max))
  }
  // No usable clause boundary: cut at the nearest whitespace to `max`,
  // preferring to look backward (keeps pieces close to the target size)
  // and falling forward only if that would leave too tiny a head. A
  // mid-word cut reads as a stutter, which is worse than a piece somewhat
  // over the target - so this never happens.
  let cut = text.lastIndexOf(" ", max)
  if (cut < MIN_FRAGMENT_CHARS) {
    cut = text.indexOf(" ", max)
    if (cut === -1) return [text] // one unbroken run with no space at all
  }
  const head = text.slice(0, cut).trim()
  const tail = text.slice(cut).trim()
  if (!tail) return [head]
  return [head, ...splitLongSentence(tail, max)]
}

interface Chunk {
  text: string
  from: number // first sentence index (inclusive)
  to: number // last sentence index (exclusive)
  charStart: number // chars before this chunk (for page progress estimates)
  chars: number
}

interface PageSentences {
  sentences: Sentence[]
  totalChars: number
}

interface PageAudio extends PageSentences {
  chunks: Chunk[]
}

interface PreparedChunk {
  blobUrl: string
  duration: number
  nativeSpeed: number
  cacheId: number | null
}

/** One in-flight or completed chunk synthesis. `result` is filled in once
 * the fetch resolves (kept alongside the promise, not just inside it, so
 * maintainPrefetch can synchronously read buffered duration without
 * awaiting) and `controller` lets a superseded generation actually abort
 * the underlying HTTP request instead of just ignoring its result.
 * `pageNum`/`chunkIdx` let the prefetch budget count only audio still
 * ahead of the playhead - counting already-played chunks used to fill the
 * 25s budget and block next-page spill. */
interface PrepEntry {
  promise: Promise<PreparedChunk>
  controller: AbortController
  token: number
  pageNum: number
  chunkIdx: number
  sessionId: string
  result?: PreparedChunk
}

interface ChunkAtom {
  text: string
  sentenceIdx: number
}

function atomsFrom(sentences: Sentence[], startIndex: number, entryMax: number): ChunkAtom[] {
  const atoms: ChunkAtom[] = []
  for (let i = startIndex; i < sentences.length; i++) {
    const max = i === startIndex ? entryMax : SENTENCE_HARD_SPLIT_CHARS
    for (const piece of splitLongSentence(sentences[i].text.trim(), max)) {
      atoms.push({ text: piece, sentenceIdx: i })
    }
  }
  return atoms
}

/** Group a page's sentences (from `startIndex` onward) into chunks for
 * synthesis. `startIndex` is the clicked/sought sentence - text before it
 * is never included, so a click never has to wait out (or skip past) audio
 * for text the user didn't ask to hear. The first chunk targets
 * `entryChars` (small, so first audio arrives fast); every chunk after
 * that targets `restChars`. `from`/`to` index the ORIGINAL sentence list
 * regardless of how splitLongSentence broke up any one sentence's text -
 * that's what keeps highlighting landing on the right sentence even when
 * one sentence spans several chunks. */
function buildChunks(sentences: Sentence[], startIndex: number, entryChars: number, restChars: number): Chunk[] {
  const atoms = atomsFrom(sentences, startIndex, entryChars)
  const chunks: Chunk[] = []
  let from = startIndex
  let to = startIndex
  let text = ""
  let charStart = sentences.slice(0, startIndex).reduce((n, s) => n + s.text.trim().length, 0)
  let target = entryChars
  const flush = () => {
    if (!text) return
    chunks.push({ text, from, to, charStart, chars: text.length })
    charStart += text.length
    text = ""
  }
  for (const atom of atoms) {
    const candidateLen = text ? text.length + atom.text.length + 1 : atom.text.length
    if (text && candidateLen > target) {
      flush()
      from = atom.sentenceIdx
      target = restChars
    }
    text = text ? `${text} ${atom.text}` : atom.text
    to = atom.sentenceIdx + 1
  }
  flush()
  return chunks
}


function splitTextIntoSpeechChunks(text: string): string[] {
  const clean = text
    .replace(/\[\d+(?:\s*,\s*\d+)*\]/g, "")
    .replace(/[*_#`~>]/g, "")
    .replace(/https?:\/\/\S+/g, "")
    .trim()
  if (!clean) return []

  const sentences = clean.match(/[^.!?\n]+[.!?\n]+|[^.!?\n]+$/g) || [clean]
  const chunks: string[] = []
  let current = ""

  for (const s of sentences) {
    const trimmed = s.trim()
    if (!trimmed) continue
    // Entry chunk target is ~120 chars so first audio plays in <0.5s!
    const targetLimit = chunks.length === 0 ? 130 : 280
    if (current.length === 0) {
      current = trimmed
    } else if (current.length + trimmed.length + 1 <= targetLimit) {
      current += " " + trimmed
    } else {
      chunks.push(current)
      current = trimmed
    }
  }
  if (current) chunks.push(current)
  return chunks
}

class TtsEngine {
  private audio: HTMLAudioElement | null = null
  private sessionId =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : "session-" + Date.now() + "-" + Math.random().toString(36).slice(2)

  constructor() {
    if (typeof window !== "undefined") {
      const handleUnload = () => {
        const cacheIds = [...this.prepared.values()]
          .map((entry) => entry.result?.cacheId)
          .filter((cacheId): cacheId is number => cacheId != null)
        beaconReleaseReadingAudio({ sessionId: this.sessionId, cacheIds, allSession: true })
      }
      window.addEventListener("pagehide", handleUnload)
      window.addEventListener("beforeunload", handleUnload)
    }
  }
  private docId: number | null = null
  private page: number | null = null
  private pageAudio: PageAudio | null = null
  private chunkIdx = 0
  private chunkStarts: number[] = [] // sentence start times within current chunk
  private chunkDuration = 0
  /** Native speed the CURRENTLY PLAYING chunk's blob was synthesized at -
   * see setSpeed, which needs this to compute the correct residual
   * playbackRate rather than assuming native == 1x. */
  private currentNativeSpeed = 1
  private playToken = 0
  private spillQueued = false
  /** Explicit voice from Settings (e.g. "kokoro-af_bella"). null means the
   * server uses this user's stored default via get_default_voice(user.id). */
  private voice: string | null = null

  private pageSentencesCache = new Map<string, Promise<PageSentences>>()
  private pageSentencesVersion = 0
  private prepared = new Map<string, PrepEntry>()
  private summaryBlobUrl: string | null = null
  private speakingMessageId: number | string | null = null
  private textPlaybackQueue: {
    chunks: string[]
    currentIdx: number
    token: number
    messageId?: number | string
    prefetches: Map<number, Promise<TtsStreamResult>>
    chunkDurations: Map<number, number>
  } | null = null
  private interruptionState: {
    podcast: Podcast
    savedTime: number
    wasPlaying: boolean
    podcastBlobUrl: string
    answerBlobUrl?: string | null
    onFinished?: () => void
  } | null = null
  private directAudioSource: { kind: "podcast" | "summary"; id: number | string } | null = null

  // Leading+trailing debounce state for click/seek entry points (readPage,
  // seekToSentence). See debounceClick.
  private clickDebounceTimer: ReturnType<typeof setTimeout> | null = null
  private clickDebouncePending: (() => void) | null = null
  private static readonly CLICK_DEBOUNCE_MS = 250
  private heartbeatIntervalTimer: ReturnType<typeof setInterval> | null = null

  private startHeartbeat(): void {
    if (this.heartbeatIntervalTimer) return
    this.heartbeatIntervalTimer = setInterval(() => {
      const currentStatus = useReader.getState().tts.status
      if (currentStatus !== "idle") {
        void heartbeatReadingAudio(this.sessionId).catch(() => {})
      }
    }, 30000)
  }

  private stopHeartbeat(): void {
    if (this.heartbeatIntervalTimer) {
      clearInterval(this.heartbeatIntervalTimer)
      this.heartbeatIntervalTimer = null
    }
  }

  private static readonly PREFETCH_BUFFER_SECONDS = 25
  private static readonly PREFETCH_MAX_INFLIGHT = 3

  // ------------------------------------------------------------------
  // Plumbing

  private ensureAudio(): HTMLAudioElement {
    if (!this.audio) {
      this.audio = new Audio()
      this.audio.addEventListener("timeupdate", () => this.onTimeUpdate())
      this.audio.addEventListener("ended", () => this.onEnded())
    }
    return this.audio
  }

  private set = (patch: Parameters<ReturnType<typeof useReader.getState>["setTts"]>[0]) =>
    useReader.getState().setTts(patch)

  /** Sentences of the page currently loaded in the engine (for highlight rendering). */
  getSentences(docId: number, page: number): Sentence[] {
    return this.docId === docId && this.page === page ? this.pageAudio?.sentences ?? [] : []
  }

  public clearPageSentencesCache(docId?: number, preservePlayback = false): void {
    if (!preservePlayback && (docId == null || this.docId === docId)) {
      this.stop()
    }
    if (docId == null) {
      this.pageSentencesCache.clear()
    } else {
      const prefix = `${docId}:`
      for (const key of Array.from(this.pageSentencesCache.keys())) {
        if (key.startsWith(prefix)) this.pageSentencesCache.delete(key)
      }
    }
    if (preservePlayback && this.docId != null && this.page != null && (docId == null || this.docId === docId)) {
      this.pageSentencesVersion++
      this.spillQueued = false
      for (const [key, entry] of this.prepared) {
        if (entry.pageNum <= this.page) continue
        entry.controller.abort()
        if (entry.result) {
          URL.revokeObjectURL(entry.result.blobUrl)
          if (entry.result.cacheId != null) {
            void releaseReadingAudio({ sessionId: entry.sessionId, cacheIds: [entry.result.cacheId] }).catch(() => {})
          }
        }
        this.prepared.delete(key)
      }
      this.maintainPrefetch(this.docId, this.page, this.chunkIdx, this.playToken)
    }
  }

  private loadPageSentences(docId: number, pageNum: number): Promise<PageSentences> {
    const key = `${docId}:${pageNum}`
    let cached = this.pageSentencesCache.get(key)
    if (!cached) {
      cached = getPage(docId, pageNum)
        .then((p) => {
          // Build sentences using shared buildPageSentences, then dehyphenate
          // for speech. PdfViewer runs the same sentence builder so sentence
          // indices stay aligned.
          const content = p.content || ""
          const sentences = dehyphenateForSpeech(
            buildPageSentences(content, p.narration_exclusions)
          )
          return {
            sentences,
            totalChars: sentences.reduce((n, s) => n + s.text.trim().length, 0) || 1,
          }
        })
        .catch((err) => {
          if (this.pageSentencesCache.get(key) === cached) this.pageSentencesCache.delete(key)
          throw err
        })
      // Evict oldest entries to keep the cache bounded - a long reading
      // session across many documents would otherwise accumulate unbounded
      // page content references in memory.
      if (this.pageSentencesCache.size >= 64) {
        const oldestKey = this.pageSentencesCache.keys().next().value
        if (oldestKey !== undefined) this.pageSentencesCache.delete(oldestKey)
      }
      this.pageSentencesCache.set(key, cached)
    }
    return cached
  }

  /** Bump the play generation and cancel everything left over from the
   * previous one: abort in-flight prepares (this is what stops an orphaned
   * poll/fetch loop from a rapid click burst from ever completing) and
   * revoke any blob URLs they'd already produced. Chunk continuation
   * within the SAME generation (chunk-to-chunk playback, page-advance -
   * see advance()) deliberately does NOT go through this, since those
   * prepares are exactly what we want to keep using. */
  private bumpGeneration(): number {
    this.playToken++
    const token = this.playToken
    this.spillQueued = false
    const previousSessionId = this.sessionId
    this.sessionId =
      typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
        ? crypto.randomUUID()
        : "session-" + Date.now() + "-" + Math.random().toString(36).slice(2)

    for (const [key, entry] of this.prepared) {
      if (entry.token !== token) {
        entry.controller.abort()
        if (entry.result) {
          URL.revokeObjectURL(entry.result.blobUrl)
        }
        this.prepared.delete(key)
      }
    }
    void releaseReadingAudio({ sessionId: previousSessionId, allSession: true }).catch(() => {})
    return token
  }

  /** Leading+trailing debounce for click/seek entry points. The first
   * click in a burst fires immediately - a single click still feels
   * instant - further clicks within ~250ms of each other are coalesced,
   * and only the LAST one fires, ~250ms after the burst goes quiet.
   * Without this, clicking N lines fast used to kick off N full prepare
   * cycles (see prepareChunk) with no way to cancel the abandoned ones,
   * saturating the browser's per-origin connection limit and burying the
   * request for the line the user actually meant to land on. Automatic
   * chunk-to-chunk continuation and page-advance do NOT go through this -
   * they're programmatic, not a user clicking repeatedly, and delaying
   * them would just add latency for no benefit. */
  private debounceClick(action: () => void) {
    if (this.clickDebounceTimer == null) {
      action() // leading edge
      this.clickDebounceTimer = setTimeout(() => this.debounceSettle(), TtsEngine.CLICK_DEBOUNCE_MS)
    } else {
      this.clickDebouncePending = action
      clearTimeout(this.clickDebounceTimer)
      this.clickDebounceTimer = setTimeout(() => this.debounceSettle(), TtsEngine.CLICK_DEBOUNCE_MS)
    }
  }

  private debounceSettle() {
    this.clickDebounceTimer = null
    const pending = this.clickDebouncePending
    this.clickDebouncePending = null
    if (pending) pending() // trailing edge: the last click in the burst
  }

  // ------------------------------------------------------------------
  // Chunk synthesis + prefetch

  /** Resolve one chunk to a playable blob URL via the streaming endpoint.
   *
   * Keyed by (token, docId, page, idx) - the token makes every entry
   * belong to exactly one generation, so a superseded generation's entries
   * are never mistaken for reusable results even if a later click
   * re-chunks the same page starting at the same idx. NOT keyed by speed:
   * a speed change reuses whatever's already synthesized (near-instant,
   * via a playbackRate adjustment - see playChunk/setSpeed) rather than
   * invalidating the cache and forcing a full resynthesis, which
   * previously made every speed tweak feel like a cold start again on top
   * of Kokoro's already-slow per-chunk synthesis. The actual native speed
   * used is stored on the result so playback can always compute the
   * correct residual regardless of when it was prepared relative to the
   * current speed setting. */
  private prepareChunk(docId: number, pageNum: number, chunk: Chunk, idx: number, token: number): Promise<PreparedChunk> {
    const nativeSpeed = nativeSpeedFor(useReader.getState().tts.speed)
    // Voice in the key so a mid-read voice change cannot reuse audio for a
    // different speaker.
    const voiceKey = this.voice || "default"
    const key = `${token}:${docId}:${pageNum}:${idx}:${voiceKey}`
    let entry = this.prepared.get(key)
    if (!entry) {
      const controller = new AbortController()
      const chunkSessionId = this.sessionId
      const newEntry: PrepEntry = {
        controller,
        token,
        pageNum,
        chunkIdx: idx,
        sessionId: chunkSessionId,
        promise: null as unknown as Promise<PreparedChunk>,
      }
      newEntry.promise = streamTts(
        chunk.text,
        this.voice,
        nativeSpeed,
        controller.signal,
        chunkSessionId
      ).then((res) => {
        if (controller.signal.aborted || newEntry.token !== this.playToken) {
          URL.revokeObjectURL(res.blobUrl)
          if (res.cacheId != null) {
            void releaseReadingAudio({ sessionId: chunkSessionId, cacheIds: [res.cacheId] }).catch(() => {})
          }
          throw new Error("Aborted generation")
        }
        const result: PreparedChunk = {
          blobUrl: res.blobUrl,
          duration: res.duration,
          nativeSpeed,
          cacheId: res.cacheId,
        }
        newEntry.result = result
        return result
      })
      newEntry.promise.catch(() => {
        // Allow retry after a genuine failure. An abort also lands here,
        // but bumpGeneration already deleted the key by the time that
        // rejection fires, so this is a harmless no-op for that case.
        if (this.prepared.get(key) === newEntry) this.prepared.delete(key)
      })
      entry = newEntry
      this.prepared.set(key, entry)
    }
    return entry.promise
  }

  /** True if this prepared entry is still ahead of (or at) the playhead. */
  private isAheadOfPlayhead(entry: PrepEntry, pageNum: number, chunkIdx: number): boolean {
    if (entry.pageNum > pageNum) return true
    if (entry.pageNum < pageNum) return false
    return entry.chunkIdx >= chunkIdx
  }

  /** Drop prepared audio already behind the playhead so the prefetch budget
   * only covers what is left to hear. */
  private prunePlayedChunks(pageNum: number, chunkIdx: number, token: number) {
    const finishedBySession = new Map<string, number[]>()
    for (const [key, entry] of this.prepared) {
      if (entry.token !== token) continue
      if (this.isAheadOfPlayhead(entry, pageNum, chunkIdx)) continue
      entry.controller.abort()
      if (entry.result) {
        URL.revokeObjectURL(entry.result.blobUrl)
        if (entry.result.cacheId != null) {
          const list = finishedBySession.get(entry.sessionId) || []
          list.push(entry.result.cacheId)
          finishedBySession.set(entry.sessionId, list)
        }
      }
      this.prepared.delete(key)
    }
    for (const [sessId, cids] of finishedBySession) {
      void releaseReadingAudio({ sessionId: sessId, cacheIds: cids }).catch(() => {})
    }
  }

  /** Keep synthesizing chunks ahead of `chunkIdx`. Buffer counts only audio
   * still ahead of the playhead (not already-played chunks), otherwise the
   * 25s budget fills mid-page and next-page spill never runs. */
  private maintainPrefetch(docId: number, pageNum: number, chunkIdx: number, token: number) {
    if (token !== this.playToken) return
    const pa = this.pageAudio
    if (!pa) return

    this.prunePlayedChunks(pageNum, chunkIdx, token)

    const speed = Math.max(useReader.getState().tts.speed || 1, 0.1)

    let bufferedSeconds = 0
    let inFlight = 0
    for (const entry of this.prepared.values()) {
      if (entry.token !== token) continue
      if (!this.isAheadOfPlayhead(entry, pageNum, chunkIdx)) continue
      // Current chunk is already playing - only count strictly-future audio.
      if (entry.pageNum === pageNum && entry.chunkIdx === chunkIdx) continue
      if (entry.result) bufferedSeconds += (entry.result.duration * entry.result.nativeSpeed) / speed
      else inFlight++
    }

    const voiceKey = this.voice || "default"
    let ahead = chunkIdx + 1
    while (
      ahead < pa.chunks.length &&
      this.prepared.has(`${token}:${docId}:${pageNum}:${ahead}:${voiceKey}`)
    ) {
      ahead++
    }
    const currentPageExhausted = ahead >= pa.chunks.length

    // Next-page spill is latency-critical: do not let a full same-page
    // buffer postpone it once every remaining current-page chunk is queued.
    if (currentPageExhausted) {
      this.spillToNextPage(docId, pageNum, chunkIdx, token)
      return
    }

    if (bufferedSeconds >= TtsEngine.PREFETCH_BUFFER_SECONDS || inFlight >= TtsEngine.PREFETCH_MAX_INFLIGHT) {
      return
    }

    void this.prepareChunk(docId, pageNum, pa.chunks[ahead], ahead, token)
      .then(() => this.maintainPrefetch(docId, pageNum, chunkIdx, token))
      .catch(() => {})
    // Fire another prepare in parallel while under the in-flight cap.
    this.maintainPrefetch(docId, pageNum, chunkIdx, token)
  }

  /** Prefetch the next page's first (entry-sized) chunk so the page turn
   * reuses already-synthesized audio instead of cold-starting. */
  private spillToNextPage(docId: number, pageNum: number, chunkIdx: number, token: number) {
    if (this.spillQueued) return
    const active = useReader.getState().activeReading
    const maxPages = active?.docId === docId ? active.numPages : null
    if (maxPages != null && pageNum >= maxPages) return
    this.spillQueued = true
    const sentencesVersion = this.pageSentencesVersion
    void this.loadPageSentences(docId, pageNum + 1)
      .then((next) => {
        if (token !== this.playToken || sentencesVersion !== this.pageSentencesVersion || next.sentences.length === 0) return
        const nextChunks = buildChunks(next.sentences, 0, ENTRY_CHUNK_TARGET_CHARS, CHUNK_TARGET_CHARS)
        if (nextChunks.length === 0) return
        return this.prepareChunk(docId, pageNum + 1, nextChunks[0], 0, token)
      })
      .then(() => {
        if (sentencesVersion === this.pageSentencesVersion) this.maintainPrefetch(docId, pageNum, chunkIdx, token)
      })
      .catch(() => {
        // Allow a later maintainPrefetch call to retry on failure.
        if (token === this.playToken && sentencesVersion === this.pageSentencesVersion) this.spillQueued = false
      })
  }

  // ------------------------------------------------------------------
  // Page playback

  /** Start reading a page, optionally from a given sentence index. Debounced
   * (see debounceClick): this is the entry point clicking a line goes
   * through, so a burst of clicks costs at most two of these actually
   * running. */
  readPage(docId: number, pageNum: number, fromSentence = 0, sentences?: Sentence[]) {
    this.debounceClick(() => this.readPageNow(docId, pageNum, fromSentence, sentences))
  }

  /** Undebounced readPage - a genuinely new generation (cancels/revokes
   * whatever the previous one had prepared). Used by the debounced public
   * readPage, and by seekToSentence's cross-chunk fallback. */
  private readPageNow(
    docId: number,
    pageNum: number,
    fromSentence: number,
    sentences?: Sentence[],
  ) {
    const token = this.bumpGeneration()
    void this.startPage(docId, pageNum, fromSentence, token, false, sentences)
  }

  private async startPage(
    docId: number,
    pageNum: number,
    fromSentence: number,
    token: number,
    preserveFollow = false,
    sentences?: Sentence[],
  ): Promise<void> {
    const audio = this.ensureAudio()
    audio.pause()
    this.directAudioSource = null
    if (this.summaryBlobUrl) {
      URL.revokeObjectURL(this.summaryBlobUrl)
      this.summaryBlobUrl = null
    }

    this.startHeartbeat()

    this.docId = docId
    this.page = pageNum
    this.pageAudio = null
    this.chunkStarts = []

    const currentState = useReader.getState()
    const provisionalNumPages =
      currentState.selectedDocId === docId && currentState.numPages > 0
        ? currentState.numPages
        : 1
    const provisionalTitle = `Document ${docId}`

    const isSamePage = currentState.activeReading?.docId === docId && currentState.tts.docId === docId && currentState.tts.page === pageNum
    useReader.setState((state) => ({
      activePodcast: null,
      expandedPodcast: null,
      narrationSentences: isSamePage && state.narrationSentences.length > 0 ? state.narrationSentences : [],
      activeReading: preserveFollow && state.activeReading?.docId === docId
        ? state.activeReading
        : {
            docId,
            title: provisionalTitle,
            numPages: provisionalNumPages,
          },
      tts: {
        ...state.tts,
        status: "preparing",
        docId,
        page: pageNum,
        sentenceIndex: fromSentence,
      },
      ...(!preserveFollow && state.selectedDocId === docId ? { followReading: true } : {}),
      ...(state.selectedDocId === docId && (preserveFollow ? state.followReading : true)
        ? { currentPage: pageNum }
        : {}),
    }))

    try {
      if (!preserveFollow) {
        const documentData = await getDocument(docId)
        if (token !== this.playToken) return

        useReader.setState({
          activeReading: {
            docId,
            title: documentData.title || `Document ${docId}`,
            numPages: Math.max(1, documentData.num_pages || 1),
          },
        })
      }

      const active = useReader.getState().activeReading
      const maxPages = active?.docId === docId ? active.numPages : 1
      if (pageNum > maxPages) {
        this.stop()
        return
      }

      const ps = sentences?.length
        ? {
            sentences,
            totalChars: sentences.reduce((totalChars, sentenceItem) => totalChars + sentenceItem.text.trim().length, 0) || 1,
          }
        : await this.loadPageSentences(docId, pageNum)
      if (token !== this.playToken) return
      if (ps.sentences.length === 0) {
        this.advance(docId, pageNum, token)
        return
      }

      useReader.setState({
        narrationSentences: ps.sentences.map((sentenceItem) => sentenceItem.text),
      })

      const clamped = Math.max(0, Math.min(fromSentence, ps.sentences.length - 1))
      // Re-chunk starting exactly at the clicked/sought sentence - never
      // synthesize text before it, and never make the user wait out a
      // chunk built from further back than where they actually clicked.
      const chunks = buildChunks(ps.sentences, clamped, ENTRY_CHUNK_TARGET_CHARS, CHUNK_TARGET_CHARS)

      this.docId = docId
      this.page = pageNum
      this.pageAudio = { sentences: ps.sentences, totalChars: ps.totalChars, chunks }
      // Allow this page to spill into the next (flag was set when the previous
      // page prefetched this page's first chunk).
      this.spillQueued = false

      await this.playChunk(0, token, clamped)
    } catch (err) {
      if (token === this.playToken) {
        console.error("TTS readPage failed:", err)
        this.reportFailure(err)
        this.stop()
      }
    }
  }

  /** Surface a synthesis failure to the user instead of just going quiet.
   *
   * Previously any failure reset status to "idle" and logged to the console,
   * which from the reader's point of view is indistinguishable from the app
   * ignoring the click entirely: the shimmer appears for a few milliseconds
   * and then nothing happens, forever. That is exactly how a GPU
   * out-of-memory fault in the speech service presented, and it made a
   * server-side problem look like broken click handling. */
  private reportFailure(err: unknown) {
    const status = (err as { response?: { status?: number } })?.response?.status
    let message = "Narration failed. Please try again."
    if (status === 502) {
      message = "The speech engine could not synthesize this passage - it may be out of memory. Try again in a moment."
    } else if (status === 401 || status === 403) {
      message = "Your session expired. Please sign in again."
    } else if (status === undefined) {
      message = "Could not reach the speech service. Check that it is running."
    }
    useReader.setState({ citationToast: message })
  }

  /** Load and play one chunk, seeking to startSentence when given. */
  private async playChunk(idx: number, token: number, startSentence?: number): Promise<void> {
    const audio = this.ensureAudio()
    const pa = this.pageAudio
    if (!pa || this.docId == null || this.page == null) return
    const chunk = pa.chunks[idx]

    // The reader is waiting for THIS chunk, so its synthesis must reach the
    // TTS service as the only Kokoro request in flight. The service runs one
    // Kokoro session behind a single-slot gate, and (crucially) all of a
    // click's requests leave the browser within ~2ms of each other, so
    // "send the entry first" is a coin flip: the gate is a non-FIFO
    // threading.Semaphore and whichever waiting thread the OS wakes first
    // grabs the GPU. Firing the prefetches alongside the entry used to let
    // up to three LONGER chunks grab the gate ahead of the short entry,
    // turning a ~0.35s click-to-audio into ~4s. So we send the entry alone,
    // and only start prefetching once its own synthesis has completed.
    //
    // That ordering also gives the prefetches MORE head start, not less:
    // by the time the entry's blob is ready we know its synthesis is done
    // (the gate is free), and the entry plays for ~4s while a following
    // chunk needs only ~1.2s to synthesize - so the prefetches finish long
    // before the entry ends. (The old "prefetch only once playback begins"
    // worry about razor-thin head start does not apply: every chunk here
    // plays ~10x longer than it takes to synthesize.)
    this.set({ status: "preparing" })
    // If this chunk wasn't ready in time, this await genuinely blocks -
    // reflect that in status instead of silently sitting on "playing" while
    // no audio is actually coming out, which reads as the reader being
    // broken rather than just catching up.
    const prep = await this.prepareChunk(this.docId, this.page, chunk, idx, token)
    if (token !== this.playToken) return

    // Entry synthesis is complete and the gate is free, so prefetching the
    // following chunks now is safe - and it runs in parallel with this
    // chunk's playback, which is far longer than their synthesis.
    this.maintainPrefetch(this.docId, this.page, idx, token)

    this.chunkIdx = idx
    this.currentNativeSpeed = prep.nativeSpeed
    audio.src = prep.blobUrl
    // Use the speed this SPECIFIC blob was actually synthesized at (may be
    // stale if the user changed speed after it was prepared) rather than
    // recomputing from the current setting - keeps pacing correct without
    // forcing a resynthesis just because the preference changed.
    const requestedSpeed = useReader.getState().tts.speed
    audio.playbackRate = requestedSpeed / prep.nativeSpeed
    audio.muted = useReader.getState().tts.muted

    const waitMeta = () =>
      audio.readyState >= 1
        ? Promise.resolve()
        : new Promise<void>((resolve, reject) => {
            audio.onloadedmetadata = () => resolve()
            audio.onerror = () => reject(new Error("Audio failed to load"))
          })
    // Local blob URLs usually already have metadata; only wait when they don't.
    if (audio.readyState < 1) await waitMeta()
    if (token !== this.playToken) return

    this.chunkDuration = audio.duration || prep.duration
    const chunkSentences = pa.sentences.slice(chunk.from, chunk.to)
    this.chunkStarts = sentenceStartTimes(chunkSentences, this.chunkDuration)

    // Chunks are now re-built starting exactly at the clicked/sought
    // sentence (see buildChunks), so chunk.from already equals
    // startSentence whenever one was given - this always lands on 0, but
    // is left in place rather than hardcoded so a future caller passing a
    // mid-chunk startSentence still seeks correctly.
    let localStart = 0
    if (startSentence != null && startSentence > chunk.from) {
      localStart = Math.min(startSentence - chunk.from, chunkSentences.length - 1)
      audio.currentTime = this.chunkStarts[localStart] ?? 0
    } else {
      audio.currentTime = 0
    }

    await audio.play()
    if (token !== this.playToken) return

    this.set({
      status: "playing",
      docId: this.docId,
      page: this.page,
      sentenceIndex: chunk.from + localStart,
      sentenceCount: pa.sentences.length,
      duration: this.estimatedPageDuration(),
      currentTime: this.pageElapsed(),
    })
  }

  /** Estimated full-page audio duration, scaled from the current chunk. */
  private estimatedPageDuration(): number {
    const pa = this.pageAudio
    if (!pa || !this.chunkDuration) return 0
    const chunk = pa.chunks[this.chunkIdx]
    return (this.chunkDuration / Math.max(chunk.chars, 1)) * pa.totalChars
  }

  /** Estimated elapsed time within the page (chunked position -> seconds). */
  private pageElapsed(): number {
    const pa = this.pageAudio
    if (!pa || !this.audio || !this.chunkDuration) return 0
    const chunk = pa.chunks[this.chunkIdx]
    const fraction =
      (chunk.charStart + (this.audio.currentTime / this.chunkDuration) * chunk.chars) /
      pa.totalChars
    return fraction * this.estimatedPageDuration()
  }

  private onTimeUpdate() {
    if (!this.audio) return
    const state = useReader.getState().tts
    if (state.status !== "playing") return
    // Direct audio (podcast/summary/streamed text): plain time tracking, no sentence sync
    if (!this.pageAudio) {
      if (this.textPlaybackQueue) {
        const elapsed = this.textQueueElapsed()
        const total = this.estimatedTextQueueDuration()
        if (Math.abs(elapsed - state.currentTime) > 0.25 || Math.abs(total - state.duration) > 0.5) {
          this.set({ currentTime: elapsed, duration: total })
        }
        return
      }
      if (Math.abs(this.audio.currentTime - state.currentTime) > 0.25) {
        this.set({ currentTime: this.audio.currentTime, duration: this.audio.duration || state.duration })
      }
      return
    }
    if (this.chunkStarts.length === 0) return
    const chunk = this.pageAudio.chunks[this.chunkIdx]
    const local = sentenceIndexAt(this.chunkStarts, this.audio.currentTime)
    const globalIdx = chunk.from + local
    const elapsed = this.pageElapsed()
    if (globalIdx !== state.sentenceIndex || Math.abs(elapsed - state.currentTime) > 0.25) {
      this.set({ sentenceIndex: globalIdx, currentTime: elapsed })
    }
  }

  private onEnded() {
    if (this.interruptionState) {
      void this.resumeFromInterruption()
      return
    }
    if (this.textPlaybackQueue) {
      const queue = this.textPlaybackQueue
      const nextIdx = queue.currentIdx + 1
      if (nextIdx < queue.chunks.length) {
        queue.currentIdx = nextIdx
        void this.playTextQueueChunk(nextIdx, queue.token)
        return
      } else {
        this.stopSpeaking()
        return
      }
    }
    if (this.docId == null || this.page == null || !this.pageAudio) {
      this.speakingMessageId = null
      this.stop()
      return
    }
    const token = this.playToken
    this.prunePlayedChunks(this.page, this.chunkIdx + 1, token)
    if (this.chunkIdx + 1 < this.pageAudio.chunks.length) {
      void this.playChunk(this.chunkIdx + 1, token).catch((err) => {
        if (token === this.playToken) {
          console.error("TTS chunk transition failed:", err)
          this.reportFailure(err)
          this.stop()
        }
      })
    } else {
      this.advance(this.docId, this.page, token)
    }
  }

  /** Continue to the next page, or stop at the end of the document. Stays
   * in the SAME generation (token is not bumped) - this is a continuation
   * of what's already playing, not a new user action, and bumping here
   * would abort/revoke the next page's first chunk that maintainPrefetch
   * deliberately spilled ahead into under this same token, turning every
   * page boundary back into a cold start. */
  private advance(docId: number, pageNum: number, token: number) {
    if (token !== this.playToken) return
    const active = useReader.getState().activeReading
    const maxPages = active?.docId === docId ? active.numPages : 1
    if (pageNum >= maxPages) {
      this.stop()
      return
    }
    const state = useReader.getState()
    if (state.selectedDocId === docId && state.followReading) {
      state.setPage(pageNum + 1)
    }
    void this.startPage(docId, pageNum + 1, 0, token, true)
  }

  // ------------------------------------------------------------------
  // Direct audio playback (podcasts, summaries): one file, seekable

  /** Play an already-generated audio endpoint through the bottom player. */
  async playPodcast(podcast: Podcast, startTime?: number): Promise<void> {
    const currentExpanded = useReader.getState().expandedPodcast
    useReader.setState({
      activeReading: null,
      expandedReading: false,
      narrationSentences: [],
      activePodcast: podcast,
      ...(currentExpanded && currentExpanded.id !== podcast.id ? { expandedPodcast: podcast } : {}),
    })
    await this.playAudioUrl(getPodcastAudioUrl(podcast.id), startTime)
  }

  async playAudioUrl(url: string, startTime?: number): Promise<void> {
    const token = this.bumpGeneration()
    const audio = this.ensureAudio()
    audio.pause()
    this.docId = null
    this.page = null
    this.pageAudio = null
    this.chunkStarts = []
    useReader.setState((state) => ({
      activeReading: null,
      expandedReading: false,
      narrationSentences: [],
      tts: {
        ...state.tts,
        status: "preparing",
        docId: null,
        page: null,
        sentenceIndex: 0,
        sentenceCount: 0,
        currentTime: 0,
        duration: 0,
      },
    }))

    const currentPodcast = useReader.getState().activePodcast
    if (!currentPodcast || getPodcastAudioUrl(currentPodcast.id) !== url) {
      useReader.setState({
        activePodcast: null,
        expandedPodcast: null,
      })
    }

    this.directAudioSource = { kind: "podcast", id: url }
    try {
      const blobUrl = await fetchAudioBlobUrl(url)
      if (token !== this.playToken) {
        URL.revokeObjectURL(blobUrl)
        return
      }
      if (this.summaryBlobUrl) URL.revokeObjectURL(this.summaryBlobUrl)
      this.summaryBlobUrl = blobUrl
      audio.src = blobUrl
      audio.playbackRate = useReader.getState().tts.speed
      audio.muted = useReader.getState().tts.muted
      await new Promise<void>((resolve, reject) => {
        audio.onloadedmetadata = () => resolve()
        audio.onerror = () => reject(new Error("Audio failed to load"))
      })
      if (token !== this.playToken) return
      if (startTime != null && startTime > 0 && isFinite(startTime)) {
        audio.currentTime = Math.min(startTime, audio.duration || startTime)
      }
      await audio.play()
      if (token !== this.playToken) return
      this.set({ status: "playing", duration: audio.duration, currentTime: audio.currentTime })
    } catch (error) {
      if (token === this.playToken) {
        console.error("TTS audio playback failed:", error)
        this.stop()
        throw error
      }
    }
  }

  /** Estimated total duration across all speech chunks in the chat response. */
  private estimatedTextQueueDuration(): number {
    if (!this.textPlaybackQueue) return 0
    const queue = this.textPlaybackQueue
    const chunks = queue.chunks
    let knownChars = 0
    let knownDuration = 0
    for (let i = 0; i < chunks.length; i++) {
      const d = queue.chunkDurations.get(i)
      if (d != null && d > 0) {
        knownDuration += d
        knownChars += chunks[i].length
      }
    }
    const rate = knownChars > 0 ? knownDuration / knownChars : 0.058
    let totalEst = 0
    for (let i = 0; i < chunks.length; i++) {
      const d = queue.chunkDurations.get(i)
      totalEst += d != null && d > 0 ? d : chunks[i].length * rate
    }
    return Math.max(totalEst, 1)
  }

  /** Continuous elapsed playback time across all speech chunks in the response. */
  private textQueueElapsed(): number {
    if (!this.textPlaybackQueue || !this.audio) return 0
    const queue = this.textPlaybackQueue
    const currentIdx = queue.currentIdx
    const chunks = queue.chunks
    let knownChars = 0
    let knownDuration = 0
    for (let i = 0; i < chunks.length; i++) {
      const d = queue.chunkDurations.get(i)
      if (d != null && d > 0) {
        knownDuration += d
        knownChars += chunks[i].length
      }
    }
    const rate = knownChars > 0 ? knownDuration / knownChars : 0.058

    let completed = 0
    for (let i = 0; i < currentIdx; i++) {
      const d = queue.chunkDurations.get(i)
      completed += d != null && d > 0 ? d : chunks[i].length * rate
    }
    const chunkCurrent = this.audio.currentTime || 0
    return Math.min(completed + chunkCurrent, this.estimatedTextQueueDuration())
  }

  /** Play an arbitrary text string out loud using chunked streaming (<0.5s time-to-first-sound). */
  async speakText(text: string, messageId?: number | string): Promise<void> {
    const currentStatus = useReader.getState().tts.status
    if (
      messageId != null &&
      this.speakingMessageId === messageId &&
      (currentStatus === "playing" || currentStatus === "preparing")
    ) {
      this.stopSpeaking()
      return
    }

    const chunks = splitTextIntoSpeechChunks(text)
    if (chunks.length === 0) return

    const token = this.bumpGeneration()
    this.speakingMessageId = messageId ?? null
    const audio = this.ensureAudio()
    audio.pause()
    this.docId = null
    this.page = null
    this.pageAudio = null
    this.chunkStarts = []
    this.directAudioSource = { kind: "summary", id: String(messageId ?? "chat") }

    useReader.setState((state) => ({
      activeReading: null,
      expandedReading: false,
      narrationSentences: [],
      tts: {
        ...state.tts,
        status: "preparing",
        docId: null,
        page: null,
        sentenceIndex: 0,
        sentenceCount: chunks.length,
        currentTime: 0,
        duration: 0,
      },
    }))

    const prefetches = new Map<number, Promise<TtsStreamResult>>()
    const chunkDurations = new Map<number, number>()
    this.textPlaybackQueue = {
      chunks,
      currentIdx: 0,
      token,
      messageId,
      prefetches,
      chunkDurations,
    }

    // Prefetch chunk 0 immediately
    prefetches.set(0, streamTts(chunks[0], this.voice, 1.0, undefined, this.sessionId))
    // Prefetch chunk 1 in parallel immediately
    if (chunks.length > 1) {
      prefetches.set(1, streamTts(chunks[1], this.voice, 1.0, undefined, this.sessionId))
    }

    await this.playTextQueueChunk(0, token)
  }

  private async playTextQueueChunk(index: number, token: number, startOffsetInChunk: number = 0): Promise<void> {
    if (token !== this.playToken || !this.textPlaybackQueue) return
    const queue = this.textPlaybackQueue
    const chunks = queue.chunks
    if (index >= chunks.length) {
      this.stopSpeaking()
      return
    }

    const audio = this.ensureAudio()
    let prepPromise = queue.prefetches.get(index)
    if (!prepPromise) {
      prepPromise = streamTts(chunks[index], this.voice, 1.0, undefined, this.sessionId)
      queue.prefetches.set(index, prepPromise)
    }

    try {
      const prep = await prepPromise
      if (token !== this.playToken || !this.textPlaybackQueue) {
        URL.revokeObjectURL(prep.blobUrl)
        return
      }

      if (this.summaryBlobUrl) URL.revokeObjectURL(this.summaryBlobUrl)
      this.summaryBlobUrl = prep.blobUrl
      audio.src = prep.blobUrl
      audio.playbackRate = useReader.getState().tts.speed
      audio.muted = useReader.getState().tts.muted

      await new Promise<void>((resolve, reject) => {
        if (audio.readyState >= 1) {
          resolve()
          return
        }
        const timer = setTimeout(() => resolve(), 3000)
        audio.onloadedmetadata = () => {
          clearTimeout(timer)
          resolve()
        }
        audio.onerror = () => {
          clearTimeout(timer)
          reject(new Error("Audio chunk failed to load"))
        }
      })

      if (token !== this.playToken || !this.textPlaybackQueue) return

      if (prep.duration && prep.duration > 0) {
        queue.chunkDurations.set(index, prep.duration)
      } else if (audio.duration && !Number.isNaN(audio.duration)) {
        queue.chunkDurations.set(index, audio.duration)
      }

      if (startOffsetInChunk > 0) {
        try {
          audio.currentTime = Math.min(startOffsetInChunk, Math.max(0, (audio.duration || 10) - 0.1))
        } catch {
          // Ignore early seek exception
        }
      }

      try {
        await audio.play()
      } catch (playErr) {
        console.warn("audio.play() in playTextQueueChunk was blocked:", playErr)
      }

      if (token !== this.playToken || !this.textPlaybackQueue) return

      const totalDuration = this.estimatedTextQueueDuration()
      const currentElapsed = this.textQueueElapsed()
      this.set({ status: "playing", duration: totalDuration, currentTime: currentElapsed })

      // Maintain background prefetch for subsequent chunks
      const nextIndex = index + 1
      if (nextIndex < chunks.length && !queue.prefetches.has(nextIndex)) {
        queue.prefetches.set(nextIndex, streamTts(chunks[nextIndex], this.voice, 1.0, undefined, this.sessionId))
      }
      const nextNext = index + 2
      if (nextNext < chunks.length && !queue.prefetches.has(nextNext)) {
        queue.prefetches.set(nextNext, streamTts(chunks[nextNext], this.voice, 1.0, undefined, this.sessionId))
      }
    } catch (err) {
      if (token === this.playToken) {
        console.error("playTextQueueChunk failed:", err)
        this.stopSpeaking()
      }
    }
  }

  stopSpeaking(): void {
    this.speakingMessageId = null
    this.stop()
  }

  getSpeakingMessageId(): number | string | null {
    return this.speakingMessageId
  }

  /** Seek within direct audio (podcast/summary/streamed text) playback across the entire message. */
  seekTo(seconds: number) {
    if (this.pageAudio) return

    if (this.textPlaybackQueue) {
      const queue = this.textPlaybackQueue
      const total = this.estimatedTextQueueDuration()
      const targetTime = Math.max(0, Math.min(seconds, total - 0.1))

      let knownChars = 0
      let knownDuration = 0
      for (let i = 0; i < queue.chunks.length; i++) {
        const d = queue.chunkDurations.get(i)
        if (d != null && d > 0) {
          knownDuration += d
          knownChars += queue.chunks[i].length
        }
      }
      const rate = knownChars > 0 ? knownDuration / knownChars : 0.058

      let acc = 0
      let targetChunkIdx = 0
      let offsetInChunk = 0
      for (let i = 0; i < queue.chunks.length; i++) {
        const dur = queue.chunkDurations.get(i) || queue.chunks[i].length * rate
        if (targetTime < acc + dur || i === queue.chunks.length - 1) {
          targetChunkIdx = i
          offsetInChunk = targetTime - acc
          break
        }
        acc += dur
      }

      if (targetChunkIdx === queue.currentIdx && this.audio) {
        this.audio.currentTime = Math.max(0, offsetInChunk)
        this.set({ currentTime: targetTime })
      } else {
        queue.currentIdx = targetChunkIdx
        void this.playTextQueueChunk(targetChunkIdx, queue.token, offsetInChunk)
      }
      return
    }

    if (!this.audio) return
    const duration = this.audio.duration
    if (!duration) return
    this.audio.currentTime = Math.max(0, Math.min(seconds, duration - 0.1))
    this.set({ currentTime: this.audio.currentTime })
  }

  skipSeconds(delta: number) {
    if (this.textPlaybackQueue) {
      const current = this.textQueueElapsed()
      this.seekTo(current + delta)
      return
    }
    if (!this.audio || this.pageAudio) return
    this.seekTo(this.audio.currentTime + delta)
  }

  // ------------------------------------------------------------------
  // Interruption management

  /** Pause podcast playback for listener question/interruption. */
  pauseForInterruption(rewindStartTime?: number): { savedTime: number; podcast: Podcast } | null {
    if (this.interruptionState) {
      return {
        savedTime: this.interruptionState.savedTime,
        podcast: this.interruptionState.podcast,
      }
    }
    const podcast = useReader.getState().activePodcast
    if (!podcast) return null

    const audio = this.ensureAudio()
    const savedTime = rewindStartTime != null ? Math.max(0, rewindStartTime) : audio.currentTime
    const wasPlaying = useReader.getState().tts.status === "playing"
    audio.pause()

    const podcastBlobUrl = this.summaryBlobUrl || audio.src || getPodcastAudioUrl(podcast.id)

    this.interruptionState = {
      podcast,
      savedTime,
      wasPlaying,
      podcastBlobUrl,
      answerBlobUrl: null,
    }

    this.set({ status: "paused" })
    return { savedTime, podcast }
  }

  /** Play generated interruption answer without wiping active podcast context. */
  async playInterruptionAnswer(answerUrl: string, onFinished?: () => void): Promise<void> {
    const audio = this.ensureAudio()
    audio.pause()

    const currentPodcast = useReader.getState().activePodcast
    if (!this.interruptionState && currentPodcast) {
      this.interruptionState = {
        podcast: currentPodcast,
        savedTime: audio.currentTime,
        wasPlaying: false,
        podcastBlobUrl: this.summaryBlobUrl || audio.src || getPodcastAudioUrl(currentPodcast.id),
      }
    }

    const blobUrl = await fetchAudioBlobUrl(answerUrl)

    if (this.interruptionState) {
      if (this.interruptionState.answerBlobUrl) {
        URL.revokeObjectURL(this.interruptionState.answerBlobUrl)
      }
      this.interruptionState.answerBlobUrl = blobUrl
      this.interruptionState.onFinished = onFinished
    }

    audio.src = blobUrl
    audio.playbackRate = useReader.getState().tts.speed
    audio.muted = useReader.getState().tts.muted

    await new Promise<void>((resolve, reject) => {
      audio.onloadedmetadata = () => resolve()
      audio.onerror = () => reject(new Error("Interruption answer audio failed to load"))
    })

    try {
      await audio.play()
    } catch (err) {
      console.warn("Autoplay was blocked for interruption answer:", err)
    }

    this.set({
      status: "playing",
      currentTime: 0,
      duration: audio.duration || 0,
    })
  }

  /** Restore original podcast playback after interruption. */
  async resumeFromInterruption(customResumeTime?: number): Promise<void> {
    const state = this.interruptionState
    if (!state || !this.audio) return

    const audio = this.audio
    audio.pause()

    if (state.answerBlobUrl) {
      URL.revokeObjectURL(state.answerBlobUrl)
    }

    if (state.podcastBlobUrl && audio.src !== state.podcastBlobUrl) {
      audio.src = state.podcastBlobUrl
      await new Promise<void>((resolve) => {
        const onLoaded = () => {
          audio.removeEventListener("loadedmetadata", onLoaded)
          audio.removeEventListener("error", onLoaded)
          resolve()
        }
        audio.addEventListener("loadedmetadata", onLoaded)
        audio.addEventListener("error", onLoaded)
      })
    }

    const targetTime = customResumeTime != null ? Math.max(0, customResumeTime) : state.savedTime
    try {
      audio.currentTime = targetTime
    } catch {
      // Ignore seek error if metadata not yet ready
    }

    const finishedCb = state.onFinished
    const wasPlaying = state.wasPlaying
    this.interruptionState = null

    if (wasPlaying) {
      try {
        await audio.play()
        this.set({
          status: "playing",
          currentTime: targetTime,
          duration: audio.duration || state.podcast.duration || 0,
        })
      } catch {
        this.set({ status: "paused", currentTime: targetTime })
      }
    } else {
      this.set({ status: "paused", currentTime: targetTime })
    }

    finishedCb?.()
  }

  cancelInterruption(): Promise<void> {
    return this.resumeFromInterruption()
  }

  isInterrupted(): boolean {
    return this.interruptionState !== null
  }

  getInterruptionState() {
    return this.interruptionState
  }

  /** True when the engine is playing a standalone file (podcast/summary). */
  isDirectAudio(): boolean {

    return this.pageAudio == null
  }

  async playSummaryAudio(summaryId: number): Promise<void> {
    const token = this.bumpGeneration()
    const audio = this.ensureAudio()
    audio.pause()
    this.docId = null
    this.page = null
    this.pageAudio = null
    this.chunkStarts = []
    useReader.setState((state) => ({
      activePodcast: null,
      expandedPodcast: null,
      activeReading: null,
      expandedReading: false,
      narrationSentences: [],
      tts: {
        ...state.tts,
        status: "preparing",
        docId: null,
        page: null,
        sentenceIndex: 0,
        sentenceCount: 0,
      },
    }))

    this.directAudioSource = { kind: "summary", id: summaryId }
    try {
      const res = await playSummary(summaryId)
      let resolved: { url: string; duration: number; cacheId: number | null } | null = null
      if (res.status === "cached" && res.cache_id != null) {
        resolved = {
          url: getAudioUrl(res.cache_id, this.sessionId),
          duration: res.duration ?? 0,
          cacheId: res.cache_id,
        }
      } else {
        for (let i = 0; i < 120; i++) {
          await new Promise((r) => setTimeout(r, 500))
          if (token !== this.playToken) return
          const status = await getTtsCacheStatus(res.text_hash)
          if (status.status === "ready" && status.cache_id != null) {
            resolved = {
              url: getAudioUrl(status.cache_id, this.sessionId),
              duration: status.duration ?? 0,
              cacheId: status.cache_id,
            }
            break
          }
        }
        if (!resolved) throw new Error("TTS generation timed out")
      }
      if (token !== this.playToken) return

      this.startHeartbeat()
      const blobUrl = await fetchAudioBlobUrl(resolved.url)
      if (token !== this.playToken) {
        URL.revokeObjectURL(blobUrl)
        return
      }
      if (this.summaryBlobUrl) URL.revokeObjectURL(this.summaryBlobUrl)
      this.summaryBlobUrl = blobUrl
      audio.src = blobUrl
      audio.playbackRate = useReader.getState().tts.speed
      audio.muted = useReader.getState().tts.muted
      await new Promise<void>((resolve, reject) => {
        audio.onloadedmetadata = () => resolve()
        audio.onerror = () => reject(new Error("Audio failed to load"))
      })
      if (token !== this.playToken) return
      await audio.play()
      this.set({
        status: "playing",
        duration: audio.duration || resolved.duration,
        currentTime: 0,
      })
    } catch (err) {
      if (token === this.playToken) {
        console.error("TTS summary playback failed:", err)
        this.set({ status: "idle" })
      }
    }
  }

  // ------------------------------------------------------------------
  // Controls

  toggle() {
    const { status } = useReader.getState().tts
    if (!this.audio) return
    if (status === "playing") {
      this.audio.pause()
      this.set({ status: "paused" })
    } else if (status === "paused") {
      void this.audio.play()
      this.set({ status: "playing" })
    } else if (status === "idle" && this.directAudioSource) {
      if (this.directAudioSource.kind === "podcast") {
        void this.playAudioUrl(String(this.directAudioSource.id)).catch(() => {})
      } else {
        void this.playSummaryAudio(Number(this.directAudioSource.id)).catch(() => {})
      }
    }
  }

  stop() {
    if (this.clickDebounceTimer) clearTimeout(this.clickDebounceTimer)
    this.clickDebounceTimer = null
    this.clickDebouncePending = null

    this.bumpGeneration()
    if (this.interruptionState?.answerBlobUrl) {
      URL.revokeObjectURL(this.interruptionState.answerBlobUrl)
    }
    this.interruptionState = null
    if (this.audio) {
      this.audio.pause()
      this.audio.removeAttribute("src")
      this.audio.load()
    }
    if (this.summaryBlobUrl) {
      URL.revokeObjectURL(this.summaryBlobUrl)
      this.summaryBlobUrl = null
    }
    if (this.textPlaybackQueue) {
      this.textPlaybackQueue.prefetches.forEach((promise) => {
        void promise
          .then((res) => {
            if (res?.blobUrl) URL.revokeObjectURL(res.blobUrl)
          })
          .catch(() => {})
      })
      this.textPlaybackQueue = null
    }
    this.speakingMessageId = null
    this.pageAudio = null
    this.stopHeartbeat()
    this.chunkStarts = []
    this.directAudioSource = null
    this.docId = null
    this.page = null
    useReader.setState((state) => ({
      activePodcast: null,
      expandedPodcast: null,
      activeReading: null,
      expandedReading: false,
      narrationSentences: [],
      tts: {
        ...state.tts,
        status: "idle",
        currentTime: 0,
        sentenceIndex: 0,
      },
    }))
    void releaseReadingAudio({ sessionId: this.sessionId, allSession: true }).catch(() => {})
  }

  /** Jump playback to a sentence on the currently loaded page. Debounced
   * like readPage - see debounceClick. */
  seekToSentence(index: number) {
    this.debounceClick(() => this.seekToSentenceNow(index))
  }

  private seekToSentenceNow(index: number) {
    const pa = this.pageAudio
    if (!pa || this.docId == null || this.page == null) return
    const clamped = Math.max(0, Math.min(index, pa.sentences.length - 1))
    const chunk = pa.chunks[this.chunkIdx]
    if (this.audio && clamped >= chunk.from && clamped < chunk.to) {
      this.audio.currentTime = this.chunkStarts[clamped - chunk.from] ?? 0
      this.set({ sentenceIndex: clamped, currentTime: this.pageElapsed() })
      if (useReader.getState().tts.status === "paused") this.toggle()
    } else {
      this.readPageNow(this.docId, this.page, clamped)
    }
  }

  skipPage(delta: number) {
    const state = useReader.getState()
    const active = state.activeReading
    const isNarrationActive =
      active != null &&
      (this.docId === active.docId || state.tts.docId === active.docId) &&
      state.tts.status !== "idle"

    if (isNarrationActive && active != null) {
      const docId = active.docId
      const base = (this.docId === docId ? this.page : null) ?? (state.tts.docId === docId ? state.tts.page : null) ?? 1
      const target = Math.max(1, Math.min(base + delta, active.numPages))
      if (target === base && delta !== 0) return
      if (state.selectedDocId === docId && state.followReading) {
        state.setPage(target)
      }
      this.readPage(docId, target, 0)
      return
    }

    const docId = state.selectedDocId
    if (docId == null) return
    const base = state.currentPage || 1
    const target = Math.max(1, Math.min(base + delta, state.numPages || base + delta))
    if (target === base && delta !== 0 && state.tts.status !== "idle") return
    state.setPage(target)
    this.readPage(docId, target, 0)
  }

  setSpeed(speed: number) {
    if (this.audio) {
      // Residual against the CURRENTLY PLAYING chunk's actual native
      // synthesis speed, not the raw requested speed. A chunk synthesized
      // at nativeSpeed=2.0 (Kokoro's cap) already plays at 2x from the
      // audio data itself; assigning `speed` directly here stacked a
      // further multiplier on top of that (2x native * 2x playbackRate =
      // 4x effective) until the next chunk swap happened to recompute it
      // correctly. Direct audio (podcast/summary) is always synthesized at
      // 1x, so pageAudio being null means no native adjustment is needed.
      this.audio.playbackRate = this.pageAudio ? speed / this.currentNativeSpeed : speed
    }
    this.set({ speed })
  }

  /** Remember preferred voice without interrupting playback (Settings load). */
  syncVoice(voice: string | null) {
    this.voice = voice || null
  }

  /** Apply a new narration voice mid-read: drop prepared chunks and
   * resynthesize from the current sentence. Podcasts/summaries unchanged. */
  setVoice(voice: string | null) {
    const next = voice || null
    if (next === this.voice) return
    this.voice = next

    const { tts } = useReader.getState()
    if (tts.page == null || this.docId == null || this.page == null) return
    if (tts.status === "idle") return

    void this.readPageNow(this.docId, this.page, tts.sentenceIndex)
  }

  getVoice(): string | null {
    return this.voice
  }

  toggleMute() {
    const muted = !useReader.getState().tts.muted
    if (this.audio) this.audio.muted = muted
    this.set({ muted })
  }

  /** Fraction (0-1) through the current page's audio, for bookmarks. */
  currentPosition(): number {
    const pa = this.pageAudio
    if (!pa || !this.audio || !this.chunkDuration) return 0
    const chunk = pa.chunks[this.chunkIdx]
    return (
      (chunk.charStart + (this.audio.currentTime / this.chunkDuration) * chunk.chars) /
      pa.totalChars
    )
  }
}

export const ttsEngine = new TtsEngine()
if (typeof window !== "undefined") {
  (window as any).ttsEngine = ttsEngine
}
