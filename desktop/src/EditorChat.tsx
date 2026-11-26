import { useEffect, useRef, useState } from 'react'
import type { ClipboardEvent, DragEvent, FormEvent, ReactNode } from 'react'
import type { EditorAttachment, EditorMessage, EditorProposal, EditorSelection } from './editorContract'
import type { ProviderId, ProviderStatus } from './settingsContract'

interface ChatProps {
  messages: EditorMessage[]
  proposals: EditorProposal[]
  source: string
  attachments: EditorAttachment[]
  selection?: EditorSelection
  prompt: string
  status: string
  pending?: EditorMessage
  busy: boolean
  hasPdfSelection: boolean
  agent?: ProviderId
  onAgent?: (agent: ProviderId | undefined) => void
  onPrompt: (value: string) => void
  onAttach: (files: File[]) => void
  onRemove: (id: string) => void
  onClearSelection: () => void
  onStop: () => void
  onSend: (selectionOnly: boolean) => void
  onApply: (proposal: EditorProposal) => void
  onDismiss: (proposal: EditorProposal) => void
  onCompare: (proposal: EditorProposal) => void
}

export function EditorChat({ messages, proposals, source, attachments, selection, prompt, status, pending, busy, hasPdfSelection, agent, onAgent, onPrompt, onAttach, onRemove, onClearSelection, onStop, onSend, onApply, onDismiss, onCompare }: ChatProps) {
  const list = useRef<HTMLDivElement>(null)
  const picker = useRef<HTMLInputElement>(null)
  const [lightbox, setLightbox] = useState<EditorAttachment>()
  const [selectionOnly, setSelectionOnly] = useState(true)
  const activeSelection = Boolean(selection || hasPdfSelection)
  const visibleMessages = pending ? [...messages, pending] : messages
  const empty = !visibleMessages.length && !proposals.length && !status

  useEffect(() => {
    const element = list.current
    if (element) element.scrollTop = element.scrollHeight
  }, [messages.length, pending?.id, status])

  function send(event: FormEvent) {
    event.preventDefault()
    if (!busy && (prompt.trim() || attachments.length)) onSend(selectionOnly && activeSelection)
  }

  function handlePaste(event: ClipboardEvent) {
    const files = Array.from(event.clipboardData.files).filter(file => file.type.startsWith('image/'))
    if (files.length > 0) {
      event.preventDefault()
      onAttach(files)
    }
  }

  function handleDrop(event: DragEvent) {
    const files = Array.from(event.dataTransfer.files).filter(file => file.type.startsWith('image/'))
    if (files.length > 0) {
      event.preventDefault()
      onAttach(files)
    }
  }

  return <section className={`wb-chat${empty ? ' wb-chat-empty' : ''}`} aria-label="题目聊天">
    <header className="wb-chat-header"><span className="wb-agent-dot" aria-hidden="true">✦</span><strong>题目助手</strong>{busy && <span className="wb-chat-progress" role="status">{status || '正在处理…'}</span>}</header>
    <div className="wb-chat-scroll" ref={list}>
      <div className="wb-chat-history">
        {visibleMessages.map(message => {
          const proposal = proposals.find(item => item.id === message.proposalId)
          return <article className={`wb-message wb-message-${message.role}`} key={message.id}>
            <div className="wb-message-label">{message.role === 'assistant' ? '题目助手' : '你'}</div>
            {(message.text || message.content) && <p>{message.text || message.content}</p>}
            {!!message.attachments?.length && <div className="wb-attachments">
              {message.attachments.map(attachment => <AttachmentCard key={attachment.id} item={attachment} onOpen={setLightbox} />)}
            </div>}
            {proposal && <ProposalCard item={proposal} source={source} busy={busy} onApply={onApply} onDismiss={onDismiss} onCompare={onCompare} />}
          </article>
        })}
        {proposals.filter(proposal => !visibleMessages.some(message => message.proposalId === proposal.id)).map(proposal => <ProposalCard key={proposal.id} item={proposal} source={source} busy={busy} onApply={onApply} onDismiss={onDismiss} onCompare={onCompare} />)}
        {!busy && status && <p className="wb-chat-result" role="status">{status}</p>}
      </div>
    </div>
    <form className="wb-chat-composer" onSubmit={send} onPaste={handlePaste} onDrop={handleDrop} onDragOver={event => {
      if (event.dataTransfer.types.includes('Files')) event.preventDefault()
    }}>
      {(!!attachments.length || selection) && <div className="wb-attachments wb-composer-attachments">
        {selection && <span className="wb-selection-chip">{selection.label}<button type="button" className="wb-icon" onClick={onClearSelection} aria-label="清除当前源码选段">×</button></span>}
        {attachments.map(item => <AttachmentCard key={item.id} item={item} onOpen={setLightbox} onRemove={onRemove} />)}
      </div>}
      <label className="wb-visually-hidden" htmlFor="wb-chat-prompt">修改要求或追问</label>
      <textarea id="wb-chat-prompt" value={prompt} placeholder={messages.length ? '继续追问或补充修改要求…' : '输入修改要求或直接粘贴参考图片…'} rows={2}
        onChange={event => onPrompt(event.target.value)} onKeyDown={event => {
          if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) send(event)
        }} />
      <div className="wb-compose-tools">
        <input type="file" ref={picker} accept="image/png,image/jpeg,image/webp" multiple hidden onChange={event => {
          onAttach(Array.from(event.target.files ?? [])); event.target.value = ''
        }} />
        <button type="button" className="wb-icon wb-plus" title="添加参考图片" aria-label="添加参考图片" onClick={() => picker.current?.click()}>+</button>
        {onAgent && <ChatAgentPicker agent={agent} disabled={busy} onAgent={onAgent} />}
        {activeSelection && <label className="wb-chat-scope"><input type="checkbox" checked={selectionOnly} onChange={event => setSelectionOnly(event.target.checked)} /> 仅修改所选区域</label>}
        {busy ? <button type="button" className="wb-btn wb-stop" onClick={onStop}><span aria-hidden="true">■</span> 停止</button>
          : <button type="submit" className="wb-send" aria-label="发送" title="发送（Enter）" disabled={!prompt.trim() && !attachments.length}>↑</button>}
      </div>
    </form>
    {lightbox && <EditorModal title={lightbox.title} onClose={() => setLightbox(undefined)}><img className="wb-lightbox-image" src={lightbox.dataUrl} alt={lightbox.title} /></EditorModal>}
  </section>
}

function ChatAgentPicker({ agent, disabled, onAgent }: { agent?: ProviderId; disabled: boolean; onAgent: (agent: ProviderId | undefined) => void }) {
  const [status, setStatus] = useState<ProviderStatus>()
  const [error, setError] = useState(false)
  const [open, setOpen] = useState(false)
  const trigger = useRef<HTMLButtonElement>(null)
  const picker = useRef<HTMLDivElement>(null)
  const label = status?.agents.find(item => item.id === agent)?.label ?? agent ?? '默认 Agent'

  useEffect(() => {
    if (!open) return
    let current = true
    const closeOutside = (event: PointerEvent) => { if (!picker.current?.contains(event.target as Node)) setOpen(false) }
    window.addEventListener('pointerdown', closeOutside, true)
    void window.utopia.call<ProviderStatus>('ui.providers').then(data => {
      if (current) { setStatus(data); setError(false) }
    }, () => { if (current) setError(true) })
    return () => { current = false; window.removeEventListener('pointerdown', closeOutside, true) }
  }, [open])

  function choose(id?: ProviderId) {
    setOpen(false)
    onAgent(id)
    trigger.current?.focus()
  }

  return <div className="wb-chat-agent" ref={picker} onKeyDown={event => {
    if (event.key === 'Escape' && open) { event.stopPropagation(); setOpen(false); trigger.current?.focus() }
  }} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false) }}>
    <button type="button" className="wb-chrome-btn" ref={trigger} aria-label="切换工作台 Agent" aria-expanded={open && !disabled} disabled={disabled} onClick={() => setOpen(value => !value)}>
      <span>{label}</span>⌄
    </button>
    {open && !disabled && <div className="wb-chrome-popover" aria-label="工作台 Agent">
      <button type="button" className="wb-chrome-btn" aria-pressed={agent === undefined} onClick={() => choose()}>默认 Agent</button>
      {status?.agents.filter(item => item.installed).map(item => <button type="button" key={item.id} className="wb-chrome-btn" aria-pressed={agent === item.id} onClick={() => choose(item.id)}>{item.label}</button>)}
      {error ? <span role="alert">读取失败，可在设置中检查连接。</span> : !status && <span role="status">正在检测本地 Agent…</span>}
    </div>}
  </div>
}

function ProposalCard({ item, source, busy, onApply, onDismiss, onCompare }: { item: EditorProposal; source: string; busy: boolean } & Pick<ChatProps, 'onApply' | 'onDismiss' | 'onCompare'>) {
  const labels: Record<string, string> = { pending: '待审阅', applied: '已采用', dismissed: '已放弃', undone: '已撤销' }
  const staleSolution = item.kind === 'solution' && item.status === 'pending' && item.baseSource !== source
  return <div className="wb-proposal">
    <div className="wb-proposal-title"><strong>{item.kind === 'solution' ? '补充答案与解析' : '修改提案'}</strong><span>{staleSolution ? '源码已变更，请重新补充' : labels[item.status] ?? item.status}</span></div>
    <div className="wb-proposal-actions">
      <button type="button" className="wb-btn" onClick={() => onCompare(item)}>查看前后对照</button>
      {item.status === 'pending' && <>
        <button type="button" className="wb-btn" disabled={busy} onClick={() => onDismiss(item)}>放弃</button>
        <button type="button" className="wb-btn wb-primary" disabled={busy || staleSolution} onClick={() => onApply(item)}>采用</button>
      </>}
    </div>
  </div>
}

function AttachmentCard({ item, onOpen, onRemove }: { item: EditorAttachment; onOpen: (item: EditorAttachment) => void; onRemove?: (id: string) => void }) {
  return <div className="wb-attachment">
    <button type="button" className="wb-attachment-preview" title={`查看 ${item.title}`} onClick={() => onOpen(item)}>
      <img src={item.dataUrl} alt="" /><span>{item.title}</span>
    </button>
    {onRemove && <button type="button" className="wb-icon wb-attachment-remove" onClick={() => onRemove(item.id)} aria-label={`移除 ${item.title}`}>×</button>}
  </div>
}

export function EditorModal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const modal = useRef<HTMLDialogElement>(null)
  useEffect(() => { modal.current?.showModal() }, [])
  return <dialog className="wb-modal" ref={modal} aria-label={title} onCancel={event => { event.preventDefault(); onClose() }} onClick={event => {
    if (event.target === event.currentTarget) onClose()
  }}>
    <div className="wb-modal-inner"><header><h2>{title}</h2><button type="button" className="wb-icon" onClick={onClose} aria-label="关闭">×</button></header>{children}</div>
  </dialog>
}
