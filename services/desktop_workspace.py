"""Persistent desktop workbench for drafts, PDF previews and reviewed revisions."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
from tempfile import NamedTemporaryFile
from threading import RLock
from uuid import uuid4

from services.desktop_jobs import DesktopJobs
from services.desktop_model import selected_provider
from services.desktop_revision import (
    changed_ranges, chat_revision, decode_attachments, offsets, rebase_selections, utf16_length,
)
from services.file_service import atomic_write_text
from services.local_agent import LOCAL_AGENTS, require_image_support
from services.local_process import check_cancelled
from services.pdf_import_service import import_pdf, inspect_pdf
from services.pdf_selection_edit import locate_pdf_selection
from services.tex_compile_service import compile_tex_preview
from services.tex_import_service import decode_tex, parse_tex_questions, replace_parsed_problem_header
from utils.core_config import CHAPTERS_DIR, SUBJECTS
from utils.image_assets import copy_graphics, graphics_references, resolve_image


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _source(params, fallback=''):
    value = params.get('source', fallback)
    if not isinstance(value, str) or '\x00' in value:
        raise ValueError('LaTeX 源码必须是文本。')
    if len(value.encode('utf-8')) > 4_000_000:
        raise ValueError('LaTeX 源码超过 4 MB，请拆分文件。')
    return value


def _title(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 240:
        raise ValueError('标题不能为空或超过 240 个字符。')
    return value.strip()


def _file(path, suffixes):
    if not isinstance(path, str) or not path.strip():
        raise ValueError('请选择一个文件。')
    candidate = Path(path).expanduser().resolve(strict=True)
    if candidate.suffix.lower() not in suffixes or not candidate.is_file():
        raise ValueError('请选择 ' + '、'.join(suffixes) + ' 文件。')
    return candidate


def _atomic_bytes(path, payload):
    with NamedTemporaryFile(prefix='.write-', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            handle.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


_SUMMARY = ('id', 'title', 'createdAt', 'updatedAt', 'sourcePath', 'pdfPath', 'previewPath',
            'chapter', 'originalPage', 'pdfPageCount', 'inputKind', 'revision')


class CompileError(RuntimeError):
    def __init__(self, result):
        super().__init__(result.get('error', '编译失败。'))
        self.log = result.get('log', '')


class DesktopWorkspace:
    def __init__(self, library: Path, state: Path):
        self.library = library.resolve()
        self.state = state.resolve()
        self.runtime = Path(__file__).resolve().parents[1]
        self.root = self.state / 'workspace'
        self.root.mkdir(parents=True, exist_ok=True)
        self.tex_support = self.state / 'tex-support'
        self.lock = RLock()
        self.pdf_lock = RLock()
        self.bank_lock = RLock()
        self.jobs = DesktopJobs()
        self.handlers = {
            'workspace.list': self.list,
            'workspace.open': self.open,
            'workspace.resume': self.resume,
            'workspace.create': self.create,
            'workspace.save': self.save,
            'workspace.import_pdf': self.import_document,
            'workspace.reconstruct': self.reconstruct,
            'workspace.compile': self.compile,
            'workspace.locate': self.locate,
            'workspace.chat': self.chat,
            'workspace.apply': self.apply,
            'workspace.dismiss': self.dismiss,
            'workspace.undo': self.undo,
            'workspace.chapters': self.chapters,
            'workspace.export': self.export,
            'workspace.export_tex': self.export_tex,
            'workspace.export_pdf': self.export_pdf,
            'task.get': self.jobs.get,
            'task.cancel': self.jobs.cancel,
        }

    def _directory(self, ident):
        if not isinstance(ident, str) or not re.fullmatch(r'[0-9a-f]{32}', ident):
            raise ValueError('草稿编号无效。')
        return self.root / ident

    def _read(self, ident):
        path = self._directory(ident) / 'document.json'
        if not path.is_file():
            raise ValueError('找不到这份草稿。')
        document = json.loads(path.read_text(encoding='utf-8'))
        if (not document.get('inputKind') and document.get('pdfPageCount') == 1
                and self._is_image_upload(document.get('_origin'))):
            document['inputKind'] = 'image'
        return document

    def _is_image_upload(self, origin):
        if not origin:
            return False
        path = Path(origin)
        return (path.suffix.lower() == '.pdf' and path.parent.parent == self.state / 'uploads'
                and re.fullmatch(r'[0-9a-f]{32}', path.parent.name) is not None)

    def _summaries(self):
        summaries = []
        for directory in self.root.iterdir():
            metadata = directory / 'document.json'
            if not re.fullmatch(r'[0-9a-f]{32}', directory.name) or not metadata.is_file():
                continue
            summary = directory / 'summary.json'
            try:
                if summary.stat().st_mtime_ns < metadata.stat().st_mtime_ns:
                    raise ValueError('stale summary')
                record = json.loads(summary.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                record = self._summary(self._read(directory.name))
                self._write_summary(summary, record)
            if (not record.get('inputKind') and record.get('pdfPageCount') == 1
                    and self._is_image_upload(record.get('_origin'))):
                record['inputKind'] = 'image'
                self._write_summary(summary, record)
            summaries.append(record)
        return sorted(summaries, key=lambda document: document['updatedAt'], reverse=True)

    @staticmethod
    def _summary(document):
        return {key: document[key] for key in (*_SUMMARY, '_origin') if key in document}

    @staticmethod
    def _write_summary(path, summary):
        try:
            atomic_write_text(str(path), json.dumps(summary, ensure_ascii=False, separators=(',', ':')))
        except OSError:
            pass  # The document remains authoritative if the lookup cache cannot be saved.

    def _write(self, document, *, touch=True):
        if touch:
            document['updatedAt'] = _now()
        directory = self._directory(document['id'])
        directory.mkdir(exist_ok=True)
        path = directory / 'source.tex'
        payload = document['source'].encode('utf-8')
        if not path.is_file() or path.read_bytes() != payload:
            _atomic_bytes(path, payload)
        document['sourcePath'] = str(path)
        atomic_write_text(str(directory / 'document.json'), json.dumps(document, ensure_ascii=False, separators=(',', ':')))
        self._write_summary(directory / 'summary.json', self._summary(document))

    @staticmethod
    def _public(document):
        fields = {key: value for key, value in document.items() if not key.startswith('_')}
        fields['proposals'] = [{key: value for key, value in proposal.items() if not key.startswith('_')}
                               for proposal in document['proposals']]
        preview = document.get('previewPath')
        fields['previewCurrent'] = bool(preview and document.get('_previewHash') == _sha(document['source']) and Path(preview).is_file())
        applied = document['_undo'][-1] if document['_undo'] else None
        fields['canUndo'] = bool(applied and applied['afterHash'] == _sha(document['source']))
        return fields

    def list(self, _params):
        with self.lock:
            return [{key: value for key, value in info.items() if key in _SUMMARY} for info in self._summaries()]

    def _open(self, document):
        path = self._directory(document['id']) / 'source.tex'
        payload = document['source'].encode('utf-8')
        if not path.exists() or path.read_bytes() != payload:
            _atomic_bytes(path, payload)
        atomic_write_text(str(self.root / 'active'), document['id'])
        return self._public(document)

    def open(self, params):
        with self.lock:
            return self._open(self._read(params.get('id') or params.get('documentId')))

    def resume(self, params):
        if params.get('id'):
            return self.open(params)
        with self.lock:
            active = self.root / 'active'
            if active.is_file():
                ident = active.read_text(encoding='utf-8').strip()
                if re.fullmatch(r'[0-9a-f]{32}', ident) and (self._directory(ident) / 'document.json').is_file():
                    return self._open(self._read(ident))
            summaries = self._summaries()
            return self._open(self._read(summaries[0]['id'])) if summaries else None

    def _question(self, relative):
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError('题目路径必须位于当前题库。')
        chapters = (self.library / 'chapters').resolve()
        candidate = _file(str(chapters / relative), ('.tex',))
        if not candidate.is_relative_to(chapters):
            raise ValueError('题目路径必须位于当前题库。')
        return candidate, candidate.relative_to(chapters).parts[0]

    def create(self, params):
        tex_path, pdf_path, chapter = None, None, None
        if params.get('questionPath'):
            tex_path, chapter = self._question(params['questionPath'])
        if params.get('path'):
            origin = _file(params['path'], ('.tex', '.pdf'))
            if origin.suffix.lower() == '.pdf':
                pdf_path = origin
            else:
                tex_path = origin
        if params.get('pdfPath'):
            pdf_path = _file(params['pdfPath'], ('.pdf',))
        input_kind = params.get('inputKind')
        if input_kind is not None and (input_kind != 'image' or not pdf_path or tex_path):
            raise ValueError('图片草稿需要附带图片转换后的 PDF。')
        preview_path = None
        if params.get('previewPath'):
            preview_path = _file(params['previewPath'], ('.pdf',))
            if not preview_path.is_relative_to(self.state / 'demo'):
                raise ValueError('仅可使用应用提供的示例预览。')
        origin_path = tex_path or pdf_path
        origin_key = os.path.normcase(str(origin_path)) if origin_path else None
        if origin_key:
            with self.lock:
                for existing in self._summaries():
                    if existing.get('_origin') == origin_key:
                        return self._open(self._read(existing['id']))

        original_source = decode_tex(tex_path.read_bytes()) if tex_path else ''
        source = _source(params, original_source)
        title = _title(params.get('title') or (origin_path.stem if origin_path else '未命名草稿'))
        original_pdf = pdf_path.read_bytes() if pdf_path else None
        page_count = None
        if original_pdf is not None:
            with self.pdf_lock:
                page_count = inspect_pdf(original_pdf)
        if input_kind == 'image' and page_count != 1:
            raise ValueError('图片草稿只能包含一页。')
        ident = uuid4().hex
        directory = self._directory(ident)
        directory.mkdir()
        document = dict(id=ident, title=title, source=source, selections=[], activeSelectionId=None,
                        messages=[], proposals=[], createdAt=_now(), updatedAt=_now(), revision=0,
                        _undo=[], _origin=origin_key)
        try:
            if tex_path:
                document['_sourceDirectory'] = str(tex_path.parent)
                document['source'] = self._copy_input_graphics(source, tex_path.parent, directory)
            if original_pdf is not None:
                original_page = params.get('originalPage', 1)
                if type(original_page) is not int or not 1 <= original_page <= page_count:
                    raise ValueError(f'原卷页码应在 1 至 {page_count} 之间。')
                original = directory / 'original.pdf'
                _atomic_bytes(original, original_pdf)
                document.update(pdfPath=str(original), pdfPageCount=page_count, originalPage=original_page)
                if input_kind == 'image' or (page_count == 1 and self._is_image_upload(origin_key)):
                    document['inputKind'] = 'image'
            if chapter:
                document['chapter'] = chapter
            if preview_path:
                preview = directory / 'demo-preview.pdf'
                shutil.copyfile(preview_path, preview)
                document['previewPath'] = str(preview)
                document['_previewHash'] = _sha(document['source'])
            with self.lock:
                self._write(document)
                return self._open(document)
        except BaseException:
            # The UUID directory was created exclusively by this request.
            if directory.parent == self.root:
                shutil.rmtree(directory, ignore_errors=True)
            raise

    def _copy_input_graphics(self, source, original_dir, directory):
        for ref in reversed(graphics_references(source)):
            path = None
            for root in (self.library, self.runtime):
                try:
                    path = resolve_image(ref['path'], root, original_dir, source)
                    break
                except (FileNotFoundError, ValueError):
                    continue
            if path is None:
                continue  # Editable import retains unresolved references for repair.
            fragment = ref['code']
            left = ref['path_start'] - ref['start']
            right = ref['path_end'] - ref['start']
            resolved = fragment[:left] + path.as_posix() + fragment[right:]
            updated = copy_graphics(resolved, directory / 'images', self.runtime, reference_base=directory)
            source = source[:ref['start']] + updated + source[ref['end']:]
        return source

    @staticmethod
    def _validate_selections(source, records):
        if not isinstance(records, list) or len(records) > 100:
            raise ValueError('已保存的源码选区数量不能超过 100。')
        selections, ids = [], set()
        for record in records:
            offsets(source, record)
            ident = record.get('id') or uuid4().hex
            label = record.get('label', '')
            if (not isinstance(ident, str) or ident in ids or len(ident) > 128
                    or not isinstance(label, str) or len(label) > 240):
                raise ValueError('源码选区名称或编号无效。')
            ids.add(ident)
            selections.append({**record, 'id': ident, 'label': label})
        return selections

    @staticmethod
    def _check_revision(document, params):
        if params.get('revision', document['revision']) != document['revision']:
            raise ValueError('草稿已有新版本，请重新打开后再修改。')

    def _replace_source(self, document, updated, changes=None):
        original = document['source']
        if original == updated:
            return
        if changes is None:
            changes = changed_ranges(original, updated)
        document['selections'] = rebase_selections(document['selections'], original, changes)
        if document['activeSelectionId'] not in {item['id'] for item in document['selections']}:
            document['activeSelectionId'] = None
        document['source'] = updated
        document['revision'] += 1

    def save(self, params):
        with self.lock:
            document = self._read(params.get('id'))
            self._check_revision(document, params)
            self._replace_source(document, _source(params, document['source']))
            if 'title' in params:
                document['title'] = _title(params['title'])
            if 'selections' in params:
                document['selections'] = self._validate_selections(document['source'], params['selections'])
            if 'activeSelectionId' in params:
                document['activeSelectionId'] = params['activeSelectionId']
            if not any(selection['id'] == document['activeSelectionId'] for selection in document['selections']):
                document['activeSelectionId'] = None
            if 'originalPage' in params:
                page = params['originalPage']
                if type(page) is not int or not 1 <= page <= document.get('pdfPageCount', 0):
                    raise ValueError('原卷页码无效。')
                document['originalPage'] = page
            self._write(document)
            return self._public(document)

    @staticmethod
    def _defaults(document):
        return dict(year='待填写', paper_type='M', paper=document['title'], number='1',
                    subject=document.get('chapter', '未分类'))

    def import_document(self, params):
        if not params.get('path'):
            raise ValueError('请选择原卷 PDF。')
        _file(params['path'], ('.pdf',))
        require_image_support(selected_provider(self.state))
        created = self.create({'path': params['path'], 'title': params.get('title')})
        return self.reconstruct({**params, 'id': created['id']})

    def _linked_bank_file(self, document, relative_path):
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError('题库文件路径无效。')
        path, _ = self._question(relative_path)
        if document.get('_origin') != os.path.normcase(str(path)):
            raise ValueError('当前工作稿没有关联到该题库文件。')
        return path

    def file_info(self, params):
        from services.desktop_file_info import read_file_info

        with self.lock:
            document = self._read(params.get('id'))
            self._linked_bank_file(document, params.get('path'))
            return read_file_info(self.library, params['path'])

    def save_file_info(self, params):
        from services.desktop_file_info import read_file_info, save_file_info

        with self.lock:
            document = self._read(params.get('id'))
            self._check_revision(document, params)
            path = self._linked_bank_file(document, params.get('path'))
            before = read_file_info(self.library, params['path'])
            keys = ('year', 'paperType', 'paper', 'number', 'subject')
            # Validate the editable draft before changing its bank file.
            replace_parsed_problem_header(document['source'], (before[key] for key in keys))
            saved = save_file_info(self.library, params['path'], params.get('fields'), params.get('expectedHash'))
            target, chapter = self._question(saved['path'])
            updated = replace_parsed_problem_header(document['source'], (saved[key] for key in keys))
            self._replace_source(document, updated)
            document['_origin'] = os.path.normcase(str(target))
            document['chapter'] = chapter
            if document['title'] == path.stem:
                document['title'] = target.stem
            self._write(document)
            return {'file': saved, 'document': self._public(document)}

    def reconstruct(self, params):
        provider = selected_provider(self.state)
        with self.lock:
            document = self._read(params.get('id'))
            if not document.get('pdfPath'):
                raise ValueError('当前草稿还没有原卷 PDF。')
        require_image_support(provider)
        return self.jobs.submit(lambda job: self._reconstruct(document, params, job), document['id'], provider=provider)

    def _reconstruct(self, document, params, job):
        original = Path(document['pdfPath']).read_bytes()
        pages = params.get('pages')
        first = params.get('firstPage', 1)
        last = params.get('lastPage')
        page_count = document['pdfPageCount']
        source = import_pdf(original, resource_dir=self._directory(document['id']),
                            defaults=self._defaults(document), first_page=first, last_page=last, pages=pages,
                            prompt_library=self.library,
                            pdf_lock=self.pdf_lock, on_progress=lambda page, total: job.progress(
                                '正在识别图片' if document.get('inputKind') == 'image' else f'正在识别原卷第 {page} 页',
                                page=page, total=total, pageCount=page_count))
        check_cancelled()
        original_page = min(pages) if pages else first
        if document['source'].strip() and source != document['source']:
            changes = changed_ranges(document['source'], source)
            proposal = self._proposal(document, changes, 'document', None, job)
        else:
            proposal = None

        def commit():
            with self.lock:
                latest = self._read(document['id'])
                self._require_same_source(document, latest)
                if proposal:
                    latest['proposals'].append(proposal)
                else:
                    self._replace_source(latest, source)
                latest['originalPage'] = original_page
                self._write(latest)
                return self._public(latest)

        job.publish(commit)

    def _compile_pdf(self, document, source, engine='xelatex'):
        check_cancelled()
        work = self._directory(document['id'])
        preview_dir = work / 'previews'
        preview_dir.mkdir(exist_ok=True)
        search = [self.library] + ([Path(document['_sourceDirectory'])] if document.get('_sourceDirectory') else [])
        if not isinstance(engine, str):
            raise ValueError('TeX 编译器选项无效。')
        # Kpathsea on Windows can fail to resolve a non-ASCII install path in
        # TEXINPUTS when the editor state is on a different path or drive.
        # Provide the pinned templates in an ASCII-named sibling directory.
        with self.lock:
            self.tex_support.mkdir(exist_ok=True)
            for name in ('main.tex', 'UtopiaPhyCyclus_book.cls', 'warm-physics.sty'):
                installed, staged = self.runtime / name, self.tex_support / name
                if installed.is_file():
                    payload = installed.read_bytes()
                    if not staged.is_file() or staged.read_bytes() != payload:
                        _atomic_bytes(staged, payload)
        result = compile_tex_preview(source, str(self.tex_support), self._defaults(document), engine=engine,
                                     resource_dir=str(work), cache_dir=self.state / 'tex-cache', search_dirs=search)
        if not result['ok']:
            raise CompileError(result)
        check_cancelled()
        path = preview_dir / f'{uuid4().hex}.pdf'
        source_map = result.get('source_map')
        if source_map:
            _atomic_bytes(path.with_suffix('.synctex.gz'), source_map['synctex'])
            mapping = dict(lines=source_map['lines'], source_hash=source_map['source_hash'].hex())
            atomic_write_text(str(path.with_suffix('.map.json')), json.dumps(mapping))
        _atomic_bytes(path, result['pdf'])
        return str(path), result['log']

    @staticmethod
    def _source_map(preview):
        path = Path(preview)
        metadata = path.with_suffix('.map.json')
        if not metadata.is_file():
            return None
        result = json.loads(metadata.read_text(encoding='utf-8'))
        return dict(source_hash=bytes.fromhex(result['source_hash']), synctex=path.with_suffix('.synctex.gz').read_bytes(),
                    lines={int(key): tuple(value) if isinstance(value, list) else value
                           for key, value in result['lines'].items()})

    def compile(self, params):
        with self.lock:
            document = self._read(params.get('id'))
        source = _source(params, document['source'])
        target = params.get('target', 'preview')
        if not source.strip():
            raise ValueError('请先录入需要编译的 LaTeX 源码。')
        if target not in ('preview', 'before', 'after'):
            raise ValueError('预览目标无效。')

        def compile_job(job):
            job.progress('正在编译 LaTeX 预览')
            path, log = self._compile_pdf(document, source, params.get('engine', 'xelatex'))

            def commit():
                with self.lock:
                    latest = self._read(document['id'])
                    if target == 'preview' and latest['source'] == source:
                        latest['previewPath'] = path
                        latest['_previewHash'] = _sha(source)
                        self._write(latest, touch=False)
                return dict(previewPath=path, source=source, revision=document['revision'], log=log)

            job.publish(commit)

        return self.jobs.submit(compile_job, document['id'])

    def locate(self, params):
        with self.lock:
            document = self._read(params.get('id'))
            self._check_revision(document, params)
            source = document['source']
            preview = document.get('previewPath')
            if not preview or preview != params.get('previewPath') or document.get('_previewHash') != _sha(source):
                raise ValueError('预览与源码版本不一致，请重新编译并框选。')

        def locate_job(job):
            job.progress('正在定位选区对应的 LaTeX 源码')
            span = locate_pdf_selection(source, Path(preview).read_bytes(), params.get('page'), params.get('rect'),
                                        source_map=self._source_map(preview), pdf_lock=self.pdf_lock)
            job.publish(lambda: dict(start=utf16_length(source[:span['start']]), end=utf16_length(source[:span['end']])))

        return self.jobs.submit(locate_job, document['id'])

    def _proposal(self, document, changes, scope, selection, job):
        source = document['source']
        updated = source
        for start, stop, after in reversed(changes):
            updated = updated[:start] + after + updated[stop:]
        job.progress('正在编译修改前后对照')
        before_path = document.get('previewPath') if document.get('_previewHash') == _sha(source) else None
        if not before_path and source.strip():
            try:
                before_path, _log = self._compile_pdf(document, source)
            except CompileError:
                pass  # A repair can still be reviewed if the previous source did not compile.
        after_path, _log = self._compile_pdf(document, updated)
        difference = changed_ranges(source, updated)
        start, end, after = difference[0] if difference else (0, 0, '')
        proposal = dict(id=uuid4().hex, scope=scope, before=source[start:end], after=after,
                        baseSource=source, source=updated, createdAt=_now(), status='pending',
                        afterPath=after_path,
                        _changes=changes,
                        changes=[dict(start=utf16_length(source[:left]), end=utf16_length(source[:right]),
                                      before=source[left:right], after=value) for left, right, value in changes])
        if before_path:
            proposal['beforePath'] = before_path
        if selection is not None:
            proposal['selection'] = selection
        return proposal

    @staticmethod
    def _require_same_source(snapshot, latest):
        if latest['revision'] != snapshot['revision'] or latest['source'] != snapshot['source']:
            raise ValueError('执行期间草稿已编辑，请使用当前版本重新发起。')

    @staticmethod
    def _message(role, scope, value, **data):
        return dict(id=uuid4().hex, role=role, text=value, scope=scope, createdAt=_now(), **data)

    def chat(self, params):
        prompt = params.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 50_000:
            raise ValueError('请输入有效的修改要求或问题。')
        scope = params.get('scope', 'document')
        if scope not in ('selection', 'document'):
            raise ValueError('对话范围无效。')
        attachments, images = decode_attachments(params.get('attachments', []))
        provider = params.get('agent', selected_provider(self.state))
        if not isinstance(provider, str) or provider != 'api' and provider not in LOCAL_AGENTS:
            raise ValueError('请选择可用的本机 Agent。')
        with self.lock:
            document = self._read(params.get('id'))
            self._check_revision(document, params)
            new_source = _source(params, document['source'])
            self._replace_source(document, new_source)
            pdf_selections = []
            if scope == 'selection':
                pdf_selections = params.get('pdfSelections', [params['pdfSelection']] if params.get('pdfSelection') else [])
                if (not isinstance(pdf_selections, list) or len(pdf_selections) > 8
                        or any(not isinstance(item, dict) or item.get('panel', 'preview') != 'preview'
                               or 'page' not in item or 'rect' not in item for item in pdf_selections)):
                    raise ValueError('在当前预览上框选后可精确修改。')
            ranges = []
            if scope == 'selection':
                if 'selection' in params:
                    ranges = [params['selection']]
                elif 'selections' in params:
                    ranges = params['selections']
                elif not pdf_selections and document['activeSelectionId']:
                    ranges = [item for item in document['selections'] if item['id'] == document['activeSelectionId']]
                if not ranges and not pdf_selections:
                    raise ValueError('请先选中源码或框选预览。')
                ranges = self._validate_selections(new_source, ranges)
            if pdf_selections and document.get('_previewHash') != _sha(new_source):
                raise ValueError('源码已修改，请重新编译后在新预览框选。')
            if any(item.get('previewPath', document.get('previewPath')) != document.get('previewPath')
                   for item in pdf_selections):
                raise ValueError('预览已刷新，请在当前页面重新框选。')
            if images or pdf_selections:
                require_image_support(provider)
            history = document['messages'][:]
            if 'historyAfter' in params:
                boundary = params['historyAfter']
                if boundary is None:
                    history = []
                else:
                    for index, message in enumerate(history):
                        if message['id'] == boundary:
                            history = history[index + 1:]
                            break
                    else:
                        raise ValueError('这条任务的对话起点已失效，请重新创建任务。')
            user_message = self._message('user', scope, prompt.strip(), attachments=attachments)
            document['messages'].append(user_message)
            self._write(document)

        def chat_job(job):
            job.progress('正在生成答复')
            pdf, source_map = None, None
            if pdf_selections:
                preview = document.get('previewPath')
                if not preview:
                    raise ValueError('请先编译当前草稿。')
                pdf = Path(preview).read_bytes()
                source_map = self._source_map(preview)
            reply, changes = chat_revision(new_source, prompt, scope, ranges, history, images,
                                           pdf=pdf, pdf_selections=pdf_selections, source_map=source_map, pdf_lock=self.pdf_lock)
            check_cancelled()
            proposal = self._proposal(document, changes, scope, ranges[0] if len(ranges) == 1 else None, job) if changes else None

            def commit():
                with self.lock:
                    latest = self._read(document['id'])
                    extra = {}
                    if proposal:
                        latest['proposals'].append(proposal)
                        extra['proposalId'] = proposal['id']
                    latest['messages'].append(self._message('assistant', scope, reply, **extra))
                    self._write(latest)
                    return self._public(latest)

            job.publish(commit)

        return self.jobs.submit(chat_job, document['id'], provider=provider)

    @staticmethod
    def _find_proposal(document, proposal_id):
        for proposal in document['proposals']:
            if proposal['id'] == proposal_id:
                return proposal
        raise ValueError('找不到该修改提案。')

    def apply(self, params):
        with self.lock:
            document = self._read(params.get('id'))
            proposal = self._find_proposal(document, params.get('proposalId'))
            self._check_revision(document, params)
            if proposal['status'] == 'applied' and document['source'] == proposal['source']:
                return self._public(document)
            if proposal['status'] != 'pending':
                raise ValueError('只有待审阅的提案可采用。')
            if proposal['baseSource'] != document['source'] or _source(params, document['source']) != proposal['baseSource']:
                raise ValueError('提案依据的源码已有改动，请重新生成后采用。')
            document['_undo'].append(dict(id=proposal['id'], selections=document['selections'],
                                          activeSelectionId=document['activeSelectionId'],
                                          previewPath=document.get('previewPath'), previewHash=document.get('_previewHash'),
                                          afterHash=_sha(proposal['source'])))
            self._replace_source(document, proposal['source'], proposal['_changes'])
            document['previewPath'] = proposal['afterPath']
            document['_previewHash'] = _sha(proposal['source'])
            proposal['status'] = 'applied'
            self._write(document)
            return self._public(document)

    def dismiss(self, params):
        with self.lock:
            document = self._read(params.get('id'))
            proposal = self._find_proposal(document, params.get('proposalId'))
            if proposal['status'] not in ('pending', 'dismissed'):
                raise ValueError('只有待审阅的提案可放弃。')
            proposal['status'] = 'dismissed'
            self._write(document)
            return self._public(document)

    def undo(self, params):
        with self.lock:
            document = self._read(params.get('id'))
            self._check_revision(document, params)
            if not document['_undo']:
                raise ValueError('还没有可撤回的提案。')
            undo = document['_undo'][-1]
            if undo['afterHash'] != _sha(document['source']) or _source(params, document['source']) != document['source']:
                raise ValueError('采用后又编辑了草稿，请恢复到采用时的内容后再撤回。')
            proposal = self._find_proposal(document, undo['id'])
            self._replace_source(document, proposal['baseSource'])
            document.update(selections=undo['selections'], activeSelectionId=undo['activeSelectionId'])
            if undo.get('previewPath'):
                document.update(previewPath=undo['previewPath'], _previewHash=undo['previewHash'])
            else:
                document.pop('previewPath', None)
                document.pop('_previewHash', None)
            document['_undo'].pop()
            proposal['status'] = 'undone'
            self._write(document)
            return self._public(document)

    def chapters(self, _params):
        root = self.library / 'chapters'
        entries = [path.name for path in root.iterdir() if path.is_dir() and not path.name.startswith('.')]
        return list(dict.fromkeys([*SUBJECTS, *sorted(entries)]))

    def _output(self, name, suffix):
        if not isinstance(name, str) or not name.strip() or Path(name).suffix.lower() != suffix:
            raise ValueError(f'请选择另存为 {suffix} 的位置。')
        path = Path(name).resolve()
        if path.is_relative_to(self.root) or path.is_relative_to(self.library / 'chapters'):
            raise ValueError('请使用保存草稿或题库入库来更新该位置。')
        return path

    def export_tex(self, params):
        with self.lock:
            document = self._read(params.get('id'))
        source = _source(params, document['source'])
        path = self._output(params.get('path'), '.tex')
        source = copy_graphics(source, path.parent / (path.stem + ' 相关图'), self.library,
                               self._directory(document['id']), reference_base=path.parent)
        atomic_write_text(str(path), source)
        return {'path': str(path)}

    def export_pdf(self, params):
        with self.lock:
            document = self._read(params.get('id'))
        source = _source(params, document['source'])
        path = self._output(params.get('path'), '.pdf')
        if not source.strip():
            raise ValueError('请先录入需要导出的 LaTeX 源码。')

        def export_job(job):
            job.progress('正在导出 PDF')
            preview = document.get('previewPath')
            if not preview or document.get('_previewHash') != _sha(source) or not Path(preview).is_file():
                preview, _log = self._compile_pdf(document, source)
            content = Path(preview).read_bytes()

            def commit():
                _atomic_bytes(path, content)
                return {'path': str(path)}

            job.publish(commit)

        return self.jobs.submit(export_job, document['id'])

    def export(self, params):
        with self.lock:
            document = self._read(params.get('id'))
        source = _source(params, document['source'])
        chapter = params.get('chapter')
        if not isinstance(chapter, str) or chapter not in self.chapters({}):
            raise ValueError('请选择题库中的知识板块。')
        defaults = self._defaults(document)
        defaults.update(subject=chapter)
        for original_key, target in (('title', 'paper'), ('year', 'year'), ('paperType', 'paper_type'), ('number', 'number')):
            if original_key in params:
                defaults[target] = _title(str(params[original_key]))
        questions = parse_tex_questions(source, '草稿.tex', defaults)
        tags = params.get('tags', [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) or '\n' in tag or '\r' in tag for tag in tags):
            raise ValueError('题目标签应为文本列表。')

        def export_job(job):
            job.progress('正在保存到题库')
            job.publish(lambda: self._export_questions(document, questions, chapter, tags, params))

        return self.jobs.submit(export_job, document['id'])

    def _export_questions(self, document, questions, chapter, tags, params):
        from services.question_service import QuestionDuplicateError, create_question_file
        from services.question_type_service import infer_question_type, require_question_type
        from utils.csv_ops import get_next_id
        from utils.latex_ops import inject_meta_data, parse_meta_data

        chapters = (self.library / 'chapters').resolve()
        if Path(CHAPTERS_DIR).resolve() != chapters:
            raise RuntimeError('题库目录与当前连接不一致，已取消写入。')
        prepared = []
        for question in questions:
            question.subject = chapter
            if 'title' in params:
                question.paper = _title(params['title'])
            if 'year' in params:
                question.year = _title(str(params['year']))
            if 'paperType' in params:
                question.paper_type = _title(params['paperType'])
            filename = question.filename
            year, paper_type, paper, number, subject = filename[:-4].split('-')
            root = chapters / chapter / year
            path = (root / filename).resolve()
            if not path.is_relative_to(chapters):
                raise ValueError('题目文件路径超出当前题库。')
            meta, body = parse_meta_data(question.content)
            fields = (year, paper_type, paper, number, subject)
            body = replace_parsed_problem_header(body, fields)
            meta['题型'] = require_question_type(params.get('kind') or infer_question_type(question.content, subject))
            if tags:
                meta['标签'] = '，'.join(tag.strip() for tag in tags if tag.strip())
            for ref in graphics_references(body):
                resolve_image(ref['path'], self.library, self._directory(document['id']), body)
            prepared.append((path, meta, body, fields))
        if len({os.path.normcase(str(item[0])) for item in prepared}) != len(prepared):
            raise ValueError('草稿内有多道题将生成同名文件，请调整题号。')
        files = []
        with self.bank_lock:
            for path, meta, body, fields in prepared:
                if path.exists():
                    files.append(dict(file=path.name, status='skip', message='同名题目已存在。'))
                    continue
                try:
                    meta.update(ID=str(get_next_id()), **{'组卷引用次数': '0'})
                    question_id = create_question_file(str(path), inject_meta_data(body, meta), *fields,
                                                       resource_dir=self._directory(document['id']), project_dir=self.library)
                    files.append(dict(file=path.name, status='success', path=str(path), id=question_id, message='已保存'))
                except QuestionDuplicateError:
                    files.append(dict(file=path.name, status='skip', message='题干与现有题目完全相同。'))
                except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
                    files.append(dict(file=path.name, status='error', message=str(error)))
        return {'files': files}
