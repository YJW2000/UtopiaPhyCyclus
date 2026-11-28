import { useCallback, useEffect, useRef, useState } from 'react'
import type { ClipboardImage } from './bridge'
import { editorCall, errorMessage, reconstructEditorDocument } from './editorContract'
import type { EditorDocument } from './editorContract'
import ModelPicker from './ModelPicker'
import type { ModelChoice, ProviderStatus } from './settingsContract'

const modelKey = 'utopia-image-intake-model'

async function readImageFile(file: File): Promise<ClipboardImage> {
  const jpeg = file.type === 'image/jpeg' || (!file.type && /\.jpe?g$/i.test(file.name))
  const png = file.type === 'image/png' || (!file.type && /\.png$/i.test(file.name))
  if (!jpeg && !png) throw new Error('请选择 PNG 或 JPG 图片。')
  const type = jpeg ? 'jpeg' : 'png'
  const data = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const body = String(reader.result).split(',')[1]
      if (body) resolve(body)
      else reject(new Error('图片文件为空。'))
    }
    reader.onerror = () => reject(new Error('无法读取图片文件。'))
    reader.readAsDataURL(file)
  })
  return { name: file.name || `剪贴板图片.${type}`, type, data }
}

function pasteGoesToControl(target: EventTarget | null) {
  if (!(target instanceof Element)) return false
  return Boolean(target.closest('.context-chat, input, textarea, select, [role="textbox"]')) ||
    (target instanceof HTMLElement && target.isContentEditable)
}

export default function ImageIntake({ disabled, onBusyChange, onOpen, onSettings }: {
  disabled: boolean
  onBusyChange: (busy: boolean) => void
  onOpen: (document: EditorDocument) => void
  onSettings: () => void
}) {
  const [image, setImage] = useState<ClipboardImage>()
  const [draft, setDraft] = useState<EditorDocument>()
  const [modelChoice, setModelChoice] = useState<ModelChoice | undefined>(() => {
    try { return JSON.parse(localStorage.getItem(modelKey) ?? 'null') ?? undefined } catch { return undefined }
  })
  const [selectedModel, setSelectedModel] = useState('')
  const [error, setError] = useState('')
  const [progress, setProgress] = useState('')
  const [stopState, setStopState] = useState<'requested' | 'stopped'>()
  const running = useRef<AbortController | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const busy = disabled || Boolean(progress)

  useEffect(() => () => {
    if (running.current) {
      running.current.abort()
      running.current = null
      onBusyChange(false)
    }
  }, [onBusyChange])

  const operate = useCallback(async (message: string, action: (signal: AbortSignal) => Promise<void>) => {
    if (disabled || running.current) return
    const controller = new AbortController()
    running.current = controller
    setError('')
    setStopState(undefined)
    setProgress(message)
    onBusyChange(true)
    try {
      await action(controller.signal)
    } catch (failure) {
      if (!controller.signal.aborted) setError(errorMessage(failure))
    } finally {
      if (running.current === controller) {
        running.current = null
        setProgress('')
        if (controller.signal.aborted) setStopState('stopped')
        onBusyChange(false)
      }
    }
  }, [disabled, onBusyChange])

  const recognize = useCallback(async (
    next: ClipboardImage, existing: EditorDocument | undefined, signal: AbortSignal,
  ) => {
    let choice = modelChoice
    if (!choice) {
      setProgress('正在选择支持图片的模型…')
      const status = await editorCall<ProviderStatus>('ui.providers')
      signal.throwIfAborted()
      const available = status.agents.filter(agent => agent.installed && agent.images)
      const provider = available.find(agent => agent.id === status.effectiveProvider) ?? available[0]
      if (!provider) throw new Error('未检测到支持图片的模型连接。请在设置中配置后重试。')
      choice = { agent: provider.id, modelName: provider.label }
    }
    setSelectedModel(choice.modelName || choice.model || choice.agent)
    let document: EditorDocument
    if (existing) {
      document = await editorCall<EditorDocument>('workspace.open', { id: existing.id })
      signal.throwIfAborted()
      if (document.source.trim()) {
        onOpen(document)
        return
      }
    } else {
      setProgress('正在保存原图…')
      const { path } = await editorCall<{ path: string }>('library.imageInput', { ...next })
      signal.throwIfAborted()
      document = await editorCall<EditorDocument>('workspace.create', {
        pdfPath: path, title: next.name, inputKind: 'image',
      })
      if (running.current?.signal === signal) setDraft(document)
      signal.throwIfAborted()
    }
    setProgress('正在识别图片并生成 LaTeX…')
    document = await reconstructEditorDocument({
      id: document.id, firstPage: 1, lastPage: 1, agent: choice.agent, model: choice.model,
    }, signal, setProgress)
    if (!signal.aborted) onOpen(document)
  }, [modelChoice, onOpen])

  const read = useCallback((load: () => Promise<ClipboardImage | null>) => {
    void operate('正在读取图片…', async signal => {
      const next = await load()
      if (signal.aborted) return
      if (!next) throw new Error('剪贴板中没有图片。请先复制图片或截图。')
      setImage(next)
      setDraft(undefined)
      setSelectedModel('')
      await recognize(next, undefined, signal)
    })
  }, [operate, recognize])

  useEffect(() => {
    function shortcut(event: KeyboardEvent) {
      if (event.defaultPrevented || event.isComposing || event.altKey || event.shiftKey ||
          !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'v' ||
          pasteGoesToControl(event.target)) return
      event.preventDefault()
      if (!event.repeat) read(window.utopia.readClipboardImage)
    }
    function paste(event: ClipboardEvent) {
      if (event.defaultPrevented || pasteGoesToControl(event.target)) return
      const clip = event.clipboardData
      if (!clip) return
      const file = Array.from(clip.files).find(item => item.type.startsWith('image/')) ??
        Array.from(clip.items).find(item => item.kind === 'file' && item.type.startsWith('image/'))?.getAsFile()
      if (!file) return
      event.preventDefault()
      read(() => readImageFile(file))
    }
    window.addEventListener('keydown', shortcut)
    window.addEventListener('paste', paste)
    return () => {
      window.removeEventListener('keydown', shortcut)
      window.removeEventListener('paste', paste)
    }
  }, [read])

  function chooseModel(choice?: ModelChoice) {
    setModelChoice(choice)
    try {
      if (choice) localStorage.setItem(modelKey, JSON.stringify(choice))
      else localStorage.removeItem(modelKey)
    } catch { /* The mounted intake still retains the selection. */ }
  }

  function stop() {
    if (!running.current || running.current.signal.aborted) return
    setStopState('requested')
    running.current.abort()
  }

  return (
    <section className="mc-card intake">
      <span aria-hidden="true">📝</span>
      <h2>从题目图片录入</h2>
      <p>按 Ctrl+V 粘贴截图，或选择 PNG、JPG 图片。识别完成即可编辑 LaTeX，PDF 在后台生成。</p>
      <div className="intake-actions">
        <button type="button" className={image ? undefined : 'primary'} disabled={busy}
          onClick={() => read(window.utopia.readClipboardImage)}>粘贴图片</button>
        <button type="button" disabled={busy} onClick={() => input.current?.click()}>选择图片</button>
        <ModelPicker images value={modelChoice} disabled={busy} onChange={chooseModel} />
        <input ref={input} hidden type="file" accept="image/jpeg,image/png" onChange={event => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) read(() => readImageFile(file))
        }} />
        {progress && <button type="button" disabled={stopState === 'requested'} onClick={stop}>停止识别</button>}
      </div>
      {(progress || stopState === 'stopped') && <p role="status">
        {stopState === 'requested' ? '正在停止识别…' : stopState === 'stopped' ? '识别已停止。' : progress}
      </p>}
      {image && (
        <div className="image-intake-preview">
          <img src={`data:image/${image.type};base64,${image.data}`} alt="原图" />
          <small>{image.name}</small>
          {selectedModel && progress && <p>识别模型 · {selectedModel}</p>}
          {(error || stopState === 'stopped') && !busy && <div className="intake-actions">
            <button type="button" className="primary" onClick={() => {
              setSelectedModel('')
              void operate('正在重试…', signal => recognize(image, draft, signal))
            }}>重试识别</button>
            {error && <button type="button" onClick={onSettings}>模型连接设置</button>}
          </div>}
        </div>
      )}
      {error && <p className="image-intake-error" role="alert">{error}</p>}
    </section>
  )
}
