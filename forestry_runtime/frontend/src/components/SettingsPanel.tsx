/**
 * Runtime settings, edited from the workbench.
 *
 * These values used to be reachable only by grepping for `os.getenv` and then
 * working out whether the number came from the container environment, the
 * deployment `.env`, or a default literal in the code. The model-call limit that
 * ended a Run was one of them, and nothing in the product could report it.
 *
 * So each row shows not just the value but where it came from, and the panel says
 * whether a change applies to the next Run or needs a restart. Clearing a field
 * removes the override, which is how a value returns to whatever the deployment
 * declared -- that is a different action from typing the deployment's number in by
 * hand, and only the first one keeps working when the deployment changes.
 */
import { useCallback, useEffect, useState } from 'react'
import { RequestError, putBody, request } from '../client'
import type { SettingsGroup, SettingsPanelData, SettingsRow, SettingsSaveResult } from '../types'

function label(source: SettingsRow['source']): string {
  if (source === 'override') return '界面设置'
  if (source === 'env') return '环境变量'
  return '默认值'
}

function applies(row: SettingsRow): string {
  return row.applies === 'restart_required' ? '需重启进程' : '下一个 Run 生效'
}

export function SettingsPanel({ chatId }: { chatId?: string }) {
  const [data, setData] = useState<SettingsPanelData | null>(null)
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    setError('')
    try {
      const payload = await request<SettingsPanelData>('/settings', chatId)
      setData(payload)
      setDrafts({})
    } catch (failure) {
      setError(failure instanceof RequestError ? failure.message : String(failure))
    }
  }, [chatId])

  useEffect(() => { void load() }, [load])

  const edited = Object.keys(drafts).length

  const save = async () => {
    if (!edited) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const payload = await request<SettingsSaveResult>('/settings', chatId, putBody({ settings: drafts }))
      setData(payload)
      setDrafts({})
      const restart = payload.applied.filter(entry => entry.applies === 'restart_required')
      setNotice(
        restart.length
          ? `已保存。${restart.map(entry => entry.key).join('、')} 需要重启 Runtime 进程后才生效。`
          : `已保存 ${payload.applied.length} 项；下一个 Run 生效。`,
      )
    } catch (failure) {
      setError(failure instanceof RequestError ? failure.message : String(failure))
    } finally {
      setBusy(false)
    }
  }

  const renderField = (row: SettingsRow) => {
    const current = drafts[row.key] ?? row.value
    const changed = row.key in drafts
    if (row.kind === 'bool') {
      return (
        <select
          value={current}
          onChange={event => setDrafts({ ...drafts, [row.key]: event.target.value })}
        >
          <option value="true">启用</option>
          <option value="false">停用</option>
        </select>
      )
    }
    return (
      <input
        value={current}
        inputMode={row.kind === 'int' || row.kind === 'float' ? 'decimal' : 'text'}
        placeholder={row.kind === 'text' ? row.default : undefined}
        onChange={event => setDrafts({ ...drafts, [row.key]: event.target.value })}
      />
    )
  }

  return (
    <section className="settingsSection">
      <div className="settingsHead">
        <h3>运行设置</h3>
        <button onClick={() => void load()} disabled={busy}>刷新</button>
      </div>

      {error && <p className="settingsError">{error}</p>}
      {notice && <p className="settingsNotice">{notice}</p>}

      {data?.groups.map((group: SettingsGroup) => (
        <details key={group.name} className="settingsGroup">
          <summary>{group.name} <small>{group.settings.length}</small></summary>
          {group.settings.map(row => (
            <div key={row.key} className={row.key in drafts ? 'settingsRow edited' : 'settingsRow'}>
              <div className="settingsLabel">
                <code>{row.key}</code>
                <span className={`settingsSource source-${row.source}`}>{label(row.source)}</span>
              </div>
              <div className="inline">{renderField(row)}</div>
              {row.help && <p className="settingsHelp">{row.help}</p>}
              <p className="settingsMeta">
                默认 {row.default}
                {row.minimum !== null && row.maximum !== null
                  ? ` · 范围 ${row.minimum}–${row.maximum}`
                  : ''}
                {' · '}{applies(row)}
                {row.source === 'override' ? ' · 清空可恢复部署值' : ''}
              </p>
            </div>
          ))}
        </details>
      ))}

      <div className="sectionActions">
        <button disabled={!edited || busy} onClick={() => void save()}>
          {busy ? '保存中…' : edited ? `保存 ${edited} 项` : '保存'}
        </button>
        {edited > 0 && <button disabled={busy} onClick={() => setDrafts({})}>放弃改动</button>}
      </div>
      {data && data.override_count > 0 && (
        <p className="settingsMeta">当前有 {data.override_count} 项被界面覆盖。</p>
      )}
    </section>
  )
}
