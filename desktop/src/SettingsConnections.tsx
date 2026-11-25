import { useCallback, useEffect, useRef, useState } from 'react'
import { pollEditorTask } from './editorContract'
import type {
  EnvironmentStatus,
  EnvironmentTool,
  EnvironmentToolId,
  LocalAgent,
  ProviderId,
  ProviderStatus,
} from './settingsContract'

const tools = [
  { id: 'git', label: 'Git' },
  { id: 'latex', label: 'LaTeX（XeLaTeX）' },
] as const

type Feedback = { error: boolean; message: string }
type ToolActions = {
  active: boolean
  pending: EnvironmentToolId | null
  feedback: Partial<Record<EnvironmentToolId, Feedback>>
  install: (tool: EnvironmentTool) => Promise<void>
  help: (tool: EnvironmentTool) => Promise<void>
}

const errorMessage = (error: unknown) =>
  (error instanceof Error ? error.message : String(error)).replace(
    /^Error invoking remote method '[^']+': (?:Error: )?/,
    ''
  )

function ToolControls({ tool, active, pending, feedback, install, help }: ToolActions & {
  tool: EnvironmentTool
}) {
  const installing = tool.installing || pending === tool.id
  const notice = feedback[tool.id]
  const installLabel = notice?.error ? '重试安装' : '一键安装'
  return (
    <>
      {(!tool.installed || installing) && (
        <div className="settings-row-actions">
          {(tool.canInstall || installing) && (
            <button
              type="button"
              className="primary"
              disabled={active || installing}
              aria-label={`${installing ? '正在安装' : installLabel} ${tool.label}`}
              onClick={() => void install(tool)}
            >
              {installing ? '正在安装…' : installLabel}
            </button>
          )}
          {!installing && tool.helpUrl && (
            <button
              type="button"
              aria-label={`${tool.label} 安装说明`}
              onClick={() => void help(tool)}
            >
              安装说明
            </button>
          )}
        </div>
      )}
      {(installing || tool.detail || notice) && (
        <div className="settings-row-message">
          {installing && (
            <p role="status">
              {tool.progress || '正在安装。如果系统弹出确认窗口，请按提示操作。'}
            </p>
          )}
          {!installing && tool.detail && tool.detail !== notice?.message && (
            <p>{tool.detail}</p>
          )}
          {notice && <p role={notice.error ? 'alert' : 'status'}>{notice.message}</p>}
        </div>
      )}
    </>
  )
}

function ToolDetails({ tool }: { tool?: EnvironmentTool }) {
  if (!tool?.installed || (!tool.version && !tool.path)) return null
  return (
    <div className="settings-tool-details">
      {tool.version && <code>{tool.version}</code>}
      {tool.path && <code>{tool.path}</code>}
    </div>
  )
}

export default function SettingsConnections({
  provider,
  saveProvider,
  openApi,
  apiPending,
}: {
  provider?: string
  saveProvider: (provider: ProviderId) => Promise<void>
  openApi: () => void
  apiPending: boolean
}) {
  const [providers, setProviders] = useState<ProviderStatus>()
  const [environment, setEnvironment] = useState<EnvironmentStatus>()
  const [providerError, setProviderError] = useState('')
  const [environmentError, setEnvironmentError] = useState('')
  const [saveError, setSaveError] = useState('')
  const [checking, setChecking] = useState(true)
  const [saving, setSaving] = useState(false)
  const [pendingInstall, setPendingInstall] = useState<EnvironmentToolId | null>(null)
  const [feedback, setFeedback] = useState<ToolActions['feedback']>({})
  const [loginFeedback, setLoginFeedback] = useState<Feedback & { provider: ProviderId }>()
  const [testingLatex, setTestingLatex] = useState(false)
  const [latexFeedback, setLatexFeedback] = useState<Feedback>()
  const checkRequest = useRef(0)
  const providerRequest = useRef(0)
  const environmentRequest = useRef(0)
  const latexRequest = useRef<AbortController | null>(null)

  const loadProviders = useCallback(async () => {
    const request = ++providerRequest.current
    try {
      const status = await window.utopia.call<ProviderStatus>('ui.providers')
      if (request !== providerRequest.current) return
      setProviders(status)
      setProviderError('')
    } catch (error) {
      if (request === providerRequest.current) setProviderError(errorMessage(error))
    }
  }, [])

  const loadEnvironment = useCallback(async () => {
    const request = ++environmentRequest.current
    try {
      const status = await window.utopia.detectEnvironment()
      if (request !== environmentRequest.current) return
      setEnvironment(status)
      setEnvironmentError('')
      return status
    } catch (error) {
      if (request === environmentRequest.current) setEnvironmentError(errorMessage(error))
    }
  }, [])

  const check = useCallback(async () => {
    const request = ++checkRequest.current
    setChecking(true)
    await Promise.all([loadProviders(), loadEnvironment()])
    if (request === checkRequest.current) setChecking(false)
  }, [loadProviders, loadEnvironment])

  useEffect(() => { void check() }, [check])
  useEffect(() => () => latexRequest.current?.abort(), [])

  const nativeInstalling = Object.values(environment?.tools || {}).some((tool) => tool.installing)
  const active = nativeInstalling || pendingInstall !== null
  useEffect(() => {
    if (!active || checking) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      const status = await loadEnvironment()
      if (cancelled) return
      if (status && !Object.values(status.tools).some((tool) => tool.installing)) {
        void loadProviders()
      } else {
        timer = setTimeout(poll, 3000)
      }
    }
    timer = setTimeout(poll, 3000)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [active, checking, loadEnvironment, loadProviders])

  async function select(id: ProviderId) {
    if (saving || id === provider) return
    setSaving(true)
    setSaveError('')
    setLoginFeedback(undefined)
    try {
      await saveProvider(id)
      await loadProviders()
    } catch (error) {
      setSaveError(errorMessage(error))
    } finally {
      setSaving(false)
    }
  }

  async function install(tool: EnvironmentTool) {
    if (active || !tool.canInstall) return
    setPendingInstall(tool.id)
    setFeedback((previous) => ({ ...previous, [tool.id]: undefined }))
    let notice: Feedback
    try {
      const result = await window.utopia.installEnvironmentTool(tool.id)
      notice = { error: !result.ok, message: result.message }
    } catch (error) {
      notice = { error: true, message: errorMessage(error) }
    }
    await check()
    setFeedback((previous) => ({ ...previous, [tool.id]: notice }))
    setPendingInstall(null)
  }

  async function help(tool: EnvironmentTool) {
    try {
      await window.utopia.openEnvironmentHelp(tool.id)
    } catch (error) {
      setFeedback((previous) => ({
        ...previous,
        [tool.id]: { error: true, message: errorMessage(error) },
      }))
    }
  }

  async function copyLoginCommand(provider: ProviderId, command: string) {
    try {
      await navigator.clipboard.writeText(command)
      setLoginFeedback({ provider, error: false, message: '已复制登录命令。' })
    } catch (error) {
      setLoginFeedback({ provider, error: true, message: `复制失败。${errorMessage(error)}` })
    }
  }

  async function testLatex() {
    if (latexRequest.current) return
    const request = new AbortController()
    latexRequest.current = request
    setTestingLatex(true)
    setLatexFeedback(undefined)
    let taskId: string | undefined
    try {
      const task = await window.utopia.call<{ taskId: string }>('system.check_latex')
      taskId = task.taskId
      const result = await pollEditorTask<{ message: string }>(taskId, request.signal)
      if (request.signal.aborted) return
      if (result.status !== 'succeeded' || !result.result) {
        throw new Error(result.error ?? '排版测试未完成。')
      }
      setLatexFeedback({ error: false, message: result.result.message })
    } catch (error) {
      if (!request.signal.aborted) setLatexFeedback({ error: true, message: errorMessage(error) })
    } finally {
      if (request.signal.aborted && taskId) {
        void window.utopia.call('task.cancel', { id: taskId }).catch(() => {})
      }
      latexRequest.current = null
      if (!request.signal.aborted) setTestingLatex(false)
    }
  }

  const actions = { active, pending: pendingInstall, feedback, install, help }
  const agents = providers?.agents ?? []
  const isReady = (agent: LocalAgent) => Boolean(agent.installed && environment?.tools[agent.id]?.installed)
  const current = agents.find((agent) => agent.id === provider)
  const effective = providers?.effectiveProvider ?? provider
  const currentLabel = provider === 'api' ? '自定义 API' : current?.label
  const connected = provider === 'api' ? providers?.apiConfigured : current && isReady(current)
  const activeLabel = effective === 'api'
    ? (providers?.apiConfigured ? '自定义 API' : undefined)
    : agents.find((agent) => agent.id === effective && isReady(agent))?.label
  const selection = activeLabel
    ? `当前使用 ${activeLabel}`
    : currentLabel && !connected ? `当前选择 ${currentLabel}，${checking ? '正在检测' : '尚未就绪'}` : ''

  return (
    <>
      <section className="mc-card settings-connections" aria-labelledby="settings-model-title">
        <div className="section-head settings-section-head">
          <h2 id="settings-model-title">模型连接</h2>
          <button
            type="button"
            aria-label="重新检测 Agent、Git 和 LaTeX"
            onClick={() => void check()}
            disabled={checking}
          >
            {checking ? '正在检测…' : '重新检测'}
          </button>
        </div>
        {providerError && <p role="alert">检测模型连接失败。{providerError}</p>}
        {saveError && <p role="alert">保存模型连接失败。{saveError}</p>}
        {!providers && checking && <p role="status">正在检测本地 Agent…</p>}
        {providers && (
          <>
            <p className="settings-selection" role="status">
              {saving ? '正在保存模型连接…' : selection}
            </p>
            <fieldset className="settings-agent-list" disabled={saving}>
              <legend>默认连接</legend>
              {agents.map((agent) => {
                const tool = environment?.tools[agent.id]
                const selected = agent.id === provider
                const installing = tool?.installing || pendingInstall === agent.id
                const ready = isReady(agent)
                const status = installing
                  ? '正在安装'
                  : ready ? '已检测到' : !tool ? (checking ? '正在检测' : '待检测')
                  : agent.installed || tool.installed ? '尚未就绪' : '未检测到'
                const command = agent.loginCommand
                return (
                  <div className="settings-row" key={agent.id}>
                    <label className="settings-agent-choice">
                      <input
                        type="radio"
                        name="settings-provider"
                        checked={selected}
                        disabled={!provider || !ready}
                        aria-label={agent.label}
                        aria-describedby={`settings-provider-${agent.id}`}
                        onChange={() => void select(agent.id)}
                      />
                      <span>{agent.label}</span>
                      <small id={`settings-provider-${agent.id}`} className="settings-status" data-ready={ready}>
                        {status}
                      </small>
                    </label>
                    {tool && <ToolControls tool={tool} {...actions} />}
                    {selected && (
                      <div className="settings-agent-detail">
                        <ToolDetails tool={tool} />
                        <p>{agent.images ? '支持图片输入。' : '仅支持文本输入。'}</p>
                        {agent.setup && <p>{agent.setup}</p>}
                        {agent.installed && command && (
                          <div className="settings-login">
                            <code>{command}</code>
                            <button type="button" onClick={() => void copyLoginCommand(agent.id, command)}>
                              复制登录命令
                            </button>
                          </div>
                        )}
                        {loginFeedback?.provider === agent.id && (
                          <p role={loginFeedback.error ? 'alert' : 'status'}>{loginFeedback.message}</p>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
              <div className="settings-row">
                <label className="settings-agent-choice">
                  <input
                    type="radio"
                    name="settings-provider"
                    checked={provider === 'api'}
                    disabled={!provider}
                    aria-label="自定义 API"
                    aria-describedby="settings-provider-api"
                    onChange={() => void select('api')}
                  />
                  <span>自定义 API</span>
                  <small id="settings-provider-api" className="settings-status" data-ready={providers.apiConfigured}>
                    {providers.apiConfigured ? '已配置' : '待配置'}
                  </small>
                </label>
                {provider === 'api' && (
                  <>
                    <div className="settings-row-actions">
                      <button type="button" disabled={apiPending} onClick={openApi}>
                        {apiPending ? '正在打开…' : '打开 API 设置'}
                      </button>
                    </div>
                    <div className="settings-agent-detail">
                      <p>{providers.apiConfigured ? '图片输入取决于所配置的模型。' : '在 API 设置中填写地址、模型和密钥。'}</p>
                    </div>
                  </>
                )}
              </div>
            </fieldset>
          </>
        )}
      </section>
      <section className="mc-card settings-connections" aria-labelledby="settings-tools-title">
        <h2 id="settings-tools-title">本地工具</h2>
        {environmentError && <p role="alert">检测本地工具失败。{environmentError}</p>}
        {tools.map(({ id, label }) => {
          const tool = environment?.tools[id]
          const installing = tool?.installing || pendingInstall === id
          const status = installing
            ? '正在安装'
            : tool?.installed
              ? tool.source === 'bundled' ? '应用内置' : '已检测到'
              : tool ? '未检测到' : checking ? '正在检测' : '待检测'
          return (
            <div key={id} className="settings-row" role="group" aria-label={label}>
              <div className="settings-tool-heading">
                <strong>{label}</strong>
                <small className="settings-status" data-ready={tool?.installed}>{status}</small>
              </div>
              {tool && <ToolControls tool={tool} {...actions} />}
              <ToolDetails tool={tool} />
              {id === 'latex' && tool?.installed && (
                <>
                  <div className="settings-row-actions">
                    <button type="button" disabled={testingLatex || active} onClick={() => void testLatex()}>
                      {testingLatex ? '正在测试中文排版…' : '测试中文排版'}
                    </button>
                  </div>
                  {(testingLatex || latexFeedback) && (
                    <div className="settings-row-message">
                      {testingLatex
                        ? <p role="status">正在编译测试题…</p>
                        : <p role={latexFeedback?.error ? 'alert' : 'status'}>{latexFeedback?.message}</p>}
                    </div>
                  )}
                </>
              )}
            </div>
          )
        })}
      </section>
    </>
  )
}
