import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image
from requests import Timeout

from services import batch_assessment_service as assessment
from services.tex_import_service import TexQuestion
from utils.core_config import SUBJECTS
from utils.latex_ops import inject_meta_data, parse_meta_data


def question(body, subject='未分类'):
    text = r'\begin{problem}{2026}{M}{评估测试}{1}{' + subject + '}' + body + r'\end{problem}'
    return TexQuestion(text, '2026', 'M', '评估测试', '1', subject)


def evidence():
    return {'parts': [{'label': '整题', 'required': True, 'routes': [{
        'core_subject': '牛顿运动定律',
        'points': [{'name': '胡克定律', 'subject': '平衡力学'}, {'name': '牛二', 'subject': '牛顿运动定律'}],
        'steps': [{'id': 'F', 'conclusion': '由弹簧伸长量求弹力', 'depends_on': []},
                  {'id': 'a', 'conclusion': '由水平合力求加速度', 'depends_on': ['F']}],
        'coupling': 1, 'novelty': 0, 'reason': '用弹力求加速度，弹力是两个定律共有的量。',
    }]}]}


class AssessmentServiceTests(unittest.TestCase):
    def assess(self, q, **kwargs):
        return assessment.assess_batch_question(q, SUBJECTS, {}, **kwargs)

    def test_different_backgrounds_with_the_same_physical_model_keep_their_stars(self):
        cache = {}
        with patch.object(assessment, '_request_json', side_effect=lambda *a, **kw: evidence()) as api:
            results = []
            for body in ('弹簧牵引光滑实验台上的木块', '弹簧牵引无摩擦水平运输线上的货箱'):
                q = self.assess(question(body + '，由伸长量求加速度。'), cache=cache)
                meta = parse_meta_data(q.content)[0]
                results.append((meta['难度星级'], assessment.assessment_detail(meta)['parts'][0]['N']))
            self.assertEqual(api.call_count, 2)
            self.assertEqual(results, [('2.0', 0), ('2.0', 0)])

    @patch.object(assessment, '_request_json', side_effect=lambda *a, **kw: evidence())
    def test_cache_alternative_solutions_and_teacher_fields(self, request):
        cache = {}
        q = question('弹簧拉动光滑桌面上的木块，求加速度。')
        q.content = inject_meta_data(q.content, {'ID': '8', '题型': '计算题', '备注': '保留'})
        rated = self.assess(q, cache=cache)
        self.assertEqual(rated.subject, '牛顿运动定律，平衡力学')
        meta, _ = parse_meta_data(rated.content)
        self.assertEqual((meta['难度星级'], meta['评分状态'], meta['ID']), ('2.0', '暂评', '8'))
        self.assess(rated)
        self.assess(q, cache=cache)
        self.assertEqual(request.call_count, 1)
        meta.update(难度星级='3.5', 知识点='弹簧弹力')
        changed = inject_meta_data(rated.content, meta) + '\n' + r'\begin{solutions}先求弹力。\end{solutions}'
        changed += '\n' + r'\begin{solutions}[另解]第二种解法。\end{solutions}'
        corrected = self.assess(replace(rated, content=changed), cache=cache)
        metadata, _ = parse_meta_data(corrected.content)
        self.assertEqual((metadata['难度星级'], metadata['知识点'], metadata['评分状态']), ('3.5', '弹簧弹力', '正式'))
        self.assertEqual(json.loads(metadata['评分明细'])['difficulty'], '2.0')
        source = request.call_args.args[2]
        self.assertIn('第二种解法', source)
        self.assertNotIn('评估测试', source)
        self.assertNotIn('3.5', source)
        self.assertEqual(request.call_count, 2)

    @patch.object(assessment, '_request_json', side_effect=lambda *a, **kw: evidence())
    def test_image_bytes_invalidate_even_at_same_path(self, request):
        with TemporaryDirectory() as directory:
            path = Path(directory) / '图.png'
            q = question(r'\includegraphics[width=2cm]{图.png}')
            Image.new('RGB', (9, 9), 'white').save(path)
            first = self.assess(q, project_dir=directory)
            messages = request.call_args.args[2]
            self.assertTrue(messages[-1]['image_url']['url'].startswith('data:image/png;base64,'))
            self.assess(first, project_dir=directory)
            self.assertEqual(request.call_count, 1)
            Image.new('RGB', (9, 9), 'black').save(path)
            self.assess(first, project_dir=directory)
            self.assertEqual(request.call_count, 2)

    def test_incomplete_question_gets_a_subject_after_solution_is_supplied(self):
        q = question('由图求加速度。')
        with patch.object(assessment, '_request_json', side_effect=[
                {'parts': [], 'insufficient_reason': '缺少伸长量及力的关系。'}, evidence()]):
            pending = self.assess(q)
            self.assertEqual(pending.subject, '未分类')
            self.assertEqual(parse_meta_data(pending.content)[0]['难度星级'], '')
            judged = self.assess(replace(pending, content=pending.content + r'\begin{solutions}由弹力求加速度。\end{solutions}'))
            self.assertEqual(judged.subject, '牛顿运动定律，平衡力学')
            self.assertNotIn('知识板块', parse_meta_data(judged.content)[0].get(assessment.MANUAL, ''))

    def test_error_does_not_mutate_or_poison_cache_and_manual_can_clear(self):
        q = question('求加速度。', '电场')
        q = assessment.mark_manual_fields(q, ['知识板块', '知识点'])
        cache = {}
        with patch.object(assessment, '_request_json', side_effect=[Timeout(), evidence()]):
            with self.assertRaisesRegex(ValueError, '超时'):
                self.assess(q, cache=cache)
            self.assertFalse(cache)
            rated = self.assess(q, cache=cache)
        self.assertEqual(rated.subject, '电场')
        meta, _ = parse_meta_data(rated.content)
        self.assertEqual(meta['知识点'], '')
        self.assertEqual(q.content.count('评分签名'), 0)

    def test_invalid_evidence_is_corrected_once_using_the_validator_message(self):
        invalid = evidence()
        invalid['parts'][0]['routes'][0]['points'].pop()
        with patch.object(assessment, '_request_json', side_effect=[invalid, evidence()]) as api:
            rated = self.assess(question('求弹簧作用下木块的加速度。'))
            self.assertIn('仅含一个知识点', api.call_args.args[1])
            self.assertEqual(parse_meta_data(rated.content)[0]['难度星级'], '2.0')
        with patch.object(assessment, '_request_json', return_value=invalid) as api:
            with self.assertRaises(ValueError):
                self.assess(question('求加速度。'))
            self.assertEqual(api.call_count, 2)

    def test_persisted_pending_question_moves_and_updates_same_index_row_after_solution(self):
        import utopia_phycyclus_app as app
        from utils import csv_ops
        from utils.index_schema import CSV_HEADERS
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '评估索引.csv').write_text(','.join(CSV_HEADERS) + '\n', encoding='utf-8-sig')
            q = question('求光滑水平桌面上木块的加速度。')
            with patch.object(assessment, '_request_json', return_value={'parts': [], 'insufficient_reason': '缺少示意图'}):
                q = self.assess(q)
            meta, _ = parse_meta_data(q.content)
            q.content = inject_meta_data(q.content, {**meta, '题型': '计算题'})
            old = root / q.subject / q.year / q.filename
            old.parent.mkdir(parents=True)
            old.write_text(q.content, encoding='utf-8')
            with (patch.object(app, 'CHAPTERS_DIR', str(root)), patch.object(csv_ops, 'CHAPTERS_DIR', str(root)),
                  patch.object(csv_ops, 'CSV_INDEX_PATH', str(root / '评估索引.csv')),
                  patch.object(app, 'record_operation'), patch.object(app, '_invalidate_semantic_for_file'),
                  patch.object(app, 'backup_existing_file'), patch.object(app.st, 'session_state', {}),
                  patch.object(app, '_read_root_ai_env_config', return_value={}),
                  patch.object(assessment, '_request_json', return_value=evidence())):
                qid = csv_ops.add_to_csv_index(str(old), q.content, q.year, q.paper_type, q.paper, q.number, q.subject)
                saved, content = app._save_and_index_content_edit(str(old), q.content + r'\begin{solutions}联用两条定律。\end{solutions}')
                row, = csv_ops.read_csv_index()
                self.assertEqual(row['题目ID'], str(qid))
                self.assertEqual(row['难度星级'], '2.0')
                self.assertEqual(row['知识点'], '胡克定律，牛顿第二定律')
                self.assertEqual(row['知识板块'], '牛顿运动定律，平衡力学')
                self.assertEqual(Path(saved).parent.parent.name, '牛顿运动定律')
                self.assertIn('{牛顿运动定律，平衡力学}', content)
                self.assertEqual(parse_meta_data(Path(saved).read_text(encoding='utf-8'))[0]['评分状态'], '正式')
                self.assertFalse(old.exists())
