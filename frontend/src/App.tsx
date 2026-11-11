import { useEffect, useRef, useState } from 'react'
import { Activity, ArrowDown, ArrowRight, Box, Check, ChevronDown, CircleHelp, Command, FolderOpen, GitBranch, History, LayoutDashboard, Maximize2, Orbit, Radio, Send, Settings2, ShieldCheck, Square, Terminal, Users, X } from 'lucide-react'
import { api, colors, labels, roles, useMission, type Journal, type Run } from './store'
import { Office, Topology } from './Office'

type Health = { backend: boolean; opencode: { connected: boolean; version?: string; url: string; stream?: boolean; error?: string } }
const short = (text: string, length = 80) => text.length > length ? text.slice(0, length) + '…' : text

function logText(entry: Journal): string {
  const event = entry.payload.event as { type?: string; data?: Record<string, unknown> } | undefined
  if (event) {
    const data = event.data ?? {}
    return `${event.type?.replace('session.', '')}  ${typeof data.delta === 'string' ? data.delta : JSON.stringify(data)}`
  }
  if (entry.kind === 'node') return `${entry.payload.node} ${entry.payload.phase === 'on_chain_start' ? 'started' : 'completed'}`
  return `${entry.kind}  ${JSON.stringify(entry.payload)}`
}

function Terminals() {
  const { selected, select, logs, run } = useMission()
  const bottom = useRef<HTMLDivElement>(null)
  const [follow, setFollow] = useState(true)
  const filtered = logs.filter((e) => e.payload.role === selected || e.payload.node === selected)
  useEffect(() => { if (follow) bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [logs, selected, follow])
  const session = run?.sessions.filter((s) => s.role === selected).at(-1)
  return <section className="terminal-panel">
    <div className="panel-title"><span><Terminal size={15}/>Agent terminals</span><span className="live-tag">LIVE</span></div>
    <div className="terminal-tabs">{roles.map((r) => <button key={r} title={labels[r]} className={selected === r ? 'selected' : ''} onClick={() => select(r)}><i style={{ background: colors[r] }}/>{labels[r].slice(0, 3)}</button>)}</div>
    <div className="terminal-toolbar"><span><span style={{ color: colors[selected] }}>●</span> {labels[selected] ?? selected}<span className="terminal-shell"> / output</span></span><button title="Toggle auto-scroll" className={follow ? 'on' : ''} onClick={() => setFollow(!follow)}><ArrowDown size={13}/></button></div>
    <div className="terminal-output">{filtered.length ? filtered.slice(-250).map((entry) => <div key={entry.id} className="log-line"><time>{new Date(entry.created * 1000).toLocaleTimeString('en-GB')}</time><span>{logText(entry)}</span></div>) : <div className="terminal-empty"><span className="prompt-symbol">❯</span> Waiting for agent output<span className="cursor"/><p>Real tool calls and execution results<br/>will appear here.</p></div>}<div ref={bottom}/></div>
    <div className="terminal-footer">{session ? <button title="Copy OpenCode session ID" onClick={() => navigator.clipboard.writeText(session.session_id)}>{short(session.session_id, 30)}</button> : <span>No active session</span>}<span>UTF-8</span></div>
  </section>
}

function Kanban() {
  const tasks = useMission((s) => s.run?.state.tasks) ?? []
  return <section className="kanban-panel"><div className="panel-title"><span><LayoutDashboard size={15}/>Mission tasks</span><span className="count">{tasks.length}</span></div>
    <div className="kanban-columns">{[['todo', 'To do'], ['in_progress', 'In progress'], ['reviewing', 'Review'], ['done', 'Done']].map(([key, title]) => <div key={key}><div className={`kanban-label ${key}`}><i/>{title}<span>{tasks.filter((t) => t.status === key).length}</span></div>{tasks.filter((t) => t.status === key).map((t, i) => <div className="task-card" key={i}>{t.title}<small style={{ color: colors[t.role] }}>{labels[t.role]}</small></div>)}</div>)}</div>
    {!tasks.length && <div className="empty-tasks"><Box size={22}/><p>A clear mission starts with a plan.</p><span>Dispatch a prompt to create your task board.</span></div>}
  </section>
}

export default function App() {
  const { run, active, selected, nodes, stream, snapshot, ingest, reset, setStream } = useMission()
  const [tab, setTab] = useState('office')
  const [health, setHealth] = useState<Health | null>(null)
  const [prompt, setPrompt] = useState('')
  const [directory, setDirectory] = useState(localStorage.getItem('orbit.directory') ?? '')
  const [mode, setMode] = useState('human')
  const [runId, setRunId] = useState(localStorage.getItem('orbit.run') ?? '')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showInspector, setShowInspector] = useState(false)
  const [password, setPassword] = useState('')
  const [history, setHistory] = useState<Run[] | null>(null)
  const [checkpoints, setCheckpoints] = useState<{ id: string; created: string; state: unknown }[] | null>(null)
  const [help, setHelp] = useState(false)
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    let mounted = true
    const check = () => api<Health>('/health').then((h) => { if (mounted) setHealth(h) }).catch(() => { if (mounted) setHealth(null) })
    void check()
    const timer = setInterval(check, 5000)
    return () => { mounted = false; clearInterval(timer) }
  }, [])
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer) }, [])
  useEffect(() => {
    if (!runId) return
    let mounted = true
    reset()
    localStorage.setItem('orbit.run', runId)
    const refresh = () => api<Run>(`/runs/${runId}`).then((r) => { if (mounted) snapshot(r) }).catch((e: Error) => { if (mounted) setError(e.message) })
    void refresh()
    const timer = setInterval(refresh, 2500)
    const source = new EventSource(`/api/runs/${runId}/events`)
    source.onopen = () => setStream(true)
    source.onerror = () => setStream(false)
    source.addEventListener('mission', (event) => { ingest(JSON.parse(event.data)); if (JSON.parse(event.data).kind === 'status') void refresh() })
    return () => { mounted = false; clearInterval(timer); source.close(); setStream(false) }
  }, [runId, reset, snapshot, ingest, setStream])

  async function action(fn: () => Promise<unknown>) {
    setBusy(true); setError('')
    try { await fn(); if (runId) snapshot(await api<Run>(`/runs/${runId}`)) }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  async function dispatch() {
    if (!prompt.trim() || !directory.trim()) { setError('Enter a mission and the absolute project directory.'); return }
    await action(async () => {
      const result = await api<{ id: string }>('/runs', { prompt, directory, mode })
      localStorage.setItem('orbit.directory', directory)
      setRunId(result.id); setPrompt('')
    })
  }
  const running = run?.status === 'running'
  const elapsed = active && nodes[active] ? Math.max(0, Math.floor(now / 1000 - nodes[active].start)) : 0
  const done = run?.state.tasks?.filter((t) => t.status === 'done').length ?? 0
  return <div className="app-shell">
    <nav className="rail"><a className="brand-mark" href="/" title="Orbit Mission Control"><Orbit size={26}/></a><div className="rail-divider"/><button className="rail-active" title="Mission control" onClick={() => setTab('office')}><LayoutDashboard size={20}/></button><button title="Live topology" onClick={() => setTab('graph')}><GitBranch size={20}/></button><button title="Mission history" onClick={() => void action(async () => setHistory(await api<Run[]>('/runs')))}><History size={20}/></button><button title="Inspect agent" onClick={() => setShowInspector(true)}><Users size={20}/></button><div className="rail-bottom"><button title="Quick guide" onClick={() => setHelp(true)}><CircleHelp size={19}/></button><button title="Settings" onClick={() => setShowSettings(true)}><Settings2 size={19}/></button><div className="user-avatar">YOU</div></div></nav>
    <div className="app-main">
      <header className="topbar"><div className="breadcrumb"><span>Workspace</span><span>/</span><strong>Mission control</strong><span className="workspace-badge">LOCAL</span></div><div className="telemetry"><span title="FastAPI :8000"><i className={health?.backend ? 'connected' : ''}/>LangGraph <small>:8000</small></span><span title={health?.opencode.error ?? health?.opencode.url}><i className={health?.opencode.connected ? 'connected' : ''}/>OpenCode <small>{health?.opencode.version ? `v${health.opencode.version}` : ':4096'}</small></span><button title="Connection settings" onClick={() => setShowSettings(true)}><Settings2 size={16}/></button></div></header>
      <main>
        <div className="page-heading"><div><div className="eyebrow">YOUR IDEAS. A TEAM IN MOTION.</div><h1>Mission control<span className="heading-dot">.</span></h1><p>One mission. Five agents. Everything in sync.</p></div><button className="outline-button" onClick={() => void action(async () => setHistory(await api<Run[]>('/runs')))}><History size={15}/>Mission history<ArrowRight size={14}/></button></div>
        <div className="stats-strip"><div><span className="stat-icon violet"><Users size={17}/></span><div><small>TEAM CAPACITY</small><strong>5 <span>specialists</span></strong></div><span className="stat-note">Ready to collaborate</span></div><div><span className="stat-icon mint"><Activity size={17}/></span><div><small>ACTIVE AGENTS</small><strong>{running && active ? '01' : '00'} <span>/ 05</span></strong></div><span className="stat-note">{active && running ? labels[active] : 'Standing by'}</span></div><div><span className="stat-icon blue"><Check size={17}/></span><div><small>TASKS COMPLETED</small><strong>{String(done).padStart(2, '0')} <span>/ {String(run?.state.tasks?.length ?? 0).padStart(2, '0')}</span></strong></div></div><div><span className="stat-icon amber"><Radio size={17}/></span><div><small>MISSION STATUS</small><strong className="status-value">{run?.status.replaceAll('_', ' ') ?? 'Awaiting mission'}</strong></div><span className={`status-indicator ${running ? 'pulsing' : ''}`}/></div></div>
        {error && <div className="error-banner"><span>{error}</span><button aria-label="Dismiss error" onClick={() => setError('')}><X size={16}/></button></div>}
        {run?.error && <div className="error-banner">{run.error}</div>}
        <div className="workspace-grid"><div className="stage-column"><section className="stage-panel"><div className="stage-header"><div className="view-tabs"><button className={tab === 'office' ? 'chosen' : ''} onClick={() => setTab('office')}><Box size={15}/>Virtual office</button><button className={tab === 'graph' ? 'chosen' : ''} onClick={() => setTab('graph')}><GitBranch size={15}/>Live topology</button></div><div className="stage-tools"><span><i className={stream && health?.opencode.stream ? 'tiny-dot' : 'tiny-dot idle'}/>{stream && health?.opencode.stream ? 'Live sync' : 'Standby'}</span><button title="Inspect current state" onClick={() => setShowInspector(true)}><Maximize2 size={14}/></button></div></div>{tab === 'office' ? <Office/> : <Topology onInspect={() => setShowInspector(true)}/>}<div className="stage-footer"><span><span className="tiny-dot" style={{ background: active ? colors[active] : '#64748b' }}/>{active && running ? `${labels[active]} is working · ${elapsed}s` : 'Your team is ready for the next mission'}</span><span>LANGGRAPH × OPENCODE</span></div></section>
        <div className="agent-roster">{roles.map((role, i) => <button key={role} className={selected === role ? 'roster-selected' : ''} onClick={() => { useMission.getState().select(role); setShowInspector(true) }}><div className="roster-avatar" style={{ color: colors[role], background: colors[role] + '14' }}>{['S', 'A', 'R', 'E', 'Q'][i]}<i className={active === role && running ? 'connected' : ''}/></div><strong>{labels[role]}</strong><span>{active === role && running ? 'Working' : nodes[role]?.end ? 'Completed' : 'Standby'}</span></button>)}</div>
        </div><aside><Terminals/><Kanban/></aside></div>
        {(!!run?.interrupts.length || !!run?.permissions.length) && <section className="approval-panel"><div className="approval-heading"><ShieldCheck size={18}/><strong>Human approval required</strong><span>Execution is paused at an approval boundary.</span></div>{!!run?.interrupts.length && <div className="approval-item"><details><summary>Review implementation plan</summary><pre>{JSON.stringify(run.interrupts, null, 2)}</pre></details><div><button disabled={busy} onClick={() => void action(() => api(`/runs/${runId}/resume`, { approved: false }))}>Reject</button><button className="approve" disabled={busy} onClick={() => void action(() => api(`/runs/${runId}/resume`, { approved: true }))}>Approve plan</button></div></div>}{run?.permissions.map((p) => <div className="approval-item" key={p.id}><code>{p.action}: {p.resources.join(', ')}</code><div><button disabled={busy} onClick={() => void action(() => api(`/runs/${runId}/permissions/${p.sessionID}/${p.id}`, { approved: false }))}>Reject</button><button className="approve" disabled={busy} onClick={() => void action(() => api(`/runs/${runId}/permissions/${p.sessionID}/${p.id}`, { approved: true }))}>Allow once</button></div></div>)}</section>}
        <section className="command-center"><div className="command-title"><span><Command size={15}/>MISSION DISPATCHER</span><span>Big ideas start with a single prompt.</span></div><textarea aria-label="Mission prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && !busy && !running) { e.preventDefault(); void dispatch() } }} placeholder="What should your team build today? Describe your mission…"/><div className="command-actions"><div className="command-options"><label className="mode-select"><ShieldCheck size={14}/><select aria-label="Execution mode" value={mode} onChange={(e) => setMode(e.target.value)}><option value="human">Human approval</option><option value="auto">Auto-run</option></select><ChevronDown size={12}/></label><label className="directory-input"><FolderOpen size={15}/><input aria-label="Absolute project directory" value={directory} onChange={(e) => setDirectory(e.target.value)} placeholder="Absolute project directory"/></label></div><div className="dispatch-actions">{run && ['failed', 'interrupted'].includes(run.status) && <button className="outline-button" disabled={busy} onClick={() => void action(() => api(`/runs/${runId}/resume`, { approved: true }))}>Resume</button>}{run && ['running', 'waiting_approval', 'interrupted'].includes(run.status) && <button className="stop-button" title="Cancel mission" disabled={busy} onClick={() => void action(() => api(`/runs/${runId}/cancel`, {}))}><Square size={13}/></button>}<span className="keyboard-hint">Ctrl ↵</span><button className="dispatch-button" disabled={busy || running || !health?.opencode.connected} onClick={() => void dispatch()}><Send size={15}/>{busy ? 'Connecting…' : 'Dispatch mission'}<ArrowRight size={15}/></button></div></div></section>
        <footer className="page-footer"><span><ShieldCheck size={12}/>Local execution. Persistent memory. Real results.</span><span>ORBIT / MISSION CONTROL <span className="version">v0.1</span></span></footer>
      </main>
    </div>
    {showSettings && <div className="modal-backdrop"><section className="modal"><div className="panel-title"><span>Engine connection</span><button onClick={() => setShowSettings(false)}><X size={18}/></button></div><p>OpenCode URL is configured in <code>.env</code>.</p><code className="config-url">{health?.opencode.url ?? 'http://127.0.0.1:4096'}</code><label>Basic Auth password<input type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)}/></label><p className="muted">Kept in backend memory only. For V2 Bearer authentication, set OPENCODE_SERVER_TOKEN in .env.</p><button className="dispatch-button" disabled={busy} onClick={() => void action(async () => { await api('/credentials', { password }); setPassword(''); setHealth(await api<Health>('/health')); setShowSettings(false) })}>Save connection<ArrowRight size={15}/></button></section></div>}
    {showInspector && <div className="modal-backdrop"><section className="modal inspector"><div className="panel-title"><span>{labels[selected]} · State inspector</span><button onClick={() => setShowInspector(false)}><X size={18}/></button></div><pre>{JSON.stringify(nodes[selected]?.data ?? run?.state ?? { status: 'No mission dispatched' }, null, 2)}</pre>{run && <button className="outline-button" onClick={() => void action(async () => setCheckpoints(await api(`/runs/${runId}/checkpoints`)))}><History size={14}/>Checkpoint history</button>}{checkpoints?.map((c) => <details key={c.id}><summary>{c.created}</summary><pre>{JSON.stringify(c.state, null, 2)}</pre></details>)}</section></div>}
    {history && <div className="modal-backdrop"><section className="modal"><div className="panel-title"><span>Mission history</span><button onClick={() => setHistory(null)}><X size={18}/></button></div>{history.length ? history.map((r) => <button className="history-row" key={r.id} onClick={() => { setRunId(r.id); setHistory(null); setCheckpoints(null) }}><span>{r.id.slice(0, 8)}<small>{new Date(r.created * 1000).toLocaleString()}</small></span><span>{r.status}<ArrowRight size={14}/></span></button>) : <p>No missions yet. Your next idea belongs here.</p>}</section></div>}
    {help && <div className="modal-backdrop"><section className="modal"><div className="panel-title"><span>Your team, connected</span><button onClick={() => setHelp(false)}><X size={18}/></button></div><p>1. Start OpenCode and verify both connection indicators.</p><p>2. Enter the absolute path to your project and describe your mission.</p><p>3. The supervisor plans, routes specialists and requests real tool execution. Approve requests when shown.</p><p>4. Inspect agents, terminal output and saved checkpoints. Copy a session ID from the terminal footer to open the same session in OpenCode.</p><p className="muted">Auto-run skips the plan approval. Mutations and unfamiliar shell commands still require tool-level approval. No separate LLM API key is needed.</p></section></div>}
  </div>
}
