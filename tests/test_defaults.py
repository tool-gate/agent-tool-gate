import json
import tempfile
import unittest
from pathlib import Path
from typing import Annotated
import core


class DefaultTests(unittest.TestCase):
    def test_omitted_defaults_are_checked(self):
        cases = [(str, 7, 'safe'), (int, True, 2), (int, -1, 2)]
        with tempfile.TemporaryDirectory() as temporary:
            register, run = core.registry(Path(temporary) / 'memory.jsonl')
            for index, (expected, default, safe) in enumerate(cases):
                with self.subTest(default=default):
                    calls = []
                    def valid(value):
                        return value > 0 if expected is int else True
                    def tool(value: Annotated[expected, valid] = default):
                        calls.append(value)
                        return value
                    name = str(index)
                    register(name, tool)
                    self.assertIn('error', json.loads(run(name, name)))
                    self.assertEqual(calls, [])
                    self.assertEqual(json.loads(run(name, name, safe)), {'result': safe})
                    self.assertEqual(calls, [safe])

    def test_none_requires_author_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            register, run = core.registry(Path(temporary) / 'memory.jsonl')
            def tool(value: Annotated[int, lambda value: value > 0] = None):
                return value
            register('optional', tool)
            self.assertEqual(json.loads(run('task', 'optional')), {'result': None})
