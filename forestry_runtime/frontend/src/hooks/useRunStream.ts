import { useEffect, useRef, useState } from 'react'

import { RequestError, request } from '../client'
import { eventText } from '../eventViews'
import type { Run, RuntimeEvent, Turn } from '../types'
import { terminalStates } from '../types'

type Callbacks = {
  onRun: (run: Run) => void
  onConfirmed: (ids: Set<string>) => void
  onArtifactsChanged: () => void
  onMissing: () => void
  onNotice: (message: string) => void
}

export function useRunStream(chatId: string, run: Run | null, epoch: number, callbacks: Callbacks) {
  const [events, setEvents] = useState<RuntimeEvent[]>([])
  const [turns, setTurns] = useState<Turn[]>([])
  const eventsRef = useRef<RuntimeEvent[]>([])
  const callbacksRef = useRef(callbacks)
  callbacksRef.current = callbacks

  useEffect(() => {
    eventsRef.current = []
    setEvents([])
    setTurns([])
  }, [chatId])

  useEffect(() => {
    if (!chatId || !run?.id || run.chat_id !== chatId) return
    let stopped = false
    let after = eventsRef.current.reduce((latest, event) => Math.max(latest, event.seq), 0)
    let connectionFailures = 0
    const poll = async () => {
      while (!stopped) {
        try {
          const page = await request<{ events: RuntimeEvent[]; next: number; has_more: boolean; run: Run }>(
            `/runs/${run.id}/events?after=${after}&limit=1000&wait_seconds=${after ? 2 : 0}`,
            chatId,
          )
          if (stopped) break
          if (connectionFailures) {
            connectionFailures = 0
            callbacksRef.current.onNotice('')
          }
          if (page.events.length) {
            after = page.next
            setEvents(previous => {
              const merged = new Map(previous.map(event => [event.seq, event]))
              for (const item of page.events) merged.set(item.seq, item)
              const next = [...merged.values()].sort((left, right) => left.seq - right.seq)
              eventsRef.current = next
              return next
            })
            const confirmed = new Set(
              page.events.filter(event => event.type === 'user_message')
                .map(event => eventText(event, 'input_id')).filter(Boolean),
            )
            if (confirmed.size) callbacksRef.current.onConfirmed(confirmed)
            if (page.events.some(event => event.type === 'done' || (
              ['job_status', 'job_reconciled'].includes(event.type) && event.terminal
            ))) callbacksRef.current.onArtifactsChanged()
          }
          callbacksRef.current.onRun(page.run)
          if (terminalStates.has(page.run.state) && !page.has_more && !page.events.length) {
            const data = await request<{ turns: Turn[] }>(`/runs/${run.id}/turns`, chatId)
            if (!stopped) setTurns(data.turns)
            break
          }
          if (!page.has_more && !page.events.length) {
            await new Promise(resolve => window.setTimeout(resolve, 250))
          }
        } catch (error) {
          if (stopped) break
          if (error instanceof RequestError && [404, 410].includes(error.status)) {
            callbacksRef.current.onNotice('该任务已被删除或清理，正在刷新任务列表。')
            callbacksRef.current.onMissing()
            break
          }
          if (!(error instanceof RequestError)) {
            connectionFailures += 1
            const delay = Math.min(1000 * 2 ** (connectionFailures - 1), 10000)
            callbacksRef.current.onNotice(`与 Runtime 的连接暂时中断，${Math.round(delay / 1000)} 秒后自动重连…`)
            await new Promise(resolve => window.setTimeout(resolve, delay))
            continue
          }
          callbacksRef.current.onNotice(error.message)
          break
        }
      }
    }
    request<{ turns: Turn[] }>(`/runs/${run.id}/turns`, chatId)
      .then(data => { if (!stopped) setTurns(data.turns) }).catch(() => undefined)
    void poll()
    return () => { stopped = true }
  }, [chatId, run?.id, epoch])

  return { events, turns }
}
