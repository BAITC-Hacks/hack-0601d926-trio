import { useQuery } from '@tanstack/react-query'

export type Role = 'consolidator' | 'transit' | 'distributor' | 'terminal' | 'coordinator' | 'peripheral'
export interface Node {
  gid: string; depth: number; is_seed: boolean; role: Role; role_score: number;
  role_scores: Record<Role, number>; cluster_id: number; priority_score: number;
  evidence: string; detailed_evidence: string | string[]; metrics: Record<string, number | boolean | null>;
  priority_factors: { key: string; label: string; value: number; weight: number; contribution: number }[];
  limitations: string[]; next_step: string;
}
export interface Edge { src: string; dst: string; sum_kzt: number; n_tx: number; depth?: number }
export interface ClusterEdge { src: number; dst: number; sum_kzt: number; n_tx: number }
export interface Graph { nodes: Node[]; edges: Edge[]; truncated: boolean }
export interface Cluster {
  cluster_id: number; n_nodes: number; n_seed: number; sum_kzt_internal: number;
  inflow_external: number; outflow_external: number; top_gids: string[];
  roles: Record<Role, number>; hypothesis: string;
}
export interface Overview {
  stats: { n_nodes: number; n_edges: number; n_transactions: number; total_volume: number; n_seed: number;
    n_components: number; n_clusters: number; n_boundary: number; n_isolated: number;
    period_start: string; period_end: string; roles: Record<Role, number> };
  source: string; warnings: string[]; duration_seconds: number; top_nodes: Node[];
  timeline?: { date: string; sum_kzt: number; n_tx: number }[];
}
export interface Methodology { limitations: string[]; roles: { role: Role; label: string; description: string }[];
  priority_weights: Record<string, number>; clustering: string; normalization: string; glossary: Record<string, string> }
export interface CopilotAnswer { answer: string; observations: string[]; hypotheses: string[]; limitations: string[];
  tools_used: { name: string; parameters: Record<string, unknown>; summary: string }[];
  links: { type: 'node' | 'cluster'; id: string | number; label: string }[]; mode: string }

export const roles: Record<Role, { label: string; short: string; color: string }> = {
  consolidator: { label: 'Консолидатор', short: 'Консолидация', color: '#a797ef' },
  transit: { label: 'Транзитный', short: 'Транзит', color: '#62b6e9' },
  distributor: { label: 'Распределитель', short: 'Распределение', color: '#eab871' },
  terminal: { label: 'Конечный получатель', short: 'Получатели', color: '#e8899f' },
  coordinator: { label: 'Кандидат в координаторы', short: 'Координация', color: '#60d6b0' },
  peripheral: { label: 'Периферийный', short: 'Периферия', color: '#8093a7' },
}
export const num = (n: number | null | undefined) => new Intl.NumberFormat('ru-RU').format(n ?? 0)
export function money(n: number | null | undefined, compact = true) {
  const amount = n ?? 0
  if (compact && Math.abs(amount) >= 1e9) return `${(amount / 1e9).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} млрд ₸`
  if (compact && Math.abs(amount) >= 1e6) return `${(amount / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} млн ₸`
  if (compact && Math.abs(amount) >= 1e3) return `${(amount / 1e3).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} тыс. ₸`
  return `${num(amount)} ₸`
}
export const pct = (n: number | undefined | null) => `${Math.round((n ?? 0) * 100)}%`
export const metric = (node: Node, key: string): number => Number(node.metrics?.[key] ?? 0)
export function params(values: Record<string, unknown>) {
  return new URLSearchParams(Object.entries(values).filter(([, v]) => v !== '' && v !== undefined && v !== null).map(([k, v]) => [k, String(v)])).toString()
}
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } })
  if (!response.ok) {
    let message = `Ошибка запроса (${response.status})`
    try { const body = await response.json(); message = typeof body.detail === 'string' ? body.detail : message } catch { /* Keep HTTP error when response is not JSON. */ }
    throw new Error(message)
  }
  const data = await response.json()
  // API keeps statistics flat; the view model groups them under stats.
  return (path === '/overview' ? { ...data, stats: data.stats ?? data } : data) as T
}
export const useApi = <T,>(path: string, enabled = true) => useQuery({ queryKey: [path], queryFn: () => api<T>(path), enabled })
