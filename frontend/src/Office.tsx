import { motion } from 'framer-motion'
import { Background, Controls, ReactFlow, MarkerType } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { BookOpen, Code2, Coffee, Layers3, ShieldCheck, Workflow } from 'lucide-react'
import { colors, labels, roles, useMission } from './store'

const positions: Record<string, { x: number; y: number }> = {
  supervisor: { x: 48, y: 29 }, planner: { x: 20, y: 48 }, researcher: { x: 78, y: 46 },
  coder: { x: 32, y: 76 }, reviewer: { x: 69, y: 76 },
}
const icons = { supervisor: Workflow, planner: Layers3, researcher: BookOpen, coder: Code2, reviewer: ShieldCheck }

export function Office() {
  const { active, selected, select, run, logs } = useMission()
  const working = run?.status === 'running'
  return <div className="office">
    <div className="office-grid" />
    <div className="office-caption"><span className="tiny-dot" /> LIVE WORKSPACE <span>01 / ENGINEERING FLOOR</span></div>
    <div className="room room-planning"><span>01 — STRATEGY</span><div className="whiteboard"><i/><i/><i/><i/></div></div>
    <div className="room room-library"><span>02 — KNOWLEDGE</span><div className="bookshelf">{Array.from({ length: 13 }, (_, i) => <i key={i}/>)}</div></div>
    <div className="room room-build"><span>03 — DEVELOPMENT</span></div>
    <div className="room room-test"><span>04 — QUALITY LAB</span></div>
    <div className="meeting-table"><div/><div/><div/><div/><Workflow size={26}/></div>
    <div className="lounge"><Coffee size={18}/><span>RECHARGE ZONE</span><div className="sofa"/></div>
    <div className="plant plant-one">✳</div><div className="plant plant-two">✳</div>
    {roles.map((role, index) => {
      const pos = positions[role]
      const Icon = icons[role]
      const isActive = active === role && working
      const latestTool = logs.filter((entry) => entry.payload.role === role &&
        (entry.payload.event as { type?: string } | undefined)?.type === 'session.tool.input.started').at(-1)
      const tool = (latestTool?.payload.event as { data?: { tool?: string; name?: string } } | undefined)?.data
      const activity = run?.permissions.some((p) => run.sessions.some((s) => s.session_id === p.sessionID && s.role === role))
        ? 'Waiting approval' : tool?.tool ?? tool?.name ?? (role === 'coder' ? 'Implementing' : role === 'reviewer' ? 'Verifying' : 'Working')
      return <div key={role}>
        <div className={`desk desk-${role} ${isActive ? 'desk-active' : ''}`} style={{ left: `${pos.x}%`, top: `${pos.y}%`, '--agent': colors[role] } as React.CSSProperties}>
          <div className="monitor"><Code2 size={14}/><i/><i/></div><div className="keyboard"/><div className="mug"/>
        </div>
        <motion.button className={`agent ${selected === role ? 'agent-selected' : ''}`} aria-label={`Inspect ${labels[role]}`}
          style={{ '--agent': colors[role] } as React.CSSProperties}
          initial={false} animate={{ left: `${isActive ? pos.x : pos.x + (index % 2 ? -8 : 7)}%`, top: `${isActive ? pos.y + 9 : pos.y + 15}%` }}
          transition={{ type: 'spring', stiffness: 45, damping: 16 }} onClick={() => select(role)}>
          {isActive && <motion.div className="speech" initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }}><span className="tiny-dot"/>{activity}<span className="ellipsis">...</span></motion.div>}
          <div className="avatar"><div className="avatar-hair"/><div className="avatar-face"><i/><i/></div><div className="avatar-shirt"><Icon size={12}/></div></div>
          <span className="agent-name">{labels[role]}<i className={isActive ? 'is-active' : ''}/></span>
        </motion.button>
      </div>
    })}
    <div className="office-legend"><span><i className="tiny-dot"/>Active</span><span><i className="tiny-dot idle"/>Standby</span><span>Click an agent to inspect</span></div>
    <div className="floor-label">ORBIT <span>HQ</span></div>
  </div>
}

const graphPositions: Record<string, { x: number; y: number }> = {
  supervisor: { x: 260, y: 20 }, planner: { x: 20, y: 165 }, researcher: { x: 500, y: 165 },
  approval: { x: 260, y: 280 }, coder: { x: 70, y: 410 }, reviewer: { x: 460, y: 410 },
}
export function Topology({ onInspect }: { onInspect?: () => void }) {
  const { active, nodes: states, select, run } = useMission()
  const nodes = Object.entries(graphPositions).map(([id, position]) => ({
    id, position, data: { label: <div><span className="node-eyebrow">{id === 'approval' ? 'INTERRUPT' : 'LANGGRAPH NODE'}</span><strong>{labels[id]}</strong><small>{active === id ? '● Running' : states[id]?.end ? `${((states[id].end! - states[id].start)).toFixed(1)}s · Complete` : 'Standby'}</small></div> },
    className: active === id ? 'graph-node graph-active' : 'graph-node',
    style: { borderColor: colors[id], '--agent': colors[id] } as React.CSSProperties,
  }))
  const pairs = [['supervisor', 'planner'], ['supervisor', 'researcher'], ['supervisor', 'approval'], ['approval', 'coder'], ['coder', 'supervisor'], ['supervisor', 'reviewer'], ['reviewer', 'supervisor']]
  const edges = pairs.map(([source, target]) => ({ id: source + target, source, target,
    animated: active === target, label: active === target ? `${run?.state.tasks?.length ?? 0} tasks` : undefined,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#506174' }, style: { stroke: active === target ? colors[target] : '#344254' },
  }))
  return <div className="topology"><ReactFlow nodes={nodes} edges={edges} fitView colorMode="dark" nodesDraggable={false}
    onNodeClick={(_, node) => { select(node.id); onInspect?.() }} minZoom={0.4}><Background color="#2a3543" gap={24}/><Controls/></ReactFlow></div>
}
