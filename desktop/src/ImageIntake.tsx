import { useCallback, useEffect, useRef, useState } from 'react'
import type { ClipboardImage } from './bridge'
import { editorCall, errorMessage, pollEditorTask } from './editorContract'
import type { EditorCompileResult, EditorDocument } from './editorContract'
import type { ProviderId, ProviderStatus } from './settingsContract'

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

async function runImageTask<T>(
  method: 'workspace.reconstruct' | 'workspace.compile',
  params: Record<string, unknown>,
  signal: AbortSignal,
  onProgress: (message: string) => void,
) {
  const { taskId } = await editorCall<{ taskId: string }>(method, params)
  const cancel = () => { void editorCall('task.cancel', { id: taskId }).catch(() => {}) }
  if (signal.aborted) cancel()
  else signal.addEventListener('abort', cancel, { once: true })
  try {
    const task = await pollEditorTask<T>(taskId, signal, message => {
      if (!signal.aborted) onProgress(message)
    })
    if (task.status !== 'succeeded' || task.result === undefined) {
      throw new Error([task.error || task.message || '图片处理已停止。', task.log].filter(Boolean).join('\n\n'))
    }
    return task.result
  } finally {
    signal.removeEventListener('abort', cancel)
  }
}

export default function ImageIntake({ disabled, onBusyChange, onOpen, onSettings }: {
  disabled: boolean
  onBusyChange: (busy: boolean) => void
  onOpen: (document: EditorDocument, compileError?: string) => void
  onSettings: () => void
}) {
  const [image, setImage] = useState<ClipboardImage>()
  const [draft, setDraft] = useState<EditorDocument>()
  const [providers, setProviders] = useState<ProviderStatus>()
  const [selected, setSelected] = useState<ProviderId | ''>()
  const [providerError, setProviderError] = useState('')
  const [error, setError] = useState('')
  const [compileError, setCompileError] = useState('')
  const [progress, setProgress] = useState('')
  const running = useRef<AbortController | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const options = (providers?.agents ?? []).filter(agent => agent.installed && agent.images)
  const effective = options.find(option => option.id === providers?.effectiveProvider)?.id ?? ''
  const provider = selected ?? effective
  const hasImage = Boolean(image)
  const hasSource = Boolean(draft?.source.trim())
  const busy = disabled || Boolean(progress)

  useEffect(() => {
    if (!hasImage) return
    let active = true
    void editorCall<ProviderStatus>('ui.providers').then(
      status => { if (active) setProviders(status) },
      failure => { if (active) setProviderError(errorMessage(failure)) },
    )
    return () => { active = false }
  }, [hasImage])

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
        onBusyChange(false)
      }
    }
  }, [disabled, onBusyChange])

  const read = useCallback((load: () => Promise<ClipboardImage | null>) => {
    void operate('正在读取图片…', async signal => {
      const next = await load()
      if (signal.aborted) return
      if (!next) throw new Error('剪贴板中没有图片。请先复制图片或截图。')
      setImage(next)
      setDraft(undefined)
      setCompileError('')
    })
  }, [operate])

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

  function recognizeAndCompile() {
    if (!image || (!hasSource && !provider)) return
    void operate(draft ? '正在处理图片…' : '正在保存原图…', async signal => {
      let document = draft
      if (!document) {
        const { path } = await editorCall<{ path: string }>('library.imageInput', { ...image })
        if (signal.aborted) return
        document = await editorCall<EditorDocument>('workspace.create', {
          pdfPath: path, title: image.name, inputKind: 'image',
        })
        if (signal.aborted) return
        setDraft(document)
      }
      if (!document.source.trim()) {
        setProgress('正在识别图片…')
        document = await runImageTask<EditorDocument>('workspace.reconstruct',
          { id: document.id, firstPage: 1, lastPage: 1, agent: provider }, signal, setProgress)
        if (signal.aborted) return
        setDraft(document)
      }
      setProgress('正在编译 PDF…')
      setCompileError('')
      try {
        await runImageTask<EditorCompileResult>('workspace.compile', { id: document.id, source: document.source }, signal, setProgress)
        if (signal.aborted) return
        const compiled = await editorCall<EditorDocument>('workspace.open', { id: document.id })
        if (signal.aborted) return
        if (!compiled.previewPath || !compiled.previewCurrent) {
          setDraft(compiled)
          throw new Error('LaTeX 源码已更新，请重新编译。')
        }
        onOpen(compiled)
      } catch (failure) {
        if (!signal.aborted) setCompileError(errorMessage(failure))
      }
    })
  }

  return (
    <section className="mc-card intake">
      <span aria-hidden="true">📝</span>
      <h2>从题目图片录入</h2>
      <p>按 Ctrl+V 粘贴截图，或选择 PNG、JPG 图片。选择识别模型后，点击「开始识别并编译」。</p>
      <div className="intake-actions">
        <button type="button" className={image ? undefined : 'primary'} disabled={busy}
          onClick={() => read(window.utopia.readClipboardImage)}>粘贴图片</button>
        <button type="button" disabled={busy} onClick={() => input.current?.click()}>选择图片</button>
        <input ref={input} hidden type="file" accept="image/jpeg,image/png" onChange={event => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) read(() => readImageFile(file))
        }} />
      </div>
      {image && (
        <div className="image-intake-preview">
          <img src={`data:image/${image.type};base64,${image.data}`} alt="原图" />
          <small>{image.name}</small>
          <div className="intake-actions">
            <label className="image-intake-model">
              识别模型
              <select value={provider} disabled={busy || hasSource || options.length === 0}
                onChange={event => setSelected(options.find(option => option.id === event.target.value)?.id ?? '')}>
                <option value="" disabled>{providers || providerError ? '选择支持图片的模型连接' : '正在检测模型连接…'}</option>
                {options.map(option => <option key={option.id} value={option.id}>{option.label}</option>)}
              </select>
            </label>
            <button type="button" className="primary" disabled={busy || (!hasSource && !provider)} onClick={recognizeAndCompile}>
              {hasSource ? '重新编译并打开' : '开始识别并编译'}
            </button>
            {compileError && draft && (
              <button type="button" disabled={busy} onClick={() => onOpen(draft, compileError)}>打开工作台修正源码</button>
            )}
          </div>
          {providerError ? (
            <p className="image-intake-error" role="alert">模型连接检测失败。{providerError}</p>
          ) : providers && !options.length ? (
            <p>请在设置中配置支持图片的 Agent。</p>
          ) : provider && !hasSource && (
            <p>识别使用此 Agent 中配置的模型。</p>
          )}
          {(providerError || (providers && !options.length)) && (
            <button type="button" disabled={busy} onClick={onSettings}>打开模型连接设置</button>
          )}
        </div>
      )}
      {progress && <p role="status">{progress}</p>}
      {error && <p className="image-intake-error" role="alert">{error}</p>}
      {compileError && (
        <div className="image-intake-error" role="alert">
          LaTeX 已保留，PDF 编译失败。
          <details><summary>编译日志</summary><pre>{compileError}</pre></details>
        </div>
      )}
    </section>
  )
}
