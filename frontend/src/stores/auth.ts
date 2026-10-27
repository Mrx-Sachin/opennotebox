import { create } from "zustand"
import { User } from "@/services/types"

/** Holds the signed-in user for display (username badge, etc).
    Tokens themselves stay in localStorage, not here - other owners'
    code (TopBar's logout, dashboard's mount check) already reads
    localStorage directly, so this store only adds the "who" on top
    of the existing "are we logged in" mechanism rather than replacing it. */
interface AuthState {
  user: User | null
  setUser: (user: User | null) => void
  clear: () => void
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  setUser: (user) => set({ user }),
  clear: () => set({ user: null }),
}))
