import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties, KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import type { LocalAgent, ModelCatalog, ModelChoice, ProviderStatus } from './settingsContract'
import './model-picker.css'

const antigravityOrchestratorModels: NonNullable<ModelCatalog['agy']> = [
  { id: 'gemini-pro-orchestrator-neon', name: 'Orchestrator (Neon)' },
  { id: 'gemini-pro-orchestrator', name: 'Orchestrator (Carbon)' },
  { id: 'gemini-pro-orchestrator-argon', name: 'Orchestrator (Argon)' },
]

export default function ModelPicker({ value, onChange, disabled, images }: {
  value?: ModelChoice
  onChange: (choice: ModelChoice | undefined) => void
  disabled?: boolean
  images?: boolean
}) {
  const id = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const popup = useRef<HTMLDivElement>(null)
  const search = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<CSSProperties>()
  const [status, setStatus] = useState<ProviderStatus>()
  const [catalog, setCatalog] = useState<ModelCatalog>()
  const [connectionError, setConnectionError] = useState(false)
  const [catalogError, setCatalogError] = useState(false)
  const [query, setQuery] = useState('')
  const [customAgent, setCustomAgent] = useState<LocalAgent>()
  const [customModel, setCustomModel] = useState('')
  const active = open && !disabled
  const automatic = images ? '自动选择模型' : '默认模型'
  const title = images ? '选择图片识别模型' : '选择修订模型'
  const agentName = status?.agents.find(agent => agent.id === value?.agent)?.label ?? value?.agent
  const modelName = value?.modelName ?? catalog?.[value?.agent ?? 'codex']?.find(model => model.id === value?.model)?.name ?? value?.model
  const display = modelName ?? (agentName ? `${agentName} · 默认模型` : automatic)

  useLayoutEffect(() => {
    if (!active) return
    function place() {
      const bounds = trigger.current?.getBoundingClientRect()
      if (!bounds) return
      const width = Math.min(316, innerWidth - 20)
      const above = bounds.top > innerHeight - bounds.bottom && (!images || innerHeight - bounds.bottom < 320)
      const space = above ? bounds.top : innerHeight - bounds.bottom
      setPosition({
        left: Math.max(10, Math.min(bounds.left, innerWidth - width - 10)), width,
        maxHeight: Math.max(88, Math.min(390, space - 16)),
        ...(above ? { bottom: innerHeight - bounds.top + 6 } : { top: bounds.bottom + 6 }),
      })
    }
    place()
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    return () => {
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
    }
  }, [active, images])

  useLayoutEffect(() => { if (active && !customAgent) search.current?.focus({ preventScroll: true }) }, [active, customAgent])

  useEffect(() => {
    if (!active) return
    let current = true
    let refresh: number | undefined
    function outside(event: PointerEvent | FocusEvent) {
      const target = event.target as Node
      if (!trigger.current?.contains(target) && !popup.current?.contains(target)) setOpen(false)
    }
    window.addEventListener('pointerdown', outside, true)
    window.addEventListener('focusin', outside, true)
    void window.utopia.call<ProviderStatus>('ui.providers').then(result => {
      if (current) { setStatus(result); setConnectionError(false) }
    }, () => { if (current) setConnectionError(true) })
    async function readModels() {
      try {
        const result = await window.utopia.papilotModels?.()
        if (!current) return
        setCatalog(result ?? {})
        if (result) refresh = window.setTimeout(readModels, 2000)
      } catch {
        if (current) setCatalogError(true)
      }
    }
    void readModels()
    return () => {
      current = false
      window.clearTimeout(refresh)
      window.removeEventListener('pointerdown', outside, true)
      window.removeEventListener('focusin', outside, true)
    }
  }, [active])

  function close() {
    setOpen(false)
    trigger.current?.focus()
  }

  function choose(choice?: ModelChoice) {
    onChange(choice)
    close()
  }

  function keyboard(event: KeyboardEvent) {
    if (event.key === 'Escape' && !event.nativeEvent.isComposing) {
      event.preventDefault()
      event.stopPropagation()
      close()
    }
  }

  const term = query.trim().toLocaleLowerCase()
  const buttons = status?.agents.filter(agent => agent.installed && (!images || agent.images)).map(agent => {
    const matchesAgent = `${agent.label} ${agent.id}`.toLocaleLowerCase().includes(term)
    const listed = catalog?.[agent.id] ?? []
    const fallbacks = images && agent.id === 'agy'
      ? antigravityOrchestratorModels.filter(model => !listed.some(item => item.id === model.id))
      : []
    const models = [...listed, ...fallbacks].filter(model => !images || model.images !== false)
    const matches = agent.modelSelection ? models.filter(model => matchesAgent || `${model.name} ${model.id}`.toLocaleLowerCase().includes(term)) : []
    const allowCustom = agent.modelSelection && !listed.some(model => !images || model.images !== false) && matchesAgent
    if (!matchesAgent && !matches.length) return null
    return <div key={agent.id} className="model-picker-group" role="group" aria-label={agent.label}>
      <strong>{agent.label}</strong>
      {matchesAgent && <button type="button" aria-pressed={value?.agent === agent.id && !value.model}
        onClick={() => choose({ agent: agent.id })}>默认模型</button>}
      {matches.map(model => <button key={model.id} type="button" title={model.id} aria-pressed={value?.agent === agent.id && value.model === model.id}
        onClick={() => choose({ agent: agent.id, model: model.id, modelName: model.name })}>{model.name}</button>)}
      {allowCustom && <button type="button" onClick={() => { setCustomAgent(agent); setCustomModel(value?.agent === agent.id ? value.model || '' : '') }}>
        输入模型 ID…
      </button>}
    </div>
  })

  const customId = customModel.trim()
  const validCustomId = /^[a-zA-Z0-9][a-zA-Z0-9._:/@+\-]{0,199}$/.test(customId)
  function chooseCustom() {
    if (customAgent && validCustomId) choose({ agent: customAgent.id, model: customId })
  }

  return <div className="model-picker">
    <button type="button" className={images ? 'model-picker-trigger' : 'model-picker-trigger wb-chrome-btn'} ref={trigger} aria-label={title}
      aria-expanded={active} aria-controls={active ? id : undefined} aria-haspopup="dialog"
      disabled={disabled} title={modelName && agentName ? `${modelName} · ${agentName}` : display}
      onClick={() => { setQuery(''); setCustomAgent(undefined); setCatalogError(false); setOpen(!active) }}>
      <span>{display}</span><span aria-hidden="true">⌄</span>
    </button>
    {active && createPortal(<div id={id} className="model-picker-popover" ref={popup} role="dialog" aria-label={title} style={position} onKeyDown={keyboard}>
      {customAgent ? <div className="model-picker-custom">
        <label>{customAgent.label} 模型 ID<input autoFocus value={customModel} maxLength={200} placeholder="输入模型 ID"
          onChange={event => setCustomModel(event.target.value)} onKeyDown={event => {
            if (event.key === 'Enter' && !event.nativeEvent.isComposing) { event.preventDefault(); chooseCustom() }
          }} /></label>
        <div><button type="button" onClick={() => setCustomAgent(undefined)}>返回</button>
          <button type="button" disabled={!validCustomId} onClick={chooseCustom}>使用模型</button></div>
      </div> : <>
        <input ref={search} type="search" placeholder="搜索模型或 Agent" aria-label="搜索模型或 Agent" value={query} onChange={event => setQuery(event.target.value)} />
        <div className="model-picker-list">
          {!term && <button type="button" aria-pressed={!value} onClick={() => choose()}>{automatic}</button>}
          {buttons}
          {connectionError ? <p role="alert">读取模型连接失败。</p> : !status ? <p role="status">正在读取模型连接…</p>
            : catalogError ? <p role="status">暂时无法读取可选模型。</p>
              : catalog === undefined ? <p role="status">正在读取可选模型…</p>
                : !buttons?.some(Boolean) && <p>{term ? '没有匹配的模型。' : '没有可用的模型连接。'}</p>}
        </div>
      </>}
    </div>, document.body)}
  </div>
}
