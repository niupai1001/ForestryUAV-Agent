import { useMemo } from 'react'

import { traceRows } from '../eventViews'
import type { Asset, RuntimeEvent, Turn } from '../types'

type Props = { events: RuntimeEvent[]; turns: Turn[]; assets: Asset[]; chatId: string }

export function TracePane({ events, turns, assets, chatId }: Props) {
  const rows = useMemo(() => traceRows(events), [events])
  return <aside className="trace"><header><h2>真实行动流</h2><span>{events.length} events · {turns.length} turns</span></header>
    <div className="turns">{turns.map(turn => <span key={turn.id} title={turn.agent_run_id}>T{turn.ordinal} {turn.state} / {turn.checkpoint_state}</span>)}</div>
    <div className="traceList">{rows.map((item, index) => <article key={index} className={`traceCard ${item.type}`}><header><strong>{item.label}</strong><span>{item.state}</span></header>{item.diff && <section className="codeDiff"><small>{item.diff.path} · {String(item.diff.replacements || 0)} replacement(s)</small><pre className="removed">{item.diff.oldText.split('\n').map(line => `- ${line}`).join('\n')}</pre><pre className="added">{item.diff.newText.split('\n').map(line => `+ ${line}`).join('\n')}</pre></section>}{item.detail !== undefined && <details open={item.type === 'result' && item.state === 'failed'}><summary>详情</summary><pre>{typeof item.detail === 'string' ? item.detail : JSON.stringify(item.detail, null, 2)}</pre></details>}</article>)}</div>
    <section className="artifacts"><h3>产物</h3>{assets.length ? assets.map(asset => <a key={asset.id} href={`/files/${chatId}/${asset.id}`}><span>{asset.name}<small>{asset.artifact_kind || 'file'}{asset.sealed_at ? ' · 已封存' : ''}</small></span><span className={`verification ${asset.verification?.status || 'none'}`}>{asset.verification?.status ?? ''}<small>{asset.size.toLocaleString()} B</small></span></a>) : <span>暂无登记产物</span>}</section>
  </aside>
}
