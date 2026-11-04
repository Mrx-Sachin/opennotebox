import { test, expect } from "@playwright/test"
import { login, openSampleDoc } from "./helpers"

test.describe("flashcards redesign", () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    const token = await page.evaluate(() => localStorage.getItem("token"))
    // Clean up any existing flashcards for sample doc 63
    const res = await page.request.get("/api/flashcards?document_id=63", {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (res.ok()) {
      const cards = await res.json()
      for (const c of cards) {
        await page.request.delete(`/api/flashcards/${c.id}?group=false`, {
          headers: { Authorization: `Bearer ${token}` },
        })
      }
    }
    await openSampleDoc(page)
  })

  test("full flashcard creation, 3D flipping, progressive hint, study ratings, and replay loop", async ({ page }) => {
    // 1. Open Flashcard launcher panel in Assistant
    const flashcardTabBtn = page.getByRole("tab", { name: "Cards" }).or(page.getByRole("button", { name: /Cards|Flashcards/i }))
    await expect(flashcardTabBtn).toBeVisible()
    await flashcardTabBtn.click()

    // Verify solid launcher panel rendered with header
    await expect(page.getByText(/Cards|Flashcards/).first()).toBeVisible()

    // 2. Click "Manual" to open FlashcardManager in Deck view
    const addCardBtn = page.getByRole("button", { name: "Manual", exact: true })
    await expect(addCardBtn).toBeVisible()
    await addCardBtn.click()

    // Verify FlashcardManager modal is open
    const managerModal = page.locator("div.fixed.z-50")
    await expect(managerModal).toBeVisible()
    await expect(managerModal.getByText("Flashcard Manager")).toBeVisible()

    // 3. Click "+ New Card" to open FlashcardEditor
    const newCardBtn = managerModal.getByRole("button", { name: /New Card/i })
    await expect(newCardBtn).toBeVisible()
    await newCardBtn.click()

    // Verify FlashcardEditor modal appears with solid theme and live preview
    await expect(page.getByRole("heading", { name: "New Flashcard" })).toBeVisible()

    // Fill in question, answer, and custom hint
    const uniqueQ = "What is the powerhouse of the cell? " + Date.now()
    const uniqueA = "Mitochondria produces ATP for the cell"
    const uniqueHint = "Think about organelle and respiration"

    await page.getByPlaceholder("e.g. What is the powerhouse of the cell?").fill(uniqueQ)
    await page.getByPlaceholder("e.g. Mitochondria").fill(uniqueA)
    await page.getByPlaceholder("e.g. Think about cellular respiration or organelle functions...").fill(uniqueHint)

    // Check Live Preview updates
    await expect(page.getByText(uniqueQ).first()).toBeVisible()
    const flipPreviewBtn = page.getByRole("button", { name: "Flip preview" })
    await expect(flipPreviewBtn).toBeVisible()
    await flipPreviewBtn.click()
    await expect(page.getByText(uniqueA).first()).toBeVisible()

    // Save the card
    await page.getByRole("button", { name: "Create Card" }).click()

    // 4. Card is now listed in Deck view!
    await expect(managerModal.getByText(uniqueQ)).toBeVisible({ timeout: 10000 })
    await expect(managerModal.getByText(uniqueA).first()).toBeVisible()

    // 5. Switch to Study tab
    const studyTabBtn = managerModal.getByRole("button", { name: "Study", exact: true })
    await studyTabBtn.click()

    // Check that our question is visible on the front of the 3D card
    await expect(managerModal.getByText(uniqueQ)).toBeVisible({ timeout: 10000 })

    // 6. Test Progressive Hint
    const hintBtn = managerModal.getByRole("button", { name: /Need a hint\?/i })
    await expect(hintBtn).toBeVisible()
    await hintBtn.click()
    await expect(managerModal.getByText(uniqueHint)).toBeVisible()

    // 7. Flip the card to reveal answer
    await managerModal.getByText(uniqueQ).click()

    // Verify back side shows answer
    await expect(managerModal.getByText(uniqueA).first()).toBeVisible()

    // 8. Humanized friendly review actions: "Still learning" (1), "Almost had it" (2), "Got it!" (3)
    const stillLearningBtn = managerModal.getByRole("button", { name: /Still learning/i })
    const almostHadItBtn = managerModal.getByRole("button", { name: /Almost had it/i })
    const gotItBtn = managerModal.getByRole("button", { name: /Got it!/i })

    await expect(stillLearningBtn).toBeVisible()
    await expect(almostHadItBtn).toBeVisible()
    await expect(gotItBtn).toBeVisible()

    // Click "Still learning" to mark as missed
    await stillLearningBtn.click()

    // 9. Session complete screen with replay arena!
    await expect(managerModal.getByText("Study Session Complete!")).toBeVisible()
    await expect(managerModal.getByText("Still learning").first()).toBeVisible()

    // 10. Replay loop: "Practice 1 missed card" button is shown!
    const replayMissedBtn = managerModal.getByRole("button", { name: /Practice \d+ missed card/i })
    await expect(replayMissedBtn).toBeVisible()
    await replayMissedBtn.click()

    // Front of the missed card is presented again for rapid mastery!
    await expect(managerModal.getByText(uniqueQ)).toBeVisible()

    // Flip again and this time mark "Got it!"
    await managerModal.getByText(uniqueQ).click()
    await expect(managerModal.getByText(uniqueA).first()).toBeVisible()
    await gotItBtn.click()

    // Session completes with mastery!
    await expect(managerModal.getByText("Missed Practice Complete!")).toBeVisible()
    await expect(managerModal.getByText("Got it smoothly")).toBeVisible()

    // Close FlashcardManager
    await managerModal.getByTitle("Close (Esc)").click()
  })

  test("cloze card with inline hint and free practice mode", async ({ page }) => {
    // 1. Open Flashcard launcher panel
    await page.getByRole("tab", { name: "Cards" }).or(page.getByRole("button", { name: /Cards|Flashcards/i })).first().click()
    const openBtn = page.getByRole("button", { name: /Manual|Deck|Study Cards/i }).first()
    await openBtn.click()

    const managerModal = page.locator("div.fixed.z-50")
    await expect(managerModal).toBeVisible()

    // Switch to Deck view
    await managerModal.getByRole("button", { name: "Deck", exact: true }).click()

    // 2. Click "+ New Card"
    await managerModal.getByRole("button", { name: /New Card/i }).click()

    // 3. Select "Cloze" format
    await page.getByRole("button", { name: "Cloze Fill-in-the-blank" }).click()

    // Fill in cloze text with hint: {{c1::ATP::energy currency}}
    const clozeText = "Mitochondria produce {{c1::ATP::cellular energy currency}} through oxidative phosphorylation."
    await page.locator("textarea").fill(clozeText)

    // Save cloze card
    await page.getByRole("button", { name: "Create Card" }).click()
    await expect(page.getByRole("heading", { name: "New Flashcard" })).not.toBeVisible({ timeout: 10000 })

    // 4. Verify card appears in Deck view
    await expect(managerModal.getByText("oxidative phosphorylation").first()).toBeVisible({ timeout: 10000 })

    // 5. Switch to Study tab
    await managerModal.getByRole("button", { name: "Study", exact: true }).click()

    // Make sure we can practice: click "Practice All" button
    const practiceAllBtn = managerModal.getByRole("button", { name: /Practice All/i })
    if (await practiceAllBtn.isVisible()) {
      await practiceAllBtn.click()
    }

    // Front of cloze card should hide ATP as a blank [ ... ] or hint trigger
    const hintBtn = managerModal.getByRole("button", { name: /Need a hint\?/i })
    await expect(hintBtn).toBeVisible()
    await hintBtn.click()

    // The inline cloze hint "cellular energy currency" should be visible!
    await expect(managerModal.getByText("cellular energy currency", { exact: true })).toBeVisible()

    // Flip card to reveal answer
    await managerModal.getByRole("button", { name: /Reveal Answer/i }).click()

    // Revealed cloze highlights ATP!
    await expect(managerModal.getByText("ATP").first()).toBeVisible()

    // Grade "Got it!"
    await managerModal.getByRole("button", { name: /Got it!/i }).click()

    // Close manager
    await managerModal.getByTitle("Close (Esc)").click()
  })

  test("space key flips card and does NOT trigger reader TTS", async ({ page }) => {
    // 1. Create a card to test
    const token = await page.evaluate(() => localStorage.getItem("token"))
    await page.request.post("/api/flashcards", {
      data: {
        document_id: 63,
        card_type: "basic",
        question: "Keyboard Isolation Question",
        answer: "Keyboard Isolation Answer",
        hint: "Isolation Hint",
      },
      headers: { Authorization: `Bearer ${token}` },
    })

    // 2. Open Flashcard launcher and open Study mode
    await page.getByRole("tab", { name: "Cards" }).or(page.getByRole("button", { name: /Cards|Flashcards/i })).first().click()
    const studyBtn = page.getByRole("button", { name: /Study Cards|Manual|Deck/i }).first()
    await studyBtn.click()

    const managerModal = page.locator("div.fixed.z-50")
    await expect(managerModal).toBeVisible()

    // Ensure we are in Study view
    const studyTab = managerModal.getByRole("button", { name: "Study", exact: true })
    if (await studyTab.isVisible()) {
      await studyTab.click()
    }

    // Check card is visible on front
    await expect(managerModal.getByText("Keyboard Isolation Question")).toBeVisible()

    // Verify reader TTS is currently idle before pressing Space
    const ttsInitial = await page.evaluate(() => {
      // @ts-ignore
      const state = window.__READER_STORE__?.getState?.() || {}
      return state.tts?.status || "idle"
    })
    expect(ttsInitial).toBe("idle")

    // 3. Press Space key on keyboard
    await page.keyboard.press("Space")

    // Card should now be flipped to answer side!
    await expect(managerModal.getByText("Keyboard Isolation Answer")).toBeVisible()

    // 4. Verify reader TTS status was NOT activated by the space press!
    const ttsAfter = await page.evaluate(() => {
      // @ts-ignore
      const state = window.__READER_STORE__?.getState?.() || {}
      return state.tts?.status || "idle"
    })
    expect(ttsAfter).toBe("idle")

    // Also verify no TTS audio player bar appeared or started playing
    await expect(page.locator("div.fixed.bottom-0").filter({ hasText: "Reading page" })).toHaveCount(0)

    // Close manager
    await page.keyboard.press("Escape")
    await expect(managerModal).not.toBeVisible()
  })
})
