"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import TopBar from "@/components/layout/TopBar"
import Sidebar from "@/components/layout/Sidebar"
import { listUsers, deleteUser, updateUser, User, getMe } from "@/services/api"
import { useReader } from "@/stores/reader"
import { useAuth } from "@/stores/auth"
import { Shield, Users, UserX, AlertCircle, CheckCircle, Loader2, Trash2, Plus, RefreshCw } from "lucide-react"

interface UserRowProps {
  user: User
  onToggleAdmin: (userId: number, currentAdmin: boolean) => void
  onDelete: (userId: number) => void
}

function UserRow({ user, onToggleAdmin, onDelete }: UserRowProps) {
  return (
    <div className="flex items-center justify-between rounded-lg border border-surface bg-panel p-3 hover:bg-surface/50">
      <div className="flex items-center gap-3">
        <div className={`flex h-10 w-10 items-center justify-center rounded-full ${user.is_admin ? "bg-indigo/20 text-indigo" : "bg-surface text-cream-dim"}`}>
          {user.is_admin ? <Shield className="h-5 w-5" /> : <Users className="h-5 w-5" />}
        </div>
        <div>
          <div className="flex items-center gap-2">
            <span className="font-medium text-cream">{user.username}</span>
            {user.is_admin && (
              <span className="rounded-full bg-indigo/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-indigo">
                Admin
              </span>
            )}
          </div>
          <div className="text-xs text-cream-dim">
            ID: {user.id} · Joined: {new Date(user.created_at).toLocaleDateString()}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button
          onClick={() => onToggleAdmin(user.id, user.is_admin)}
          className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
            user.is_admin
              ? "bg-void text-cream hover:bg-surface"
              : "bg-indigo text-white hover:bg-indigo/80"
          }`}
        >
          {user.is_admin ? <UserX className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
          {user.is_admin ? "Revoke" : "Promote"}
        </button>
        <button
          onClick={() => onDelete(user.id)}
          className="flex items-center gap-1.5 rounded-md bg-[#ef4444]/10 px-3 py-1.5 text-xs font-medium text-[#ef4444] transition-colors hover:bg-[#ef4444]/20"
        >
          <Trash2 className="h-3.5 w-3.5" />
          Delete
        </button>
      </div>
    </div>
  )
}

export default function AdminPage() {
  const router = useRouter()
  const { sidebarCollapsed, selectedDocId, selectDoc, setUploadModalOpen, toggleSidebar } = useReader()
  const user = useAuth((s) => s.user)
  const [users, setUsers] = useState<User[]>([])
  const [loading, setLoading] = useState(true)
  const [processingUser, setProcessingUser] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Populate user from localStorage token on mount
  useEffect(() => {
    const token = localStorage.getItem("token")
    if (!token) {
      router.push("/login")
      return
    }
    if (!user) {
      getMe().then((u) => useAuth.getState().setUser(u)).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!user) {
      // Waiting for user to load
      return
    }
    if (!user.is_admin) {
      router.push("/dashboard")
      return
    }
    fetchUsers()
  }, [user, router])

  const fetchUsers = async () => {
    try {
      setError(null)
      setLoading(true)
      const data = await listUsers()
      setUsers(data)
    } catch (err) {
      console.error("Failed to fetch users:", err)
      setError("Failed to load users")
    } finally {
      setLoading(false)
    }
  }

  const handleToggleAdmin = async (userId: number, currentAdmin: boolean) => {
    try {
      setProcessingUser(userId)
      const updated = await updateUser(userId, !currentAdmin)
      setUsers((prev) => prev.map((u) => (u.id === userId ? updated : u)))
    } catch (err) {
      console.error("Failed to update user:", err)
      setError(`Failed to ${currentAdmin ? "demote" : "promote"} user`)
    } finally {
      setProcessingUser(null)
    }
  }

  const handleDelete = async (userId: number) => {
    if (!window.confirm("Are you sure you want to delete this user? This cannot be undone.")) {
      return
    }
    try {
      setProcessingUser(userId)
      await deleteUser(userId)
      setUsers((prev) => prev.filter((u) => u.id !== userId))
    } catch (err) {
      console.error("Failed to delete user:", err)
      setError("Failed to delete user")
    } finally {
      setProcessingUser(null)
    }
  }

  if (!user?.is_admin) {
    return null
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-void text-cream">
      <TopBar />

      <div className="relative flex min-h-0 flex-1">
        <Sidebar documents={[]} loading={false} onDelete={() => {}} />

        <main className="flex min-w-0 flex-1 flex-col bg-void">
          <div className="flex h-16 items-center border-b border-surface bg-panel px-6">
            <div className="flex items-center gap-2">
              <Shield className="h-5 w-5 text-indigo" />
              <h1 className="text-lg font-semibold">Admin Panel</h1>
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-6">
            {error && (
              <div className="mb-6 rounded-lg border border-[#ef4444]/30 bg-[#ef4444]/10 p-4 text-sm text-[#ef4444]">
                <div className="flex items-center gap-2">
                  <AlertCircle className="h-4 w-4" />
                  {error}
                </div>
              </div>
            )}

            <div className="mb-6 rounded-lg border border-indigo/20 bg-indigo/5 p-4">
              <div className="flex items-start gap-3">
                <Shield className="mt-0.5 h-5 w-5 text-indigo" />
                <div>
                  <h3 className="font-medium text-indigo">Admin Access</h3>
                  <p className="mt-1 text-sm text-cream-dim">
                    You have full control over this instance. Use these tools responsibly.
                  </p>
                </div>
              </div>
            </div>

            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-sm font-semibold uppercase tracking-wide text-cream-dim">
                Users ({users.length})
              </h2>
              <button
                onClick={fetchUsers}
                disabled={loading || processingUser !== null}
                className="flex items-center gap-2 rounded-md bg-surface px-3 py-1.5 text-xs font-medium text-cream-dim hover:bg-surface/80 disabled:opacity-50"
              >
                {processingUser !== null ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5" />
                )}
                Refresh
              </button>
            </div>

            <div className="space-y-3">
              {loading ? (
                <div className="space-y-3">
                  {[...Array(4)].map((_, i) => (
                    <div key={i} className="h-20 animate-pulse rounded-lg bg-surface/60" />
                  ))}
                </div>
              ) : users.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-center">
                  <Users className="mb-3 h-12 w-12 text-cream-dim/50" />
                  <p className="text-cream-dim">No users found</p>
                </div>
              ) : (
                users.map((user) => (
                  <UserRow
                    key={user.id}
                    user={user}
                    onToggleAdmin={handleToggleAdmin}
                    onDelete={handleDelete}
                  />
                ))
              )}
            </div>

            <div className="mt-8 rounded-lg border border-surface bg-panel p-4">
              <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold text-cream">
                <AlertCircle className="h-4 w-4 text-cream-dim" />
                Admin Functions
              </h3>
              <ul className="space-y-2 text-sm text-cream-dim">
                <li className="flex items-start gap-2">
                  <CheckCircle className="mt-0.5 h-3.5 w-3.5 text-[#4ade80] shrink-0" />
                  <span><strong>List users:</strong> View all accounts on this instance</span>
                </li>
                <li className="flex items-start gap-2">
                  <CheckCircle className="mt-0.5 h-3.5 w-3.5 text-[#4ade80] shrink-0" />
                  <span><strong>Promote/demote:</strong> Grant or remove admin privileges</span>
                </li>
                <li className="flex items-start gap-2">
                  <CheckCircle className="mt-0.5 h-3.5 w-3.5 text-[#4ade80] shrink-0" />
                  <span><strong>Delete users:</strong> Remove stale or unwanted accounts</span>
                </li>
              </ul>
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}
