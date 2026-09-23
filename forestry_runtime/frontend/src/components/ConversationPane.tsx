import { FormEvent, useEffect, useMemo, useRef } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { conversationRows } from '../eventViews'
import type { PendingUserMessage, Run, RuntimeEvent } from '../types'
import { terminalStates } from '../types'

type Props = {
  run: Run | null
  events: RuntimeEvent[]
  pending: PendingUserMessage[]
  notice: string
  prompt: string
  busy: boolean
  onPrompt: (value: string) => void
  onSend: (event: FormEvent) => void
  onControl: (action: 'pause' | 'cancel') => void
}

export function ConversationPane(props: Props) {
  const messages = useMemo(
    () => conversationRows(props.events, props.pending), [props.events, props.pending],
  )
  const end = useRef<HTMLDivElement | null>(null)
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [messages.length, props.pending.length])
  return <section className="conversation">
    <header className="topbar"><div><h1>Agent 对话</h1><p>界面只呈现 Runtime 返回的状态与事件</p></div>{props.run && <span className={`state ${props.run.state}`}>{props.run.state}{props.run.state_version !== undefined ? ` · v${props.run.state_version}` : ''}</span>}</header>
    <div className="messages">
      {!messages.length && <div className="empty"><strong>描述一个目标</strong><span>可使用 Workspace、已授权目录、沙盒代码与已启用工具。</span></div>}
      {messages.map((message, index) => <article key={index} className={`bubble ${message.role}`}>
        {message.thinking && <details><summary>模型返回的 thinking</summary><pre>{message.thinking}</pre></details>}
        {message.text && (message.role === 'assistant'
          ? <div className="answer markdown"><Markdown remarkPlugins={[remarkGfm]}>{message.text}</Markdown></div>
          : <div className="answer">{message.text}</div>)}
        {message.pending && <small className="delivery">正在提交…</small>}
      </article>)}
      <div ref={end} />
    </div>
    {props.notice && <div className="notice">{props.notice}</div>}
    <form className="composer" onSubmit={props.onSend}><textarea value={props.prompt} onChange={event => props.onPrompt(event.target.value)} placeholder={props.run && !terminalStates.has(props.run.state) ? '补充要求会排入下一个 Turn…' : '输入任务要求…'} />
      <div className="composerActions"><div>{props.run && !terminalStates.has(props.run.state) && <><button type="button" onClick={() => props.onControl('pause')}>暂停</button><button type="button" className="danger" onClick={() => props.onControl('cancel')}>取消</button></>}</div><button className="primary" disabled={props.busy || !props.prompt.trim()}>发送</button></div>
    </form>
  </section>
}
