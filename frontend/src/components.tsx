import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { AlertTriangle, ArrowDownToLine, ArrowUpRight, Focus, LoaderCircle, Minus, Plus, RefreshCw, SearchX } from 'lucide-react'
import { Link } from 'react-router-dom'
import cytoscape from 'cytoscape'
import type { Core, ElementDefinition } from 'cytoscape'
import { money, num, pct, roles } from './api'
import type { Cluster, ClusterEdge, Graph, Node, Role } from './api'

export function RoleBadge({ role }: { role: Role }) {
  const item = roles[role] ?? roles.peripheral
  return <span className="role-badge" style={{ color: item.color }}><i style={{ background: item.color }} />{item.label}</span>
}
export function Score({ score, large = false }: { score: number; large?: boolean }) {
  return <span className={`score ${large ? 'large' : ''}`}><span>{score.toFixed(2)}</span><span className="score-track"><i style={{ width: pct(score) }} /></span></span>
}
export function State({ loading, error, empty, children }: { loading?: boolean; error?: Error | null; empty?: boolean; children: ReactNode }) {
  if (loading) return <div className="state"><LoaderCircle className="spin" size={26} /><strong>Загружаем наблюдения</strong><span>Собираем показатели и связи графа</span></div>
  if (error) return <div className="state"><AlertTriangle size={27} /><strong>Не удалось получить данные</strong><span>{error.message}</span><button className="button secondary" onClick={() => window.location.reload()}><RefreshCw size={15} />Повторить</button></div>
  if (empty) return <div className="state"><SearchX size={28} /><strong>По этим условиям узлов нет</strong><span>Измените фильтры или выберите другую глубину исследования.</span></div>
  return children
}
export function PageHeading({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children?: ReactNode }) {
  return <div className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{description}</p></div><div className="heading-actions">{children}</div></div>
}
export function Panel({ title, subtitle, action, children, className = '' }: { title?: string; subtitle?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>{title && <div className="panel-head"><div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>{action}</div>}{children}</section>
}
export function Legend({ compact = false }: { compact?: boolean }) {
  return <div className={`legend ${compact ? 'compact' : ''}`}>{Object.entries(roles).map(([key, value]) => <span key={key}><i style={{ background: value.color }} />{value.short}</span>)}<span><i className="seed-dot" />Seed</span></div>
}
export function ExportButton({ filename = 'nodes_roles.csv', label = 'Экспорт CSV' }: { filename?: string; label?: string }) {
  return <a className="button secondary" href={`/api/exports/${filename}`} download><ArrowDownToLine size={15} />{label}</a>
}
export function NodeLink({ gid }: { gid: string }) { return <Link className="node-link" to={`/nodes/${gid}`}><span className="mono">{gid}</span><ArrowUpRight size={13} /></Link> }

interface GraphCanvasProps { graph?: Graph; clusters?: { nodes: Cluster[]; edges: ClusterEdge[] }; onNode?: (id: string) => void; onCluster?: (id: number) => void; height?: number; mini?: boolean }
export function GraphCanvas({ graph, clusters, onNode, onCluster, height = 450, mini = false }: GraphCanvasProps) {
  const container = useRef<HTMLDivElement>(null)
  const cyRef = useRef<Core | null>(null)
  const callbackRef = useRef({ onNode, onCluster })
  callbackRef.current = { onNode, onCluster }
  const [tooltip, setTooltip] = useState<{ x: number; y: number; label: string; detail: string } | null>(null)
  useEffect(() => {
    if (!container.current) return
    const elements: ElementDefinition[] = []
    if (clusters) {
      clusters.nodes.forEach((c, i) => elements.push({ data: { id: `c${c.cluster_id}`, entityId: c.cluster_id, label: `К${c.cluster_id}`, color: ['#60d6b0', '#a797ef', '#62b6e9', '#eab871', '#e8899f'][i % 5], size: Math.min(62, 22 + Math.sqrt(c.n_nodes) * 4), border: c.n_seed ? 2 : 0, detail: `${num(c.n_nodes)} узлов · ${money(c.sum_kzt_internal)}` }, position: { x: Math.cos(i * 2.39996) * Math.sqrt(i + 1) * 45, y: Math.sin(i * 2.39996) * Math.sqrt(i + 1) * 45 } }))
      clusters.edges.forEach((e, i) => elements.push({ data: { id: `e${i}`, source: `c${e.src}`, target: `c${e.dst}`, amount: e.sum_kzt, weight: Math.min(4, 0.7 + Math.log1p(e.sum_kzt) / 7) } }))
    } else if (graph) {
      graph.nodes.forEach((n, i) => elements.push({ data: { id: n.gid, entityId: n.gid, label: n.gid.length > 8 ? `…${n.gid.slice(-6)}` : n.gid, color: roles[n.role]?.color ?? '#8093a7', size: 12 + n.priority_score * 30, border: n.is_seed ? 3 : 0, detail: `${roles[n.role]?.label} · приоритет ${n.priority_score.toFixed(2)}` }, position: { x: Math.cos(i * 2.39996) * Math.sqrt(i + 1) * 35, y: Math.sin(i * 2.39996) * Math.sqrt(i + 1) * 35 } }))
      graph.edges.forEach((e, i) => elements.push({ data: { id: `e${i}`, source: String(e.src), target: String(e.dst), amount: e.sum_kzt, weight: Math.min(4, 0.6 + Math.log1p(e.sum_kzt) / 10) } }))
    }
    const cy = cytoscape({ container: container.current, elements, minZoom: 0.12, maxZoom: 4,
      style: [
        { selector: 'node', style: { 'background-color': 'data(color)', 'width': 'data(size)', 'height': 'data(size)', 'label': 'data(label)', 'font-size': mini ? 8 : 10, 'color': '#c1cfda', 'text-valign': 'bottom', 'text-margin-y': 7, 'font-family': 'monospace', 'border-width': 'data(border)', 'border-color': '#effef6', 'border-opacity': 0.95, 'overlay-opacity': 0 } },
        { selector: 'edge', style: { 'width': 'data(weight)', 'line-color': '#385365', 'target-arrow-color': '#6a8899', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'arrow-scale': 0.7, 'opacity': 0.66 } },
        { selector: '.faded', style: { 'opacity': 0.12 } },
        { selector: '.highlight', style: { 'line-color': '#63d4b1', 'target-arrow-color': '#63d4b1', 'opacity': 1 } },
        { selector: 'node:selected', style: { 'border-color': '#fff', 'border-width': 4 } },
      ],
      layout: { name: 'cose', randomize: false, animate: false, nodeRepulsion: () => 13000, idealEdgeLength: () => 85, edgeElasticity: () => 90, gravity: 0.35, numIter: 500, padding: mini ? 35 : 55, componentSpacing: 75 },
    })
    cyRef.current = cy
    cy.on('tap', 'node', event => {
      const node = event.target
      cy.elements().removeClass('faded highlight')
      const neighborhood = node.closedNeighborhood()
      cy.elements().not(neighborhood).addClass('faded')
      neighborhood.edges().addClass('highlight')
      if (clusters) callbackRef.current.onCluster?.(node.data('entityId') as number)
      else callbackRef.current.onNode?.(node.data('entityId') as string)
    })
    cy.on('tap', event => { if (event.target === cy) cy.elements().removeClass('faded highlight') })
    cy.on('mouseover', 'node, edge', event => {
      const entity = event.target
      const point = event.renderedPosition ?? entity.renderedPosition()
      setTooltip({ x: point.x, y: point.y, label: entity.isNode() ? (clusters ? `Кластер ${entity.data('entityId')}` : `gid ${entity.data('entityId')}`) : 'Направление перевода', detail: entity.isNode() ? String(entity.data('detail')) : money(entity.data('amount') as number) })
    })
    cy.on('mouseout', 'node, edge', () => setTooltip(null))
    const observer = new ResizeObserver(() => { cy.resize(); cy.fit(undefined, mini ? 35 : 55) })
    observer.observe(container.current)
    return () => { observer.disconnect(); cy.destroy(); cyRef.current = null }
  }, [graph, clusters, mini])
  const count = clusters?.nodes.length ?? graph?.nodes.length ?? 0
  return <div className={`graph-canvas ${mini ? 'mini' : ''}`} style={{ height }}>
    <div className="graph-surface" ref={container} role="img" aria-label={`Интерактивный граф: ${num(count)} узлов. Выберите узел для просмотра связей.`} />
    {count === 0 && <div className="graph-empty">Нет связей для отображения</div>}
    <div className="graph-watermark">ГРАФ ДЕНЕГ <span> / </span> {clusters ? 'КЛАСТЕРЫ' : 'СВЯЗИ'}</div>
    <div className="graph-controls"><button title="Приблизить" onClick={() => cyRef.current?.zoom((cyRef.current?.zoom() ?? 1) * 1.25)}><Plus size={16} /></button><button title="Отдалить" onClick={() => cyRef.current?.zoom((cyRef.current?.zoom() ?? 1) / 1.25)}><Minus size={16} /></button><button title="Вписать граф" onClick={() => cyRef.current?.fit(undefined, 45)}><Focus size={16} /></button></div>
    {tooltip && <div className="graph-tooltip" style={{ left: Math.min(tooltip.x + 15, (container.current?.clientWidth ?? 500) - 250), top: Math.max(8, tooltip.y - 65) }}><strong>{tooltip.label}</strong><span>{tooltip.detail}</span></div>}
  </div>
}
export function NodePreview({ node }: { node: Node }) {
  return <div className="node-preview"><div className="between"><div><span className="eyebrow">ВЫБРАННЫЙ УЗЕЛ</span><h3>gid <span className="mono">{node.gid}</span></h3></div><Score score={node.priority_score} /></div><RoleBadge role={node.role} /><p>{node.evidence}</p><div className="preview-metrics"><span>Глубина <b>{node.depth}</b></span><span>Кластер <b>К{node.cluster_id}</b></span><span>Seed <b>{node.is_seed ? 'Да' : 'Нет'}</b></span></div><Link className="button primary" to={`/nodes/${node.gid}`}>Открыть карточку<ArrowUpRight size={15} /></Link></div>
}
