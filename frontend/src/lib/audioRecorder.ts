export interface ActiveRecording {
  stop: () => Promise<Blob>
  cancel: () => Promise<void>
}

/**
 * Resamples float32 audio samples from one sample rate to another using linear interpolation.
 */
function resampleAudio(
  samples: Float32Array,
  fromRate: number,
  toRate: number
): Float32Array {
  if (fromRate === toRate || samples.length === 0) {
    return samples
  }
  const ratio = fromRate / toRate
  const targetLength = Math.round(samples.length / ratio)
  const result = new Float32Array(targetLength)
  for (let i = 0; i < targetLength; i++) {
    const originalPos = i * ratio
    const index = Math.floor(originalPos)
    const nextIndex = Math.min(index + 1, samples.length - 1)
    const fraction = originalPos - index
    result[i] = samples[index] * (1 - fraction) + samples[nextIndex] * fraction
  }
  return result
}

/**
 * Encodes mono 16-bit signed PCM samples into a clean WAV ArrayBuffer with a 44-byte header.
 */
function encodeWav(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const numChannels = 1
  const bitsPerSample = 16
  const bytesPerSample = bitsPerSample / 8
  const blockAlign = numChannels * bytesPerSample
  const byteRate = sampleRate * blockAlign
  const dataSize = samples.length * bytesPerSample
  const buffer = new ArrayBuffer(44 + dataSize)
  const view = new DataView(buffer)

  const writeString = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i))
    }
  }

  // RIFF Chunk descriptor
  writeString(0, "RIFF")
  view.setUint32(4, 36 + dataSize, true)
  writeString(8, "WAVE")

  // "fmt " sub-chunk
  writeString(12, "fmt ")
  view.setUint32(16, 16, true) // Subchunk1Size for PCM
  view.setUint16(20, 1, true) // AudioFormat: 1 = PCM
  view.setUint16(22, numChannels, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, byteRate, true)
  view.setUint16(32, blockAlign, true)
  view.setUint16(34, bitsPerSample, true)

  // "data" sub-chunk
  writeString(36, "data")
  view.setUint32(40, dataSize, true)

  // Write PCM audio samples
  let offset = 44
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    const pcm = s < 0 ? s * 0x8000 : s * 0x7fff
    view.setInt16(offset, Math.round(pcm), true)
    offset += 2
  }

  return buffer
}

/**
 * Starts recording mono audio from navigator.mediaDevices.getUserMedia
 * and provides a stop handle that encodes the recording to 16-bit PCM WAV at 24000Hz.
 */
export async function startRecording(): Promise<ActiveRecording> {
  if (typeof window === "undefined" || !navigator.mediaDevices?.getUserMedia) {
    throw new Error("Microphone capture is not supported in this environment.")
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  })

  type AudioContextConstructor = typeof AudioContext
  const AudioContextClass: AudioContextConstructor =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext: AudioContextConstructor }).webkitAudioContext

  let audioContext: AudioContext
  try {
    audioContext = new AudioContextClass({ sampleRate: 24000 })
  } catch {
    audioContext = new AudioContextClass()
  }

  const source = audioContext.createMediaStreamSource(stream)
  const processor = audioContext.createScriptProcessor(4096, 1, 1)

  const chunks: Float32Array[] = []
  let isClosed = false

  processor.onaudioprocess = (event: AudioProcessingEvent) => {
    if (isClosed) return
    const input = event.inputBuffer.getChannelData(0)
    chunks.push(new Float32Array(input))
  }

  source.connect(processor)
  processor.connect(audioContext.destination)

  const cleanup = async () => {
    if (isClosed) return
    isClosed = true

    try {
      source.disconnect()
      processor.disconnect()
    } catch {
      // Ignore disconnect errors
    }

    stream.getTracks().forEach((track) => {
      try {
        track.stop()
      } catch {
        // Ignore track stop errors
      }
    })

    if (audioContext.state !== "closed") {
      try {
        await audioContext.close()
      } catch {
        // Ignore close errors
      }
    }
  }

  const stop = async (): Promise<Blob> => {
    await cleanup()

    let totalLength = 0
    for (const chunk of chunks) {
      totalLength += chunk.length
    }

    const merged = new Float32Array(totalLength)
    let currentOffset = 0
    for (const chunk of chunks) {
      merged.set(chunk, currentOffset)
      currentOffset += chunk.length
    }

    const targetSampleRate = 24000
    const finalSamples = resampleAudio(merged, audioContext.sampleRate, targetSampleRate)
    const wavBuffer = encodeWav(finalSamples, targetSampleRate)

    return new Blob([wavBuffer], { type: "audio/wav" })
  }

  const cancel = async (): Promise<void> => {
    await cleanup()
  }

  return { stop, cancel }
}
