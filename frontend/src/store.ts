import { create } from 'zustand'

export const roles = ['supervisor', 'planner', 'researcher', 'coder', 'reviewer'] as const
export type Role = typeof roles[number]
export const labels: Record<string, string> = { supervisor: 'Supervisor', planner: 'Architect', researcher: 'Researcher', coder: 'Engineer', reviewer: 'Reviewer', approval: 'Human approval' }
export const colors: Record<string, string> = { supervisor: '#c5a0ff', planner: '#72b6ff', researcher: '#f4ca7b', coder: '#78e0bd', reviewer: '#f09ec7', approval: '#f4ca7b' }
export interface Task { title: string; role: string; status: string }
export interface Permission { id: string; sessionID: string; action: string; resources: string[] }
export interface Run {
  id: string; status: string; error?: string; created: number
  state: { tasks?: Task[]; outputs?: Record<string, string>; summary?: string; [key: string]: unknown }
  interrupts: unknown[]; permissions: Permission[]
  sessions: { role: string; session_id: string; status: string }[]
}
export interface Journal { id: number; kind: string; created: number; payload: Record<string, unknown> }
interface MissionStore {
  run: Run | null; active: string | null; logs: Journal[]; nodes: Record<string, { start: number; end?: number; data: unknown }>
  selected: string; stream: boolean
  reset: () => void; snapshot: (run: Run) => void; ingest: (event: Journal) => void
  select: (role: string) => void; setStream: (value: boolean) => void
}
export const useMission = create<MissionStore>((set) => ({
  run: null, active: null, logs: [], nodes: {}, selected: 'supervisor', stream: false,
  reset: () => set({ run: null, active: null, logs: [], nodes: {}, stream: false }),
  snapshot: (run) => set({ run }),
  select: (selected) => set({ selected }),
  setStream: (stream) => set({ stream }),
  ingest: (event) => set((s) => {
    if (s.logs.some((e) => e.id === event.id)) return s
    const logs = [...s.logs, event].slice(-1200)
    if (event.kind === 'node') {
      const node = String(event.payload.node)
      const starting = event.payload.phase === 'on_chain_start'
      return { logs, active: starting ? node : (s.active === node ? null : s.active),
        selected: starting ? node : s.selected,
        nodes: { ...s.nodes, [node]: { start: starting ? event.created : (s.nodes[node]?.start ?? event.created),
          end: starting ? undefined : event.created, data: event.payload.data } } }
    }
    if (event.kind === 'status' && s.run) return { logs, run: { ...s.run, status: String(event.payload.status), error: event.payload.error as string }, active: null }
    return { logs }
  }),
}))

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/api' + path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(typeof error.detail === 'string' ? error.detail : JSON.stringify(error.detail))
  }
  return response.json()
}
