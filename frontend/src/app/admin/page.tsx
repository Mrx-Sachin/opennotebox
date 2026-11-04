"use client"

import { useState, useEffect, useMemo } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import {
  Shield,
  Users,
  UserX,
  AlertCircle,
  Loader2,
  Trash2,
  Plus,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  FileText,
  HardDrive,
  BookOpen,
  ArrowLeft,
  LogOut,
  Settings,
  UserPlus,
  X,
} from "lucide-react"
import { getUserStats, deleteUser, updateUser, adminCreateUser, getMe, UserStats } from "@/services/api"
import { useAuth } from "@/stores/auth"

function formatBytes(bytes: number): string {
  if (!bytes || bytes === 0 || isNaN(bytes)) return "0 B"
  const units = ["B", "KB", "MB", "GB"]
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / Math.pow(1024, i)
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[i]}`
}

function formatDate(iso: string | null): string {
  if (!iso) return "-"
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
}

interface UserCardProps {
  user: UserStats
  onToggleAdmin: (userId: number, currentAdmin: boolean) => void
  onDelete: (userId: number) => void
  processing: boolean
}

function UserCard({ user, onToggleAdmin, onDelete, processing }: UserCardProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="rounded-xl border border-surface/80 bg-panel/60 transition-colors hover:border-surface">
      {/* User header row */}
      <div className="flex items-center gap-3 px-5 py-4">
        {/* Avatar */}
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${
            user.is_admin
              ? "bg-indigo/15 text-indigo"
              : "bg-surface text-cream-dim"
          }`}
        >
          {user.username[0].toUpperCase()}
        </div>

        {/* Name + meta */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-medium text-cream">{user.username}</span>
            {user.is_admin && (
              <span className="shrink-0 rounded bg-indigo/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-indigo">
                Admin
              </span>
            )}
          </div>
          <div className="mt-0.5 text-xs text-cream-dim">
            Joined {formatDate(user.created_at)}
          </div>
        </div>

        {/* Stats pills */}
        <div className="hidden items-center gap-3 sm:flex">
          <div className="flex items-center gap-1.5 rounded-md bg-surface/60 px-2.5 py-1.5">
            <FileText className="h-3.5 w-3.5 text-cream-dim" />
            <span className="text-xs font-medium text-cream">{user.doc_count}</span>
            <span className="text-xs text-cream-dim">docs</span>
          </div>
          <div className="flex items-center gap-1.5 rounded-md bg-surface/60 px-2.5 py-1.5">
            <HardDrive className="h-3.5 w-3.5 text-cream-dim" />
            <span className="text-xs font-medium text-cream">{formatBytes(user.total_bytes)}</span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1.5">
          {user.doc_count > 0 && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs text-cream-dim transition-colors hover:bg-surface hover:text-cream"
            >
              {expanded ? (
                <ChevronDown className="h-3.5 w-3.5" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5" />
              )}
              <span className="hidden sm:inline">Files</span>
            </button>
          )}
          <button
            onClick={() => onToggleAdmin(user.id, user.is_admin)}
            disabled={processing}
            className={`flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors disabled:opacity-50 ${
              user.is_admin
                ? "text-cream-dim hover:bg-surface hover:text-cream"
                : "bg-indigo/15 text-indigo hover:bg-indigo/25"
            }`}
          >
            {user.is_admin ? (
              <UserX className="h-3.5 w-3.5" />
            ) : (
              <Plus className="h-3.5 w-3.5" />
            )}
            {user.is_admin ? "Revoke" : "Promote"}
          </button>
          <button
            onClick={() => onDelete(user.id)}
            disabled={processing}
            className="flex items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium text-rust/80 transition-colors hover:bg-rust/10 hover:text-rust disabled:opacity-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Delete</span>
          </button>
        </div>
      </div>

      {/* Mobile stats (visible below sm breakpoint) */}
      <div className="flex items-center gap-3 border-t border-surface/40 px-5 py-2.5 sm:hidden">
        <div className="flex items-center gap-1.5">
          <FileText className="h-3.5 w-3.5 text-cream-dim" />
          <span className="text-xs text-cream">{user.doc_count} docs</span>
        </div>
        <div className="flex items-center gap-1.5">
          <HardDrive className="h-3.5 w-3.5 text-cream-dim" />
          <span className="text-xs text-cream">{formatBytes(user.total_bytes)}</span>
        </div>
      </div>

      {/* Expanded document list */}
      {expanded && user.documents.length > 0 && (
        <div className="border-t border-surface/40">
          <div className="px-5 py-2">
            <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-4 gap-y-0 text-[11px] font-medium uppercase tracking-wider text-cream-dim/60">
              <span className="py-1.5">Document</span>
              <span className="py-1.5 text-right">Pages</span>
              <span className="py-1.5 text-right">Size</span>
              <span className="py-1.5 text-right">Uploaded</span>
            </div>
            {user.documents.map((doc) => (
              <div
                key={doc.id}
                className="grid grid-cols-[1fr_auto_auto_auto] gap-x-4 border-t border-surface/20 py-2 text-xs"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <FileText className="h-3.5 w-3.5 shrink-0 text-indigo/60" />
                  <span className="truncate text-cream/90">{doc.title}</span>
                  {doc.processing_status !== "completed" && (
                    <span className="shrink-0 rounded bg-yellow-500/15 px-1.5 py-0.5 text-[10px] text-yellow-500">
                      {doc.processing_status}
                    </span>
                  )}
                </div>
                <span className="text-right tabular-nums text-cream-dim">{doc.num_pages || "-"}</span>
                <span className="text-right tabular-nums text-cream-dim">{formatBytes(doc.file_size)}</span>
                <span className="text-right text-cream-dim">{formatDate(doc.created_at)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function CreateUserModal({
  onClose,
  onCreate,
}: {
  onClose: () => void
  onCreate: (username: string, password: string) => Promise<void>
}) {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!username.trim() || !password) return
    setError("")
    setSubmitting(true)
    try {
      await onCreate(username.trim(), password)
    } catch (err: any) {
      setError(err.message || "Failed to create user")
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="w-full max-w-sm rounded-xl border border-surface bg-panel p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-5 flex items-center justify-between">
          <h3 className="text-base font-semibold text-cream">Create user</h3>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="new-username" className="mb-1.5 block text-xs font-medium text-cream-dim">
              Username
            </label>
            <input
              id="new-username"
              type="text"
              required
              autoFocus
              minLength={3}
              maxLength={50}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded-lg border border-surface bg-void px-3 py-2.5 text-sm text-cream placeholder:text-cream-dim/60 transition-colors focus:border-indigo focus:outline-none focus:ring-2 focus:ring-indigo/40"
              placeholder="e.g. testuser"
            />
          </div>
          <div>
            <label htmlFor="new-password" className="mb-1.5 block text-xs font-medium text-cream-dim">
              Password
            </label>
            <input
              id="new-password"
              type="text"
              required
              minLength={1}
              maxLength={72}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-surface bg-void px-3 py-2.5 text-sm text-cream placeholder:text-cream-dim/60 transition-colors focus:border-indigo focus:outline-none focus:ring-2 focus:ring-indigo/40"
              placeholder="Password"
            />
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-rust/30 bg-rust/10 px-3 py-2 text-xs text-rust">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={submitting || !username.trim() || !password}
            className="gradient-primary flex w-full items-center justify-center gap-2 rounded-lg py-2.5 text-sm font-medium text-white shadow-glow transition-transform hover:scale-[1.01] disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:scale-100"
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {submitting ? "Creating..." : "Create user"}
          </button>
        </form>
      </div>
    </div>
  )
}

export default function AdminPage() {
  const router = useRouter()
  const authUser = useAuth((s) => s.user)
  const [users, setUsers] = useState<UserStats[]>([])
  const [loading, setLoading] = useState(true)
  const [authChecking, setAuthChecking] = useState(true)
  const [processingUser, setProcessingUser] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showCreateModal, setShowCreateModal] = useState(false)

  useEffect(() => {
    const token = localStorage.getItem("token")
    if (!token) {
      router.push("/login")
      return
    }
    if (!authUser) {
      getMe()
        .then((u) => {
          useAuth.getState().setUser(u)
          setAuthChecking(false)
        })
        .catch(() => {
          setAuthChecking(false)
          router.push("/login")
        })
    } else {
      setAuthChecking(false)
    }
  }, [authUser, router])

  useEffect(() => {
    if (authChecking || !authUser) return
    if (!authUser.is_admin) return
    fetchStats()
  }, [authUser, authChecking])

  const fetchStats = async () => {
    try {
      setError(null)
      setLoading(true)
      const data = await getUserStats()
      setUsers(data)
    } catch {
      setError("Failed to load user stats")
    } finally {
      setLoading(false)
    }
  }

  const handleToggleAdmin = async (userId: number, currentAdmin: boolean) => {
    try {
      setProcessingUser(userId)
      await updateUser(userId, !currentAdmin)
      await fetchStats()
    } catch {
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
    } catch {
      setError("Failed to delete user")
    } finally {
      setProcessingUser(null)
    }
  }

  const handleCreateUser = async (username: string, password: string) => {
    try {
      await adminCreateUser(username, password)
      setShowCreateModal(false)
      await fetchStats()
    } catch (err: any) {
      const msg = err?.response?.data?.detail || "Failed to create user"
      throw new Error(msg)
    }
  }

  const totalStorage = useMemo(() => users.reduce((sum, u) => sum + u.total_bytes, 0), [users])
  const totalDocs = useMemo(() => users.reduce((sum, u) => sum + u.doc_count, 0), [users])

  const handleLogout = () => {
    localStorage.removeItem("token")
    localStorage.removeItem("refresh_token")
    useAuth.getState().clear()
    window.location.href = "/login"
  }

  if (authChecking) {
    return (
      <div className="flex h-screen items-center justify-center bg-void text-cream">
        <Loader2 className="h-8 w-8 animate-spin text-indigo" />
      </div>
    )
  }

  if (!authUser?.is_admin) {
    return (
      <div className="flex h-screen flex-col items-center justify-center bg-void px-4 text-center text-cream">
        <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-rust/10 text-rust">
          <Shield className="h-8 w-8" />
        </div>
        <h1 className="font-display text-xl font-bold tracking-tight sm:text-2xl">Access Denied</h1>
        <p className="mt-2 max-w-sm text-sm text-cream-dim">
          Admin privileges are required to view the Admin Panel. Signed in as <strong>{authUser?.username || "user"}</strong>.
        </p>
        <div className="mt-6 flex items-center gap-3">
          <Link
            href="/dashboard"
            className="flex items-center gap-2 rounded-lg bg-indigo/15 px-4 py-2 text-sm font-medium text-indigo hover:bg-indigo/25"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to Library
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-screen flex-col bg-void text-cream">
      {/* Minimal top bar */}
      <header className="relative shrink-0 bg-void">
        <div className="flex h-12 items-center gap-3 px-4">
          <div className="flex shrink-0 items-center gap-2">
            <div className="gradient-primary flex h-7 w-7 items-center justify-center rounded-lg shadow-glow">
              <BookOpen className="h-4 w-4 text-white" />
            </div>
            <span className="hidden font-display text-base font-bold tracking-tight text-cream sm:inline">
              OpenNoteBox
            </span>
          </div>
          <div className="min-w-0 flex-1" />
          <Link
            href="/dashboard"
            className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Library
          </Link>
          {authUser && (
            <span className="hidden max-w-[10rem] truncate text-xs text-cream-dim md:inline">
              {authUser.username}
            </span>
          )}
          <button
            onClick={handleLogout}
            title="Sign out"
            className="rounded-md p-2 text-cream-dim transition-colors hover:bg-surface hover:text-cream"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
        <div className="gradient-hairline absolute inset-x-0 bottom-0" />
      </header>

      {/* Content */}
      <main className="flex-1 overflow-y-auto reader-scroll">
        <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
          {/* Page header */}
          <div className="mb-8">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo/10">
                <Shield className="h-5 w-5 text-indigo" />
              </div>
              <div>
                <h1 className="text-xl font-semibold tracking-tight">Admin Panel</h1>
                <p className="text-sm text-cream-dim">Manage users and monitor storage</p>
              </div>
            </div>
          </div>

          {/* Summary cards */}
          <div className="mb-8 grid grid-cols-3 gap-3">
            <div className="rounded-xl border border-surface/80 bg-panel/60 px-4 py-3">
              <div className="flex items-center gap-2 text-cream-dim">
                <Users className="h-4 w-4" />
                <span className="text-xs uppercase tracking-wider">Users</span>
              </div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{users.length}</div>
            </div>
            <div className="rounded-xl border border-surface/80 bg-panel/60 px-4 py-3">
              <div className="flex items-center gap-2 text-cream-dim">
                <FileText className="h-4 w-4" />
                <span className="text-xs uppercase tracking-wider">Documents</span>
              </div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{totalDocs}</div>
            </div>
            <div className="rounded-xl border border-surface/80 bg-panel/60 px-4 py-3">
              <div className="flex items-center gap-2 text-cream-dim">
                <HardDrive className="h-4 w-4" />
                <span className="text-xs uppercase tracking-wider">Storage</span>
              </div>
              <div className="mt-1 text-2xl font-semibold tabular-nums">{formatBytes(totalStorage)}</div>
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="mb-6 flex items-center gap-2 rounded-lg border border-rust/30 bg-rust/10 px-4 py-3 text-sm text-rust">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{error}</span>
              <button onClick={() => setError(null)} className="ml-auto text-xs hover:underline">
                Dismiss
              </button>
            </div>
          )}

          {/* User list header */}
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-cream-dim">
              All users
            </h2>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowCreateModal(true)}
                className="flex items-center gap-1.5 rounded-md bg-indigo/15 px-3 py-1.5 text-xs font-medium text-indigo transition-colors hover:bg-indigo/25"
              >
                <UserPlus className="h-3.5 w-3.5" />
                Add user
              </button>
              <button
                onClick={fetchStats}
                disabled={loading}
                className="flex items-center gap-1.5 rounded-md bg-surface/60 px-3 py-1.5 text-xs text-cream-dim transition-colors hover:bg-surface hover:text-cream disabled:opacity-50"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                Refresh
              </button>
            </div>
          </div>

          {/* User list */}
          <div className="space-y-2">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-16">
                <Loader2 className="h-6 w-6 animate-spin text-indigo" />
                <span className="mt-3 text-sm text-cream-dim">Loading users...</span>
              </div>
            ) : users.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <Users className="mb-3 h-10 w-10 text-cream-dim/40" />
                <p className="text-cream-dim">No users found</p>
              </div>
            ) : (
              users.map((u) => (
                <UserCard
                  key={u.id}
                  user={u}
                  onToggleAdmin={handleToggleAdmin}
                  onDelete={handleDelete}
                  processing={processingUser === u.id}
                />
              ))
            )}
          </div>
        </div>
      </main>

      {/* Create user modal */}
      {showCreateModal && (
        <CreateUserModal
          onClose={() => setShowCreateModal(false)}
          onCreate={handleCreateUser}
        />
      )}
    </div>
  )
}
