import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { artifactCaption, conversationRows, type DeliveredArtifact } from '../eventViews'
import type { PendingUserMessage, Run, RuntimeEvent } from '../types'
import { terminalStates } from '../types'

type Props = {
  run: Run | null
  events: RuntimeEvent[]
  artifacts: DeliveredArtifact[]
  pending: PendingUserMessage[]
  notice: string
  prompt: string
  busy: boolean
  onPrompt: (value: string) => void
  onSend: (event: FormEvent) => void
  onControl: (action: 'pause' | 'cancel') => void
}

function Check({ label, value }: { label: string; value: boolean | null | undefined }) {
  const state = value === true ? 'pass' : value === false ? 'fail' : 'unknown'
  const text = value === true ? '通过' : value === false ? '未通过' : '未验证'
  return <li className={`artifactCheck ${state}`}><span>{label}</span><strong>{text}</strong></li>
}

function ArtifactCard({ item }: { item: DeliveredArtifact }) {
  const { delivery } = item
  const [failed, setFailed] = useState(false)
  const checks = delivery.checks
  const urls = delivery.urls
  const inline = typeof urls?.inline_url === 'string' ? urls.inline_url : ''
  const download = typeof urls?.download_url === 'string' ? urls.download_url : ''
  const showImage = checks?.browser_can_display === true && Boolean(inline) && !failed
  return <article className="artifactCard">
    <header>
      <strong>{delivery.name || delivery.asset_id || '未命名产物'}</strong>
      {item.tool && <small>{item.tool}</small>}
    </header>
    <p className="artifactCaption">{artifactCaption(delivery)}</p>
    {showImage && <img
      src={inline} alt={delivery.name} loading="lazy"
      onError={() => setFailed(true)}
    />}
    {failed && <p className="artifactFail">图片无法显示，请下载后查看。</p>}
    <ul className="artifactChecks">
      <Check label="文件存在" value={checks?.file_exists} />
      <Check label="服务端可读" value={checks?.server_can_read} />
      <Check label="浏览器可显示" value={checks?.browser_can_display} />
      <Check label="解答任务" value={checks?.answers_task} />
    </ul>
    {download && <a className="artifactDownload" href={download} download>下载</a>}
  </article>
}

export function ConversationPane(props: Props) {
  const messages = useMemo(
    () => conversationRows(props.events, props.pending), [props.events, props.pending],
  )
  const end = useRef<HTMLDivElement | null>(null)
  useEffect(
    () => { end.current?.scrollIntoView({ block: 'end' }) },
    [messages.length, props.pending.length, props.artifacts.length],
  )
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
      {props.artifacts.length > 0 && <section className="artifactList">
        <h3>Runtime 交付的产物</h3>
        {props.artifacts.map(item => <ArtifactCard key={item.delivery.asset_id} item={item} />)}
      </section>}
      <div ref={end} />
    </div>
    {props.notice && <div className="notice">{props.notice}</div>}
    <form className="composer" onSubmit={props.onSend}><textarea value={props.prompt} onChange={event => props.onPrompt(event.target.value)} placeholder={props.run && !terminalStates.has(props.run.state) ? '补充要求会排入下一个 Turn…' : '输入任务要求…'} />
      <div className="composerActions"><div>{props.run && !terminalStates.has(props.run.state) && <><button type="button" onClick={() => props.onControl('pause')}>暂停</button><button type="button" className="danger" onClick={() => props.onControl('cancel')}>取消</button></>}</div><button className="primary" disabled={props.busy || !props.prompt.trim()}>发送</button></div>
    </form>
  </section>
}
