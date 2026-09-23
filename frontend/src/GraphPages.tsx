import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowDownLeft, ArrowUpRight, ChevronRight, CircleHelp, Layers3, Network, Search, SlidersHorizontal, X } from 'lucide-react'
import { money, num, params, roles, useApi } from './api'
import type { Cluster, ClusterEdge, Graph, Node, Role } from './api'
import { ExportButton, GraphCanvas, Legend, NodeLink, NodePreview, PageHeading, Panel, State } from './components'

export function GraphPage() {
  const [url, setUrl] = useSearchParams()
  const [search, setSearch] = useState(url.get('gid') ?? '')
  const [mode, setMode] = useState(url.get('gid') ? 'ego' : 'top')
  const [limit, setLimit] = useState('60')
  const [role, setRole] = useState(url.get('role') ?? '')
  const [cluster, setCluster] = useState(url.get('cluster_id') ?? '')
  const [depth, setDepth] = useState('')
  const [seed, setSeed] = useState('')
  const [minAmount, setMinAmount] = useState('')
  const [maxAmount, setMaxAmount] = useState('')
  const [egoDepth, setEgoDepth] = useState('1')
  const [direction, setDirection] = useState('both')
  const [selected, setSelected] = useState<string | null>(null)
  const gid = url.get('gid')
  const graphPath = mode === 'ego' && gid ? `/nodes/${gid}/ego?${params({ depth: egoDepth, direction })}` : `/graph?${params({ mode: 'top', limit, role, cluster_id: cluster, depth, is_seed: seed, min_amount: minAmount, max_amount: maxAmount })}`
  const graph = useApi<Graph>(graphPath, mode !== 'clusters' && (mode !== 'ego' || !!gid))
  const clusterGraph = useApi<{ nodes: Cluster[]; edges: ClusterEdge[] }>('/clusters/graph', mode === 'clusters')
  const clusters = useApi<Cluster[]>('/clusters')
  const node = useApi<Node>(`/nodes/${selected}`, selected !== null)
  const navigate = useNavigate()
  const clearFilters = () => { setRole(''); setCluster(''); setDepth(''); setSeed(''); setMinAmount(''); setMaxAmount('') }
  return <><PageHeading eyebrow="ИССЛЕДОВАНИЕ СВЯЗЕЙ" title="Следуйте за движением денег" description="От общей структуры к конкретному клиенту. Каждое направление — наблюдаемый перевод."><ExportButton /></PageHeading>
    <div className="graph-toolbar"><div className="segmented">{[['top', 'Приоритетные узлы'], ['clusters', 'Карта кластеров'], ['ego', 'Связи клиента']].map(([value, label]) => <button key={value} className={mode === value ? 'selected' : ''} onClick={() => { setMode(value); setSelected(null) }}>{value === 'clusters' ? <Layers3 size={14} /> : <Network size={14} />}{label}</button>)}</div><form className="search-field" onSubmit={e => { e.preventDefault(); if (/^\d+$/.test(search.trim())) { setUrl({ gid: search.trim() }); setMode('ego'); setSelected(search.trim()) } }}><Search size={15} /><input aria-label="Найти gid на графе" placeholder="Введите gid" inputMode="numeric" pattern="[0-9]+" value={search} onChange={e => setSearch(e.target.value)} /><button type="submit">Найти<ArrowUpRight size={13} /></button></form></div>
    <div className="graph-workspace"><aside className="filter-panel"><div className="filter-heading"><SlidersHorizontal size={15} /><strong>Параметры графа</strong></div>
      {mode === 'top' && <><label>Основная роль<select value={role} onChange={e => setRole(e.target.value)}><option value="">Все роли</option>{Object.entries(roles).map(([key, value]) => <option value={key} key={key}>{value.label}</option>)}</select></label><label>Кластер<select value={cluster} onChange={e => setCluster(e.target.value)}><option value="">Все кластеры</option>{clusters.data?.map(c => <option value={c.cluster_id} key={c.cluster_id}>К{c.cluster_id} · {c.n_nodes} узлов</option>)}</select></label><div className="form-row"><label>Глубина<select value={depth} onChange={e => setDepth(e.target.value)}><option value="">Все</option>{[0, 1, 2, 3, 4].map(v => <option key={v}>{v}</option>)}</select></label><label>Seed<select value={seed} onChange={e => setSeed(e.target.value)}><option value="">Все</option><option value="true">Да</option><option value="false">Нет</option></select></label></div><label>Сумма связи, KZT<div className="amount-inputs"><input aria-label="Минимальная сумма связи" type="number" min="0" placeholder="От" value={minAmount} onChange={e => setMinAmount(e.target.value)} /><input aria-label="Максимальная сумма связи" type="number" min="0" placeholder="До" value={maxAmount} onChange={e => setMaxAmount(e.target.value)} /></div></label><label>Количество узлов<select value={limit} onChange={e => setLimit(e.target.value)}>{[30, 60, 100, 200].map(v => <option key={v} value={v}>Топ {v}</option>)}</select></label><button className="text-button reset-filter" onClick={clearFilters}><X size={13} />Сбросить фильтры</button></>}
      {mode === 'ego' && <><label>Глубина связей<select value={egoDepth} onChange={e => setEgoDepth(e.target.value)}>{[1, 2, 3].map(v => <option key={v} value={v}>{v} {v === 1 ? 'шаг' : 'шага'}</option>)}</select></label><label>Направление<select value={direction} onChange={e => setDirection(e.target.value)}><option value="both">Все связи</option><option value="in">Входящие</option><option value="out">Исходящие</option></select></label>{gid && <div className="ego-focus"><span>Центр исследования</span><strong className="mono">gid {gid}</strong><Link to={`/nodes/${gid}`}>Карточка клиента<ArrowUpRight size={13} /></Link></div>}</>}
      {mode === 'clusters' && <div className="filter-explanation"><Layers3 size={26} /><h3>Вся сеть в одном масштабе</h3><p>Каждый круг — сообщество клиентов. Размер отражает количество узлов, стрелки — межкластерные переводы.</p><p>Выберите кластер, чтобы изучить его структуру.</p></div>}
      <div className="filter-hint"><CircleHelp size={16} /><p>Выберите узел, чтобы подсветить соседей. Нажмите на фон, чтобы сбросить выделение.</p></div>
    </aside><Panel className="graph-main"><div className="graph-status"><span><i className="status-dot" />{mode === 'clusters' ? `${num(clusterGraph.data?.nodes.length)} кластеров` : `${num(graph.data?.nodes.length)} узлов · ${num(graph.data?.edges.length)} связей`}</span><span>{mode === 'ego' ? `Эго-сеть ${gid ?? '—'}` : mode === 'clusters' ? 'Агрегированная структура' : 'Выборка по приоритету'}</span></div>
      {mode === 'ego' && !gid ? <div className="state graph-start"><Search size={30} /><strong>С какого клиента начать?</strong><span>Введите gid в поле поиска, чтобы раскрыть его окружение.</span></div> : mode === 'clusters' ? <State loading={clusterGraph.isLoading} error={clusterGraph.error}><GraphCanvas clusters={clusterGraph.data} height={530} onCluster={id => navigate(`/clusters/${id}`)} /></State> : <State loading={graph.isLoading} error={graph.error} empty={graph.data?.nodes.length === 0}><GraphCanvas graph={graph.data} height={530} onNode={setSelected} /></State>}
      {mode !== 'clusters' && <Legend />}{graph.data?.truncated && mode !== 'clusters' && <div className="truncation">Показана ограниченная выборка. Уточните фильтры или откройте эго-сеть клиента.</div>}
    </Panel></div>{selected !== null && mode !== 'clusters' && <State loading={node.isLoading} error={node.error}>{node.data && <NodePreview node={node.data} />}</State>}
  </>
}

export function ClustersPage() {
  const { id } = useParams()
  const all = useApi<Cluster[]>('/clusters')
  const currentId = id ? Number(id) : all.data?.[0]?.cluster_id
  const cluster = all.data?.find(c => c.cluster_id === currentId)
  const graph = useApi<Graph>(`/graph?${params({ cluster_id: currentId, limit: 100 })}`, currentId !== undefined)
  const navigate = useNavigate()
  return <><PageHeading eyebrow="СТРУКТУРА СЕТИ" title="Кластеры и финансовые контуры" description="Сообщества связанных клиентов и объяснимые гипотезы об их взаимодействии."><ExportButton filename="clusters.csv" /></PageHeading><State loading={all.isLoading} error={all.error} empty={all.data?.length === 0}><div className="clusters-layout"><div className="cluster-list"><div className="cluster-list-head"><span>КЛАСТЕРЫ</span><span>{num(all.data?.length)}</span></div>{all.data?.map(c => <Link key={c.cluster_id} className={`cluster-list-item ${c.cluster_id === currentId ? 'active' : ''}`} to={`/clusters/${c.cluster_id}`}><div className="between"><strong><Layers3 size={16} />Кластер {c.cluster_id}</strong><ChevronRight size={15} /></div><p>{c.hypothesis}</p><div className="cluster-meta"><span>{num(c.n_nodes)} узлов</span><span>{num(c.n_seed)} seed</span><span>{money(c.sum_kzt_internal)}</span></div></Link>)}</div>
    {cluster ? <div className="cluster-detail"><Panel><div className="cluster-detail-title"><div className="eyebrow">ОБЪЯСНИМОЕ СООБЩЕСТВО</div><div className="between"><h2>Кластер {cluster.cluster_id}</h2><Link className="button secondary" to={`/graph?cluster_id=${cluster.cluster_id}`}>Исследовать<ArrowUpRight size={14} /></Link></div><p className="hypothesis"><span>Гипотеза</span>{cluster.hypothesis}</p></div><div className="cluster-stats"><div><strong>{num(cluster.n_nodes)}</strong><span>клиентов</span></div><div><strong>{num(cluster.n_seed)}</strong><span>seed-клиентов</span></div><div><strong>{money(cluster.sum_kzt_internal)}</strong><span>внутренний оборот</span></div></div><div className="cluster-flows"><span><ArrowDownLeft size={17} />Входящий поток<b>{money(cluster.inflow_external)}</b></span><span><ArrowUpRight size={17} />Исходящий поток<b>{money(cluster.outflow_external)}</b></span></div></Panel>
      <Panel title="Внутренняя структура" subtitle="До 100 узлов; связи направлены по движению средств"><State loading={graph.isLoading} error={graph.error}><GraphCanvas graph={graph.data} height={340} onNode={gid => navigate(`/nodes/${gid}`)} /></State><Legend compact /></Panel><div className="cluster-detail-bottom"><Panel title="Ключевые клиенты"><div className="key-nodes">{cluster.top_gids.map(gid => <NodeLink gid={gid} key={gid} />)}</div></Panel><Panel title="Распределение ролей"><div className="small-role-list">{Object.entries(cluster.roles).map(([role, count]) => <div key={role}><span><i style={{ background: roles[role as Role]?.color }} />{roles[role as Role]?.short ?? role}</span><b>{count}</b></div>)}</div></Panel></div></div> : <div className="state"><h3>Кластер не найден</h3><p>Выберите сообщество из списка.</p></div>}</div></State></>
}

