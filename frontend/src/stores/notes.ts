import { create } from "zustand"
import {
  Note,
  NoteColor,
  NoteCreate,
  NoteUpdate,
  createNote,
  createNoteColor,
  deleteNote,
  deleteNoteColor,
  getNoteColors,
  getNotes,
  makeNoteFlashcard,
  updateNote,
  updateNoteColor,
} from "@/services/api"
import { DEFAULT_NOTE_COLORS } from "@/lib/noteColors"

export interface NoteSelection {
  docId: number
  page: number
  endPage?: number
  quote: string
  x: number
  y: number
}

interface NotesState {
  docId: number | null
  notes: Note[]
  colors: NoteColor[]
  loading: boolean
  focusedNoteId: number | null
  selection: NoteSelection | null
  /** Date.now() when the floating bar was last cleared. Click-to-read
   * ignores a click that just dismissed the bar. */
  selectionClearedAt: number
  composeRequested: boolean
  lastColor: string

  load: (docId: number) => Promise<void>
  loadColors: () => Promise<void>
  create: (body: NoteCreate) => Promise<Note>
  update: (id: number, body: NoteUpdate) => Promise<Note>
  remove: (id: number) => Promise<void>
  addColor: (name: string, hex: string, hint?: string) => Promise<NoteColor>
  renameColor: (id: string, name: string, hex?: string, hint?: string | null) => Promise<void>
  removeColor: (id: string) => Promise<void>
  setSelection: (sel: NoteSelection | null) => void
  focusNote: (id: number | null) => void
  requestCompose: () => void
  clearCompose: () => void
  makeFlashcard: (noteId: number) => Promise<void>
}

export const useNotes = create<NotesState>((set, get) => ({
  docId: null,
  notes: [],
  colors: DEFAULT_NOTE_COLORS,
  loading: false,
  focusedNoteId: null,
  selection: null,
  selectionClearedAt: 0,
  composeRequested: false,
  lastColor: "amber",

  load: async (docId) => {
    const switching = get().docId !== docId
    if (switching) {
      set({ docId, loading: true, notes: [], focusedNoteId: null, selection: null })
    }
    try {
      const [notes, colors] = await Promise.all([getNotes(docId), getNoteColors()])
      if (get().docId !== docId) return
      set({ notes, colors, loading: false })
    } catch {
      if (get().docId !== docId) return
      set({ notes: switching ? [] : get().notes, loading: false })
    }
  },

  loadColors: async () => {
    try {
      const colors = await getNoteColors()
      set({ colors })
    } catch {
      /* keep whatever is already loaded */
    }
  },

  create: async (body) => {
    const note = await createNote(body)
    set((s) => ({
      notes: [...s.notes, note].sort((a, b) => a.page_number - b.page_number || a.id - b.id),
      selection: null,
      selectionClearedAt: Date.now(),
      composeRequested: false,
      lastColor: note.color || s.lastColor,
    }))
    return note
  },

  update: async (id, body) => {
    const note = await updateNote(id, body)
    set((s) => ({ notes: s.notes.map((n) => (n.id === id ? note : n)) }))
    return note
  },

  remove: async (id) => {
    await deleteNote(id)
    set((s) => ({
      notes: s.notes.filter((n) => n.id !== id),
      focusedNoteId: s.focusedNoteId === id ? null : s.focusedNoteId,
    }))
  },

  addColor: async (name, hex, hint) => {
    const color = await createNoteColor(name, hex, hint)
    set((s) => ({ colors: [...s.colors, color] }))
    return color
  },

  renameColor: async (id, name, hex, hint) => {
    const color = await updateNoteColor(id, {
      name,
      ...(hex ? { hex } : {}),
      ...(hint !== undefined ? { hint } : {}),
    })
    set((s) => ({ colors: s.colors.map((c) => (c.id === id ? color : c)) }))
  },

  removeColor: async (id) => {
    await deleteNoteColor(id)
    set((s) => ({ colors: s.colors.filter((c) => c.id !== id) }))
  },

  setSelection: (sel) =>
    set({
      selection: sel,
      composeRequested: sel ? get().composeRequested : false,
      selectionClearedAt: sel ? get().selectionClearedAt : Date.now(),
    }),
  focusNote: (id) => set({ focusedNoteId: id }),
  requestCompose: () => set({ composeRequested: true }),
  clearCompose: () => set({ composeRequested: false }),
  makeFlashcard: async (noteId) => {
    const card = await makeNoteFlashcard(noteId)
    set((s) => ({
      notes: s.notes.map((n) => (n.id === noteId ? { ...n, flashcard_id: card.id } : n)),
    }))
  },
}))
