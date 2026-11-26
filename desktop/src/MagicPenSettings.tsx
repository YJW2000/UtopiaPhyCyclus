import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { errorMessage } from './editorContract'
import './magic-pen-settings.css'

export interface QuickNote {
  id: string
  label?: string
  instruction?: string
  icon?: string
}

type Note = Required<QuickNote>

const limit = 8
// Papilot's shipped Simplified Chinese presets. Store overrides only so an
// unchanged preset still follows the language of the PDF revision pane.
const presets: Note[] = [
  { id: 'polish', icon: 'sparkles', label: '润色', instruction: '只润色这一句：改语法、用词和语气，不改意思和长度。' },
  { id: 'tighten', icon: 'compress', label: '精简', instruction: '说得更简洁：去掉重复和多余的话，意思不变。' },
  { id: 'runt', icon: 'runt', label: '孤行', instruction: '这段最后一行只剩一两个词，微调措辞把它收回去。' },
  { id: 'connect', icon: 'link', label: '衔接', instruction: '和上一句接不上。补一句过渡，或者呼应前面提过的内容。' },
  { id: 'verify', icon: 'verify', label: '核实', instruction: '对照引用的文献或原始来源核实这里。只在确实有误时修改，并说明核对了什么。' },
]

function resolve(notes: QuickNote[] | null): Note[] {
  if (!notes) return presets
  return notes.map(note => {
    const preset = presets.find(item => item.id === note.id)
    return {
      id: note.id,
      label: note.label ?? preset?.label ?? '',
      instruction: note.instruction ?? preset?.instruction ?? '',
      icon: note.icon ?? preset?.icon ?? 'notes',
    }
  })
}

function encode(notes: Note[]): QuickNote[] | null {
  const saved = notes.map(({ id, label, instruction, icon }) => {
    const preset = presets.find(item => item.id === id)
    return {
      id,
      ...(label !== preset?.label ? { label } : {}),
      ...(instruction !== preset?.instruction ? { instruction } : {}),
      ...(icon !== preset?.icon ? { icon } : {}),
    }
  })
  if (saved.length === presets.length && saved.every((note, index) =>
    note.id === presets[index].id && Object.keys(note).length === 1,
  )) return null
  return saved
}

export default function MagicPenSettings() {
  const [saved, setSaved] = useState<QuickNote[] | null>()
  const [draft, setDraft] = useState<Note>()
  const [attempt, setAttempt] = useState(0)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [undo, setUndo] = useState<{ notes: QuickNote[] | null }>()

  useEffect(() => {
    let active = true
    void window.utopia.papilotQuickNotes().then(notes => {
      if (active) setSaved(notes)
    }).catch(failure => {
      if (active) setError(errorMessage(failure))
    })
    return () => { active = false }
  }, [attempt])

  const notes = saved === undefined ? [] : resolve(saved)
  const label = draft?.label.replace(/[·\s]+/g, ' ').trim()
  const instruction = draft?.instruction.trim()

  async function save(next: QuickNote[] | null, showUndo = false) {
    if (saving || saved === undefined) return
    setSaving(true)
    setNotice('')
    setError('')
    setUndo(undefined)
    try {
      setSaved(await window.utopia.papilotQuickNotes(next))
      setDraft(undefined)
      setNotice('已保存，修订时可从魔法笔圆盘选用。')
      if (showUndo) setUndo({ notes: saved })
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setSaving(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    if (!draft || !label || !instruction) return
    const note = { ...draft, label, instruction }
    const next = notes.some(item => item.id === note.id)
      ? notes.map(item => item.id === note.id ? note : item)
      : [...notes, note]
    void save(encode(next))
  }

  return <section className="mc-card magic-pen-settings" aria-labelledby="magic-pen-settings-title">
    <h2 id="magic-pen-settings-title">魔法笔默认语句</h2>
    <p>选中文字后，圆盘会显示这些语句。数字键 1 至 {limit} 对应同一顺序。</p>
    {saved === undefined ? error ? <div className="settings-row-actions">
      <button type="button" onClick={() => { setError(''); setAttempt(value => value + 1) }}>重新读取</button>
    </div> : <p role="status">正在读取魔法笔设置…</p> : <>
      <ol className="magic-pen-settings-list">
        {notes.map((note, index) => <li key={note.id}>
          <kbd>{index + 1}</kbd>
          <div><strong>{note.label}</strong><p>{note.instruction}</p></div>
          <div className="magic-pen-settings-actions">
            <button type="button" aria-label={`编辑 ${note.label}`} disabled={saving || Boolean(draft)} onClick={() => {
              setNotice('')
              setDraft(note)
            }}>编辑</button>
            <button type="button" aria-label={`删除 ${note.label}`} disabled={saving || Boolean(draft) || notes.length < 2}
              onClick={() => void save(encode(notes.filter(item => item.id !== note.id)), true)}>删除</button>
          </div>
        </li>)}
      </ol>
      {draft ? <form className="magic-pen-settings-form" onSubmit={submit}>
        <label>圆盘名称<input autoFocus required maxLength={40} value={draft.label} disabled={saving}
          onChange={event => setDraft({ ...draft, label: event.target.value })} /></label>
        <label>默认语句<textarea required rows={3} value={draft.instruction} disabled={saving}
          onChange={event => setDraft({ ...draft, instruction: event.target.value })} /></label>
        <div className="magic-pen-settings-actions">
          <button type="button" disabled={saving} onClick={() => setDraft(undefined)}>取消</button>
          <button className="primary" type="submit" disabled={saving || !label || !instruction}>{saving ? '正在保存…' : '保存语句'}</button>
        </div>
      </form> : <div className="settings-row-actions">
        <button type="button" disabled={saving || notes.length >= limit} onClick={() => {
          setNotice('')
          setDraft({ id: `custom-${crypto.randomUUID().replace(/-/g, '')}`, label: '', instruction: '', icon: 'notes' })
        }}>添加语句</button>
        <button type="button" disabled={saving || saved === null} onClick={() => void save(null, true)}>恢复内置语句</button>
        {undo && <button type="button" disabled={saving} onClick={() => void save(undo.notes)}>撤销</button>}
      </div>}
    </>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">{error}</p>}
  </section>
}
