import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { EditorModal } from './EditorChat'
import { errorMessage } from './editorContract'
import type { EditorDocument } from './editorContract'

export interface QuestionFileInfo {
  path: string
  id: string
  sourceHash: string
  year: string
  paperType: string
  paper: string
  number: string
  subject: string
  tags: string
}

interface SavedInfo {
  file: QuestionFileInfo
  document: EditorDocument
}

const fields = [
  ['year', '年份'], ['paperType', '试卷类别代码'], ['paper', '试卷名称'],
  ['number', '原卷题号'], ['subject', '知识板块'], ['tags', '标签'],
] as const

export default function FileInfoDialog({
  path, document, onClose, onSaved,
}: {
  path: string
  document: EditorDocument
  onClose: () => void
  onSaved: (result: SavedInfo) => void
}) {
  const [info, setInfo] = useState<QuestionFileInfo>()
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let active = true
    window.utopia.call<QuestionFileInfo>('workspace.fileInfo', { id: document.id, path })
      .then((value) => { if (active) setInfo(value) })
      .catch((error) => { if (active) setError(errorMessage(error)) })
    return () => { active = false }
  }, [document.id, path])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!info || saving) return
    const form = new FormData(event.currentTarget)
    setError('')
    setSaving(true)
    try {
      const result = await window.utopia.call<SavedInfo>('workspace.saveFileInfo', {
        id: document.id,
        revision: document.revision,
        path,
        expectedHash: info.sourceHash,
        fields: Object.fromEntries(form),
      })
      onSaved(result)
    } catch (error) {
      setError(errorMessage(error))
      setSaving(false)
    }
  }

  return (
    <EditorModal title="修改文件信息" onClose={() => { if (!saving) onClose() }}>
      {error && <p className="wb-form-error" role="alert">{error}</p>}
      {info ? (
        <form className="wb-form question-file-info" onSubmit={(event) => void submit(event)}>
          <p className="filepath">{info.path}</p>
          {fields.map(([field, label]) => (
            <label key={field}>{label}
              <input name={field} defaultValue={info[field]} required={field !== 'tags'} disabled={saving} />
            </label>
          ))}
          <small>类别代码示例 G（高考题）、M（模拟题）。多个知识板块用中文逗号分隔，第一个决定归档位置。</small>
          <div className="wb-form-actions">
            <button type="button" className="wb-btn" disabled={saving} onClick={onClose}>取消</button>
            <button type="submit" className="wb-btn wb-primary" disabled={saving}>{saving ? '正在保存…' : '保存文件信息'}</button>
          </div>
        </form>
      ) : !error && <p role="status">正在读取文件信息…</p>}
    </EditorModal>
  )
}
