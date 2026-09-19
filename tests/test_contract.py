import contextlib
import io
import json
from pathlib import Path
import tempfile
from typing import Annotated
import unittest
from unittest.mock import patch
import core


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'memory.jsonl'
        self.register, self.run = core.registry(self.path)

    def refused(self, *args, expected="rejected", **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                result = self.run(*args, **kwargs)
            except BaseException as error:
                self.fail('exception escaped: ' + type(error).__name__)
        answer = json.loads(result)
        self.assertEqual(answer['outcome'], expected)
        self.assertEqual(answer['body_started'], expected == 'unknown')
        self.assertTrue(answer['error'].startswith('NE_ZNAYU:'))
        records = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual(records[-1]['event'], 'execution_unknown' if expected == 'unknown' else 'refusal')
        self.assertEqual(records[-1]['error'], answer['error'])
        return answer

    def test_all_exception_classes(self):
        class BrokenError(Exception):
            def __str__(self):
                raise RuntimeError('broken formatter')
        for kind in (ValueError, RuntimeError, BaseException, BrokenError):
            with self.subTest(kind=kind.__name__):
                calls = []
                def fail(value):
                    raise kind()
                def guarded(value: Annotated[int, fail]):
                    calls.append(value)
                def broken():
                    raise kind()
                self.register(kind.__name__ + '_predicate', guarded)
                self.register(kind.__name__ + '_body', broken)
                self.refused('predicate', kind.__name__ + '_predicate', 1)
                self.assertEqual(calls, [])
                self.refused('body', kind.__name__ + '_body', expected='unknown')

    def test_context_types_and_missing_context(self):
        calls = []
        def tool():
            calls.append(True)
        self.register('tool', tool)
        with patch.object(core, 'before') as hook:
            for value in (['x'], 123, None):
                self.refused(value, 'tool')
                self.refused('task', value)
            self.refused()
            self.refused('task')
            hook.assert_not_called()
        self.assertEqual(calls, [])

    def test_tool_keyword_names_do_not_collide(self):
        def tool(task: Annotated[str, lambda value: True], name: Annotated[str, lambda value: True]):
            return task + name
        self.register('tool', tool)
        self.assertEqual(json.loads(self.run('context', 'tool', task='a', name='b')), {'result': 'ab'})
        self.refused('context', 'tool', extra='x')

    def test_unserializable_and_nonfinite_results(self):
        for index, value in enumerate((object(), {1}, float('nan'), float('inf'), -float('inf'))):
            with self.subTest(index=index):
                def tool():
                    return {'nested': [value]}
                self.register(str(index), tool)
                self.refused('serialize', str(index), expected='unknown')

    def test_hook_error_is_recorded(self):
        with patch.object(core, 'before', side_effect=RuntimeError('hook failed')):
            self.refused('task', 'unknown')

    def test_log_failure_still_returns_json(self):
        with patch.object(core, 'remember', side_effect=OSError('storage unavailable')):
            answer = json.loads(self.run('task', 'unknown'))
        self.assertTrue(answer['error'].startswith('NE_ZNAYU:'))
        self.assertEqual(answer['log_status'], 'unavailable')

    def test_memory_growth_and_show_limit(self):
        total = 80
        with contextlib.redirect_stdout(io.StringIO()):
            for index in range(total):
                answer = json.loads(self.run('same task', 'unknown'))
                self.assertIn('error', answer)
        rows = [json.loads(line) for line in self.path.read_text().splitlines()]
        refusals = [r for r in rows if r['event'] == 'refusal']
        shown = [r for r in rows if r['event'] == 'shown']
        self.assertEqual(len(refusals), total)
        self.assertEqual(len(shown), total - 1)
        self.assertTrue(all(0 < r['count'] <= core.SHOW_LIMIT for r in shown))
        self.assertTrue(all('record' not in r for r in shown))
        self.assertTrue(all('records' not in r for r in shown))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            hits = core.before('same task', self.path)
        self.assertEqual(len(hits), core.SHOW_LIMIT)
        self.assertEqual(len(output.getvalue().splitlines()), core.SHOW_LIMIT)
        self.assertEqual(len(self.path.read_text().splitlines()), len(rows) + 1)
        print(f'memory: calls={total}, refusals={len(refusals)}, shown={len(shown)}, rows={len(rows)}')

    def test_memory_ranking(self):
        for task in ('alpha', 'alpha beta', 'alpha beta gamma', 'unrelated'):
            core.remember({'event': 'refusal', 'task': task}, self.path)
        with contextlib.redirect_stdout(io.StringIO()):
            hits = core.before('alpha beta gamma', self.path)
        self.assertEqual([r['task'] for r in hits], ['alpha beta gamma', 'alpha beta', 'alpha'])
