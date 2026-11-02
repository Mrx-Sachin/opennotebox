import { expect, test, type Page, type Locator } from "@playwright/test"
import {
  setupReaderMockHarness,
  parseCommittedFixtureWithPython,
  loadWhisperExpectations,
  loadWhisperCorruptPayload,
  visualLineCount,
  type SpanRect,
} from "./reader-mock-harness"

const expectations = loadWhisperExpectations()

interface HighlightObservation {
  pageNumber: number
  phrase: string
  activeSpans: SpanRect[]
  visualLines: number
  combinedText: string
  layerScrollTop: number
  layerScrollLeft: number
  containerRect: { left: number; top: number; width: number; height: number }
  normEnvelope: [number, number, number, number]
}

/**
 * Extract active highlight state and metrics from the browser DOM.
 */
async function captureHighlightState(page: Page, pageNumber: number): Promise<HighlightObservation> {
  const data = await page.evaluate((pNum) => {
    const container = document.querySelector(`#pdf-page-container-${pNum}`)
    if (!container) return null
    const containerRect = container.getBoundingClientRect()
    const textLayer = container.querySelector(".textLayer")
    const activeElements = Array.from(container.querySelectorAll(".tts-sentence-active"))

    const activeSpans = activeElements.map((el) => {
      const r = el.getBoundingClientRect()
      return {
        text: el.textContent || "",
        left: r.left,
        top: r.top,
        right: r.right,
        bottom: r.bottom,
        width: r.width,
        height: r.height,
        normLeft: (r.left - containerRect.left) / containerRect.width,
        normTop: (r.top - containerRect.top) / containerRect.height,
        normRight: (r.right - containerRect.left) / containerRect.width,
        normBottom: (r.bottom - containerRect.top) / containerRect.height,
      }
    })

    return {
      containerRect: {
        left: containerRect.left,
        top: containerRect.top,
        width: containerRect.width,
        height: containerRect.height,
      },
      layerScrollTop: textLayer ? textLayer.scrollTop : 0,
      layerScrollLeft: textLayer ? textLayer.scrollLeft : 0,
      activeSpans,
    }
  }, pageNumber)

  if (!data) {
    throw new Error(`Page container #pdf-page-container-${pageNumber} not found in DOM`)
  }

  const vLines = visualLineCount(data.activeSpans)
  const combinedText = data.activeSpans.map((s) => s.text).join(" ").replace(/\s+/g, " ").trim()

  const minX = data.activeSpans.length ? Math.min(...data.activeSpans.map((s) => s.normLeft)) : 0
  const minY = data.activeSpans.length ? Math.min(...data.activeSpans.map((s) => s.normTop)) : 0
  const maxX = data.activeSpans.length ? Math.max(...data.activeSpans.map((s) => s.normRight)) : 0
  const maxY = data.activeSpans.length ? Math.max(...data.activeSpans.map((s) => s.normBottom)) : 0

  return {
    pageNumber,
    phrase: "",
    activeSpans: data.activeSpans,
    visualLines: vLines,
    combinedText,
    layerScrollTop: data.layerScrollTop,
    layerScrollLeft: data.layerScrollLeft,
    containerRect: data.containerRect,
    normEnvelope: [minX, minY, maxX, maxY],
  }
}

interface LineReference {
  text: string
  top: number
  bottom: number
  left: number
  right: number
  toleranceX: number
  toleranceY: number
}

type Passage = typeof expectations.passages[number]

function canonical(text: string): string {
  // Independent of the application's matcher: PDF font spans add whitespace.
  return text.toLowerCase().replace(/\s/g, "")
}

async function physicalLine(anchor: Locator): Promise<LineReference> {
  return anchor.evaluate((element) => {
    const container = element.closest('[id^="pdf-page-container-"]')!
    const bounds = container.getBoundingClientRect()
    const target = element.getBoundingClientRect()
    const column = target.left + target.width / 2 < bounds.left + bounds.width / 2
    const spans = [...container.querySelectorAll('.textLayer span')].filter(span => {
      if (span.querySelector('span') || !span.textContent?.trim()) return false
      const rect = span.getBoundingClientRect()
      const overlap = Math.min(rect.bottom, target.bottom) - Math.max(rect.top, target.top)
      const sameColumn = (rect.left + rect.width / 2 < bounds.left + bounds.width / 2) === column
      return sameColumn && overlap > Math.min(rect.height, target.height) / 2
    })
    const rects = spans.map(span => span.getBoundingClientRect())
    return {
      text: spans.map(span => span.textContent).join(' '),
      left: (Math.min(...rects.map(rect => rect.left)) - bounds.left) / bounds.width,
      right: (Math.max(...rects.map(rect => rect.right)) - bounds.left) / bounds.width,
      top: (Math.min(...rects.map(rect => rect.top)) - bounds.top) / bounds.height,
      bottom: (Math.max(...rects.map(rect => rect.bottom)) - bounds.top) / bounds.height,
      toleranceX: 2 / bounds.width,
      toleranceY: 2 / bounds.height,
    }
  })
}

function assertSinglePhysicalLineHighlight(
  observation: HighlightObservation,
  reference: LineReference,
  expectedText: string,
  phrase: string,
) {
  const diagnostic = JSON.stringify({ expectedText, reference, actual: observation })
  expect(observation.activeSpans.length, `Missing highlight: ${diagnostic}`).toBeGreaterThan(0)
  expect(observation.visualLines, `Oversized highlight: ${diagnostic}`).toBe(1)
  expect(observation.layerScrollTop, `Internal text-layer scroll: ${diagnostic}`).toBe(0)
  expect(observation.layerScrollLeft, `Internal text-layer scroll: ${diagnostic}`).toBe(0)
  expect(canonical(reference.text), 'The independent PDF.js row must match the fixed physical line').toBe(canonical(expectedText))
  const actual = canonical(observation.combinedText)
  expect(actual, `Wrong line text: ${diagnostic}`).toContain(canonical(phrase))
  // A sentence can start partway through the 'setting. We caution' physical line.
  const allowed = [canonical(expectedText)]
  if (phrase === 'We caution') allowed.push(canonical(expectedText.replace(/^setting\.\s*/, '')))
  expect(allowed, `Incomplete or wrong highlighted line: ${diagnostic}`).toContain(actual)
  for (const span of observation.activeSpans) {
    expect(span.normTop!, `Wrong vertical line: ${diagnostic}`).toBeGreaterThanOrEqual(reference.top - reference.toleranceY)
    expect(span.normBottom!, `Wrong vertical line: ${diagnostic}`).toBeLessThanOrEqual(reference.bottom + reference.toleranceY)
    expect(span.normLeft!, `Wrong column: ${diagnostic}`).toBeGreaterThanOrEqual(reference.left - reference.toleranceX)
    expect(span.normRight!, `Wrong column: ${diagnostic}`).toBeLessThanOrEqual(reference.right + reference.toleranceX)
  }
}

async function navigateToPage(page: Page, pageNumber: number) {
  const input = page.locator('input[type="number"]')
  await expect(input).toBeVisible()
  await input.fill(String(pageNumber))
  await input.press('Enter')
  await expect(page.locator(`#pdf-page-container-${pageNumber} .textLayer span`).first()).toBeVisible()
}

function targetLine(page: Page, pageNumber: number, phrase: string) {
  return page.locator(`#pdf-page-container-${pageNumber} .textLayer span`).filter({ hasText: phrase }).first()
}

async function assertPlayback(page: Page, passage: Passage, reference: LineReference, target: Locator) {
  await expect(target, 'The clicked PDF span itself must be highlighted').toHaveClass(/tts-sentence-active/)
  await expect.poll(() => page.evaluate(() => {
    const audio = (window as any).__capturedAudioElement as HTMLAudioElement | undefined
    return !!audio && !audio.paused && audio.currentTime > 0
  }), { message: 'Actual Audio.currentTime must advance' }).toBe(true)
  // Check several frames while audio is playing, not only a transient CSS class.
  for (let sample = 0; sample < 3; sample++) {
    assertSinglePhysicalLineHighlight(
      await captureHighlightState(page, passage.page_number), reference, passage.expected_line_text, passage.phrase,
    )
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())))
  }
}

test.describe('Whisper PDF physical-line highlighting', () => {
  let parsedPages: ReturnType<typeof parseCommittedFixtureWithPython>
  test.beforeAll(() => {
    parsedPages = parseCommittedFixtureWithPython('e2e/fixtures/whisper-pages-7-8-11.pdf')
  })

  test.afterEach(async ({ page }) => {
    await page.unrouteAll({ behavior: 'ignoreErrors' })
  })

  for (const passage of expectations.passages) {
    test(`${passage.id}: clicked line and automatic next line stay aligned`, async ({ page }, testInfo) => {
      const errors: string[] = []
      page.on('pageerror', error => errors.push(error.message))
      const harness = await setupReaderMockHarness(page, { pages: parsedPages, docId: 3 })
      await page.goto('/dashboard/read/3')
      await navigateToPage(page, passage.page_number)
      const target = targetLine(page, passage.page_number, passage.phrase)
      await expect(target).toBeVisible()
      await target.scrollIntoViewIfNeeded()
      const reference = await physicalLine(target)
      await target.click()
      await assertPlayback(page, passage, reference, target)
      expect(harness.streamRequests[0]?.text, 'Speech must start from the clicked passage').toContain(passage.phrase)
      await page.screenshot({ path: testInfo.outputPath('clicked-line.png') })

      // Seek the real audio near its end. Let its native ended event advance the
      // application's queue, without clicking another line or changing reader state.
      const nextPhrase = passage.next_line_text.split(' ').slice(0, 3).join(' ')
      const nextTarget = targetLine(page, passage.page_number, passage.next_line_text)
      await expect(nextTarget).toHaveCount(1)
      const nextReference = await physicalLine(nextTarget)
      await page.evaluate(() => {
        const audio = (window as any).__capturedAudioElement as HTMLAudioElement
        if (!Number.isFinite(audio.duration) || audio.paused) throw new Error('Audio was not playing')
        audio.currentTime = audio.duration - 0.03
      })
      await expect(nextTarget, 'Natural audio completion must highlight the immediate next line').toHaveClass(/tts-sentence-active/)
      await expect(target).not.toHaveClass(/tts-sentence-active/)
      assertSinglePhysicalLineHighlight(
        await captureHighlightState(page, passage.page_number), nextReference, passage.next_line_text, nextPhrase,
      )
      await page.screenshot({ path: testInfo.outputPath('automatic-next-line.png') })
      expect(errors).toEqual([])
    })
  }

  test('zoom and follow-reading retain the same physical line', async ({ page }, testInfo) => {
    await setupReaderMockHarness(page, { pages: parsedPages, docId: 3 })
    await page.goto('/dashboard/read/3')
    const passage = expectations.passages.find((entry: Passage) => entry.id === 'page8-welsh')!
    await navigateToPage(page, passage.page_number)
    const observedWidths: number[] = []
    for (const action of ['initial', 'in', 'out'] as const) {
      if (action !== 'initial') {
        const before = await page.locator('#pdf-page-container-8 canvas').first().boundingBox()
        await page.getByTitle(action === 'in' ? 'Zoom in' : 'Zoom out', { exact: true }).click()
        await expect.poll(async () => (await page.locator('#pdf-page-container-8 canvas').first().boundingBox())!.width).not.toBe(before!.width)
      }
      const target = targetLine(page, passage.page_number, passage.phrase)
      await expect(target).toBeVisible()
      await target.scrollIntoViewIfNeeded()
      const reference = await physicalLine(target)
      await target.click()
      await assertPlayback(page, passage, reference, target)
      const observation = await captureHighlightState(page, passage.page_number)
      observedWidths.push(observation.containerRect.width)
      await page.screenshot({ path: testInfo.outputPath(`zoom-${action}.png`) })
    }
    expect(observedWidths[1]).toBeGreaterThan(observedWidths[0])
    expect(observedWidths[2]).toBeLessThan(observedWidths[1])
  })

  test('the same oracle rejects the actual legacy paragraph, missing highlight, and adjacent line', async ({ page }, testInfo) => {
    await setupReaderMockHarness(page, { pages: loadWhisperCorruptPayload(), docId: 3 })
    await page.goto('/dashboard/read/3')
    const passage = expectations.passages.find((entry: Passage) => entry.id === 'page8-welsh')!
    await navigateToPage(page, passage.page_number)
    const target = targetLine(page, passage.page_number, passage.phrase)
    await target.scrollIntoViewIfNeeded()
    const reference = await physicalLine(target)
    await target.click()
    await expect(target).toHaveClass(/tts-sentence-active/)
    const corrupt = await captureHighlightState(page, passage.page_number)
    expect(corrupt.visualLines).toBe(5)
    expect(() => assertSinglePhysicalLineHighlight(corrupt, reference, passage.expected_line_text, passage.phrase)).toThrow(/Oversized highlight/)
    await page.screenshot({ path: testInfo.outputPath('rejected-legacy-five-lines.png') })

    await page.locator('.tts-sentence-active').evaluateAll(elements => elements.forEach(element => element.classList.remove('tts-sentence-active')))
    const missing = await captureHighlightState(page, passage.page_number)
    expect(() => assertSinglePhysicalLineHighlight(missing, reference, passage.expected_line_text, passage.phrase)).toThrow(/Missing highlight/)

    const wrongTarget = targetLine(page, passage.page_number, passage.next_line_text.slice(0, 24))
    await expect(wrongTarget).toHaveCount(1)
    await wrongTarget.evaluate(element => element.classList.add('tts-sentence-active'))
    const wrong = await captureHighlightState(page, passage.page_number)
    expect(wrong.visualLines).toBe(1)
    expect(() => assertSinglePhysicalLineHighlight(wrong, reference, passage.expected_line_text, passage.phrase)).toThrow(/Wrong line text/)
    // Even copying the expected words onto the adjacent row cannot bypass geometry.
    const displaced = { ...wrong, combinedText: passage.expected_line_text }
    expect(() => assertSinglePhysicalLineHighlight(displaced, reference, passage.expected_line_text, passage.phrase)).toThrow(/Wrong vertical line/)
    await page.screenshot({ path: testInfo.outputPath('rejected-adjacent-line.png') })
  })
})
