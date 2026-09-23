import { useState } from 'react'
import { NavLink, Link, Route, Routes, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, ArrowRight, ArrowUpRight, BookOpen, Check, ChevronDown, ChevronRight, CircleHelp, Download, GitBranch, Layers3, LayoutDashboard, ListOrdered, Network, RefreshCw, Search, ShieldCheck, Sparkles } from 'lucide-react'
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis } from 'recharts'
import { api, money, num, roles, useApi } from './api'
import type { Overview as OverviewData, Graph } from './api'
import { ExportButton, GraphCanvas, Legend, NodeLink, PageHeading, Panel, RoleBadge, Score, State } from './components'
import { GraphPage, ClustersPage } from './GraphPages'
import { PrioritiesPage, NodePage, MethodologyPage } from './DataPages'
import { CopilotPage } from './CopilotPage'

const navigation = [
  { to: '/', icon: LayoutDashboard, label: 'Обзор', end: true },
  { to: '/graph', icon: Network, label: 'Исследование графа' },
  { to: '/priorities', icon: ListOrdered, label: 'Приоритеты' },
  { to: '/clusters', icon: Layers3, label: 'Кластеры' },
  { to: '/copilot', icon: Sparkles, label: 'AML Copilot' },
]
export default function App() {
  const [search, setSearch] = useState('')
  const [exportOpen, setExportOpen] = useState(false)
  const navigate = useNavigate()
  const overview = useApi<OverviewData>('/overview')
  const client = useQueryClient()
  const [polling, setPolling] = useState(false)
  const status = useQuery({ queryKey: ['recalculate-status'], queryFn: async () => {
    const value = await api<{ status: string; message: string; duration_seconds?: number }>('/recalculate/status')
    if (value.status !== 'running') { setPolling(false); await client.invalidateQueries({ predicate: query => query.queryKey[0] !== 'recalculate-status' }) }
    return value
  }, enabled: polling, refetchInterval: polling ? 1500 : false })
  const recalculate = useMutation({ mutationFn: () => api('/recalculate', { method: 'POST' }), onSuccess: () => setPolling(true) })
  const busy = recalculate.isPending || polling
  const source = overview.data?.source ?? ''
  const demo = /demo|synthetic|демо|синтет/i.test(source)
  const period = overview.data?.stats
  const formatDate = (s: string) => s ? new Date(`${s.slice(0, 10)}T00:00:00`).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '—'
  return <div className="app-layout">
    <aside className="sidebar">
      <Link className="brand" to="/"><span className="brand-mark"><GitBranch size={24} strokeWidth={1.7} /></span><span>Граф денег<small>АНАЛИТИЧЕСКОЕ ПРОСТРАНСТВО</small></span></Link>
      <div className="workspace"><span className="workspace-icon"><Network size={16} /></span><div>Исследование сети<small>Внутрибанковские переводы</small></div><ChevronDown size={13} /></div>
      <div className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <nav>{navigation.map(item => <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}><item.icon size={18} /><span>{item.label}</span>{item.to === '/copilot' && <span className="local-badge">LOCAL</span>}</NavLink>)}</nav>
      <div className="sidebar-bottom"><NavLink className="nav-item" to="/methodology"><BookOpen size={18} /><span>Методология</span></NavLink><div className="privacy-card"><ShieldCheck size={19} /><div><strong>Данные остаются здесь</strong><span>Локальная обработка<br />Без внешних сервисов</span></div></div><div className="analyst"><div className="avatar">AML</div><div>Рабочее место аналитика<small><i />Локальная сессия</small></div></div></div>
    </aside>
    <div className="main-shell">
      <header className="topbar"><div className="breadcrumb">Аналитика <ChevronRight size={13} /><span>Исследование сети</span></div><form className="global-search" onSubmit={event => { event.preventDefault(); if (search.trim()) navigate(`/priorities?q=${encodeURIComponent(search.trim())}`) }}><Search size={15} /><input aria-label="Поиск клиента по gid" value={search} onChange={event => setSearch(event.target.value)} placeholder="Найти клиента по gid" /><kbd>↵</kbd></form><span className="connection"><i />Система {overview.isError ? 'недоступна' : 'локальна'}</span><Link to="/methodology" className="help-link" aria-label="Справка"><CircleHelp size={19} /></Link></header>
      <div className="dataset-bar"><div><span className={`source-tag ${demo ? 'demo' : ''}`}><i />{demo ? 'ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ' : 'НАБЛЮДАЕМАЯ СЕТЬ'}</span><span className="dataset-period">{period ? `${formatDate(period.period_start)} — ${formatDate(period.period_end)}` : 'Подключение к данным'}</span></div><div className="dataset-actions"><span className="muted">{overview.data ? `${num(overview.data.stats.n_nodes)} клиентов` : 'parquet'}</span><button className="text-button" disabled={busy} onClick={() => recalculate.mutate()}><RefreshCw size={13} className={busy ? 'spin' : ''} />{busy ? 'Пересчитываем…' : 'Пересчитать'}</button><div className="export-menu"><button className="text-button" onClick={() => setExportOpen(!exportOpen)}><Download size={13} />Экспорт<ChevronDown size={12} /></button>{exportOpen && <div className="dropdown">{[['nodes_roles.csv', 'Все узлы и роли'], ['top_nodes.csv', 'Приоритетные узлы'], ['clusters.csv', 'Кластеры']].map(([file, label]) => <a key={file} href={`/api/exports/${file}`} download onClick={() => setExportOpen(false)}>{label}<Download size={13} /></a>)}</div>}</div></div>
      </div>
      {(recalculate.error || status.data?.status === 'failed') && <div className="inline-error">{recalculate.error?.message ?? status.data?.message}</div>}
      {status.data?.status === 'completed' && !busy && <div className="calculation-complete"><Check size={14} />Расчёт обновлён. {status.data.duration_seconds?.toFixed(2)} с</div>}
      {demo && <div className="demo-banner">Демонстрационный набор: результаты предназначены для проверки интерфейса и не относятся к реальным клиентам.</div>}
      <main><Routes><Route path="/" element={<Overview />} /><Route path="/graph" element={<GraphPage />} /><Route path="/priorities" element={<PrioritiesPage />} /><Route path="/nodes/:gid" element={<NodePage />} /><Route path="/clusters" element={<ClustersPage />} /><Route path="/clusters/:id" element={<ClustersPage />} /><Route path="/copilot" element={<CopilotPage />} /><Route path="/methodology" element={<MethodologyPage />} /><Route path="*" element={<div className="state"><h1>Страница не найдена</h1><Link to="/" className="button primary">Перейти к обзору</Link></div>} /></Routes></main>
      <footer><span><ShieldCheck size={12} />Результаты — аналитические гипотезы, требующие проверки.</span><span>Граф денег<span className="footer-divider">/</span>AML Research Workspace</span></footer>
    </div>
  </div>
}

function Overview() {
  const query = useApi<OverviewData>('/overview')
  const graph = useApi<Graph>('/graph?mode=top&limit=45')
  const navigate = useNavigate()
  const data = query.data
  const stats = data?.stats
  return <><PageHeading eyebrow="ОТ НАБЛЮДЕНИЙ К ГИПОТЕЗАМ" title="Вся сеть. Ясные приоритеты." description="Находите значимые связи и понимайте, с кого начать проверку."><Link className="button primary" to="/graph"><Network size={16} />Исследовать граф<ArrowUpRight size={15} /></Link></PageHeading>
    <State loading={query.isLoading} error={query.error}>{data && stats && <>
      <div className="stat-grid"><Stat label="Клиенты в сети" value={num(stats.n_nodes)} sub={`${num(stats.n_seed)} исходных seed-клиентов`} icon={<Network size={17} />} /><Stat label="Наблюдаемый оборот" value={money(stats.total_volume)} sub="По представленным переводам" icon={<Activity size={17} />} /><Stat label="Переводы и связи" value={num(stats.n_transactions)} sub={`${num(stats.n_edges)} направленных связей`} icon={<GitBranch size={17} />} /><Stat label="Структура сети" value={num(stats.n_clusters)} unit="кластеров" sub={`${num(stats.n_components)} несвязанных компонент`} icon={<Layers3 size={17} />} /></div>
      <div className="overview-main"><Panel title="Карта финансовых связей" subtitle="45 приоритетных узлов и наблюдаемые связи между ними" action={<Link className="panel-link" to="/graph">Открыть граф<ArrowUpRight size={14} /></Link>} className="network-panel"><State loading={graph.isLoading} error={graph.error}><GraphCanvas graph={graph.data} height={345} mini onNode={gid => navigate(`/nodes/${gid}`)} /></State><Legend compact /><div className="graph-footnote"><span><i className="status-dot" />Направление стрелок — движение средств</span><span>Размер узла — приоритет</span></div></Panel>
      <Panel title="Роли в наблюдаемой сети" subtitle="Структурные признаки, а не установленные факты" className="roles-panel"><div className="role-total"><strong>{num(stats.n_nodes)}</strong><span>клиентов распределено по ролям</span></div><div className="role-stack">{Object.entries(roles).map(([role, value]) => <div key={role} title={`${value.label}: ${stats.roles[role as keyof typeof roles] ?? 0}`} style={{ background: value.color, flex: stats.roles[role as keyof typeof roles] || 0.001 }} />)}</div><div className="role-distribution">{Object.entries(roles).map(([role, value]) => <Link to={`/priorities?role=${role}`} key={role}><span><i style={{ background: value.color }} />{value.short}</span><span><b>{num(stats.roles[role as keyof typeof roles])}</b><em>{((stats.roles[role as keyof typeof roles] ?? 0) / Math.max(stats.n_nodes, 1) * 100).toFixed(1)}%</em></span></Link>)}</div><Link to="/methodology" className="method-link">Как определяются роли<ArrowRight size={13} /></Link></Panel></div>
      <div className="overview-bottom"><Panel title="С кого начать проверку" subtitle="Узлы с наиболее выраженными признаками структурного влияния" action={<Link className="panel-link" to="/priorities">Все приоритеты<ArrowUpRight size={14} /></Link>}><div className="table-wrap"><table className="data-table overview-table"><thead><tr><th>#</th><th>Клиент / gid</th><th>Предполагаемая роль</th><th>Приоритет</th><th>Основание для проверки</th><th /></tr></thead><tbody>{data.top_nodes.slice(0, 5).map((node, index) => <tr key={node.gid}><td className="muted">{String(index + 1).padStart(2, '0')}</td><td><NodeLink gid={node.gid} />{node.is_seed && <span className="seed-label">seed</span>}</td><td><RoleBadge role={node.role} /></td><td><Score score={node.priority_score} /></td><td className="evidence-cell">{node.evidence}</td><td><Link aria-label={`Открыть узел ${node.gid}`} to={`/nodes/${node.gid}`}><ChevronRight size={15} /></Link></td></tr>)}</tbody></table></div><div className="table-foot"><span>Приоритет показывает порядок проверки, а не вероятность нарушения.</span><ExportButton filename="top_nodes.csv" label="Скачать топ" /></div></Panel></div>
      <div className="overview-lower"><Panel title="Динамика переводов" subtitle="Наблюдаемый объём по дням"><div className="timeline-chart">{data.timeline?.length ? <ResponsiveContainer width="100%" height={175}><AreaChart data={data.timeline} margin={{ top: 12, right: 20, bottom: 0, left: 15 }}><defs><linearGradient id="volumeGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#60d6b0" stopOpacity={0.25} /><stop offset="100%" stopColor="#60d6b0" stopOpacity={0} /></linearGradient></defs><XAxis dataKey="date" tickFormatter={v => String(v).slice(8, 10)} tick={{ fill: '#728392', fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={20} /><Tooltip contentStyle={{ background: '#17232d', border: '1px solid #344653', borderRadius: 8, color: '#e6eef4' }} formatter={v => [money(Number(v)), 'Объём']} /><Area type="monotone" dataKey="sum_kzt" stroke="#60d6b0" strokeWidth={2} fill="url(#volumeGradient)" /></AreaChart></ResponsiveContainer> : <div className="muted empty-chart">Временной ряд недоступен для этого набора.</div>}</div></Panel><Panel title="Контекст наблюдения" subtitle="Учитывайте при интерпретации результатов"><div className="quality-facts"><div><span className="quality-number">{num(stats.n_boundary)}</span><span>узлов на границе наблюдения<small>Отсутствие исходящих связей не доказывает удержание</small></span></div><div><span className="quality-number">{num(stats.n_isolated)}</span><span>изолированных клиентов<small>Сохранены в расчёте и доступны в поиске</small></span></div></div><div className="quality-warning"><CircleHelp size={16} /><span>{data.warnings[0] ?? 'Доступны только внутрибанковские переводы от 5 000 ₸. Полный баланс клиента неизвестен.'}</span></div><Link className="method-link" to="/methodology">Все ограничения данных<ArrowRight size={13} /></Link></Panel></div>
      <Link className="copilot-invite" to="/copilot"><span className="copilot-symbol"><Sparkles size={22} /></span><div><strong>Следующий вопрос — к AML Copilot</strong><p>Уточните связи, разберите приоритет или найдите транзитные цепочки.</p></div><span>Начать исследование<ArrowRight size={16} /></span></Link>
    </>}</State></>
}
function Stat({ label, value, sub, unit, icon }: { label: string; value: string; sub: string; unit?: string; icon: React.ReactNode }) {
  return <div className="stat-card"><div className="between"><span>{label}</span>{icon}</div><div className="stat-value">{value}{unit && <small>{unit}</small>}</div><p>{sub}</p></div>
}

