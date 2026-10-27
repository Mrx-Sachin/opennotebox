export interface Sentence {
  text: string
  /** char offset of sentence start within the page text */
  start: number
  end: number
}

/**
 * Split page text into sentences for TTS sync. Boundary = ./!/? followed by
 * whitespace, avoiding common abbreviation and decimal false-positives.
 * Newline runs (paragraph breaks) also terminate a sentence.
 */
export function splitSentences(text: string): Sentence[] {
  const sentences: Sentence[] = []
  const re = /[^.!?\n]+(?:[.!?]+["')\]]?|\n+|$)/g
  let match: RegExpExecArray | null
  while ((match = re.exec(text)) !== null) {
    const raw = match[0]
    const trimmed = raw.trim()
    if (!trimmed) continue
    const leading = raw.length - raw.trimStart().length
    sentences.push({
      text: trimmed,
      start: match.index + leading,
      end: match.index + leading + trimmed.length,
    })
  }
  return sentences
}

/**
 * Apportion total audio duration across sentences by character weight.
 * Returns cumulative start times, one per sentence.
 */
export function sentenceStartTimes(sentences: Sentence[], duration: number): number[] {
  const totalChars = sentences.reduce((sum, s) => sum + s.text.length, 0) || 1
  const starts: number[] = []
  let elapsed = 0
  for (const s of sentences) {
    starts.push(elapsed)
    elapsed += (s.text.length / totalChars) * duration
  }
  return starts
}

/** Index of the sentence playing at time t, given cumulative start times. */
export function sentenceIndexAt(starts: number[], t: number): number {
  let idx = 0
  for (let i = 0; i < starts.length; i++) {
    if (starts[i] <= t) idx = i
    else break
  }
  return idx
}
