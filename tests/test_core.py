import contextlib
import inspect
import io
import json
from pathlib import Path
import tempfile
from typing import Annotated
import unittest
from unittest.mock import patch
import core


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'memory.jsonl'
        self.register, self.run = core.registry(self.path)
        self.calls = []

        def tool(text: Annotated[str, lambda value: len(value) <= 10],
                 count: Annotated[int, lambda value: 1 <= value <= 3] = 1):
            self.calls.append((text, count))
            return text * count

        self.raw = tool
        self.assertIsNone(self.register('echo', tool))

    def denied(self, *args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            answer = json.loads(self.run('task', 'echo', *args, **kwargs))
        self.assertEqual(answer['outcome'], 'rejected')
        self.assertFalse(answer['body_started'])
        self.assertTrue(answer['error'].startswith('NE_ZNAYU:'))
        self.assertEqual(self.calls, [])

    def test_only_run_is_exposed(self):
        api = core.registry(self.path)
        self.assertEqual(len(api), 2)
        self.assertEqual([part.__name__ for part in api], ['register', 'run'])
        self.assertFalse(hasattr(self.run, '__wrapped__'))
        self.assertEqual(vars(self.run), {})
        self.denied('ok', extra=True)

    def test_shape(self):
        self.denied()
        self.denied('ok', extra=1)

    def test_types(self):
        self.denied(7)
        self.denied('ok', True)
        self.denied(None)

    def test_constraints(self):
        self.denied('text exceeds limit')
        self.denied('ok', 0)

    def test_refusal_raises_inside_boundary(self):
        with self.assertRaisesRegex(core.FailClosedError, '^NE_ZNAYU:'):
            core.gate(inspect.signature(self.raw), (7,), {})
        self.denied(7)

    def test_bound_arguments(self):
        bound = inspect.signature(self.raw).bind('safe', count=2)
        with patch.object(core, 'gate', return_value=bound):
            self.assertEqual(json.loads(self.run('task', 'echo', 'other')), {'result': 'safesafe'})
        self.assertEqual(self.calls, [('safe', 2)])

    def test_list_snapshot(self):
        original = ['safe']
        def validator(value):
            original[0] = 'changed'
            return value == ['safe']
        def tool(values: Annotated[list, validator]):
            return values
        self.register('list', tool)
        self.assertEqual(json.loads(self.run('task', 'list', original)), {'result': ['safe']})
        self.assertEqual(original, ['changed'])

    def test_schema(self):
        def plain(cmd: str):
            return cmd
        for tool in (lambda cmd: cmd, plain, 7):
            with self.assertRaises(core.FailClosedError):
                self.register('bad', tool)
        with self.assertRaises(core.FailClosedError):
            self.register('echo', self.raw)
        self.assertIn('error', json.loads(self.run('task', 'missing')))

    def test_hook_before_execution(self):
        record = {'event': 'refusal', 'task': 'inspect valve', 'error': 'invalid value'}
        core.remember(record, self.path)
        output = io.StringIO()
        def tool():
            self.assertIn('inspect valve', output.getvalue())
            return 'ok'
        self.register('checked', tool)
        with contextlib.redirect_stdout(output):
            self.assertEqual(json.loads(self.run('valve report', 'checked')), {'result': 'ok'})
        rows = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual(rows[0], record)
        self.assertEqual(rows[1]['event'], 'shown')
        self.assertTrue(rows[1]['worked'])

    def test_memory_failure_blocks_execution(self):
        self.path.write_text('{broken\n')
        self.assertIn('error', json.loads(self.run('task', 'echo', 'ok')))
        self.assertEqual(self.calls, [])

    def test_refusal_is_recorded(self):
        self.denied('ok', 0)
        self.assertEqual(json.loads(self.path.read_text())['event'], 'refusal')
