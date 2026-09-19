import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Annotated
from unittest.mock import patch
import core
import instruments

class MolchunyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'files'
        self.root.mkdir()
        self.memory = Path(self.temp.name) / 'memory.jsonl'
        self.run = instruments.build(self.root, self.memory)
        self.register, self.core_run = core.registry(self.memory)

    def call(self, name, **arguments):
        with contextlib.redirect_stdout(io.StringIO()):
            return json.loads(self.run('task', name, **arguments))

    # Kills defaults_after_snapshot
    def test_defaults_are_copied_by_snapshot(self):
        default_list = []
        def tool(values: Annotated[list, lambda v: True] = default_list):
            values.append(1)
            return values
        self.register('tool', tool)
        self.core_run('task', 'tool')
        self.assertEqual(default_list, [])

    # Kills write_without_encoding
    def test_write_uses_utf8(self):
        with patch.object(instruments.os, 'fdopen', wraps=instruments.os.fdopen) as mock:
            self.call('write_file', path='enc.txt', text='data')
            self.assertEqual(mock.call_args.kwargs.get('encoding'), 'utf-8')

    # Kills read_without_encoding
    def test_read_uses_utf8(self):
        (self.root / 'enc.txt').write_text('data')
        with patch.object(instruments.os, 'fdopen', wraps=instruments.os.fdopen) as mock:
            self.call('read_file', path='enc.txt')
            self.assertEqual(mock.call_args.kwargs.get('encoding'), 'utf-8')

    # Kills annotated_three_args
    def test_annotated_strict_length(self):
        def tool(val: Annotated[int, lambda x: True, 'extra']): pass
        with self.assertRaises(core.FailClosedError):
            self.register('tool', tool)

    # Kills regex_anchor_removed
    def test_regex_requires_exact_match(self):
        with patch.object(instruments.tempfile, 'mkstemp') as create:
            self.assertIn('error', self.call('write_file', path='bad/name', text='data'))
            create.assert_not_called()

    # Kills o_nonblock_removed в read_file (заглушка предотвращает вечное зависание тестов при мутации)
    def test_o_nonblock_used_in_read(self):
        (self.root / 'safe.txt').write_text('data')
        actual_open = os.open
        with patch.object(instruments.os, 'open', wraps=actual_open) as mock:
            self.call('read_file', path='safe.txt')
            self.assertTrue(mock.call_args.args[1] & os.O_NONBLOCK)

    # Kills o_nonblock_removed в run_program
    def test_o_nonblock_used_in_run(self):
        (self.root / 'safe.txt').write_text('data')
        actual_open = os.open
        with patch.object(instruments.os, 'open', wraps=actual_open) as mock:
            self.call('run_program', program='wc', args=['-l', 'safe.txt'])
            self.assertTrue(mock.call_args.args[1] & os.O_NONBLOCK)

    # Kills broad_except_before
    def test_before_does_not_mask_bugs(self):
        self.memory.write_text('{}\n', encoding='utf-8')
        with patch.object(core.json, 'loads', side_effect=AttributeError('bug in code')):
            with self.assertRaises(AttributeError):
                core.before('task', self.memory)

    # Kills casefold_to_lower
    def test_memory_ranking_is_case_insensitive_including_unicode(self):
        core.remember({'event': 'refusal', 'task': 'straße'}, self.memory)
        with contextlib.redirect_stdout(io.StringIO()):
            hits = core.before('STRASSE', self.memory)
        self.assertEqual(len(hits), 1)

    # Kills shown_without_task
    def test_shown_event_includes_truncated_task(self):
        core.remember({'event': 'refusal', 'task': 'probe'}, self.memory)
        with contextlib.redirect_stdout(io.StringIO()):
            core.before('probe', self.memory)
        shown = [json.loads(x) for x in self.memory.read_text().splitlines()][-1]
        self.assertEqual(shown.get('task'), 'probe')

    # Kills allow_nan_in_remember
    def test_remember_rejects_nan(self):
        with self.assertRaises(core.FailClosedError):
            core.remember({'event': 'refusal', 'val': float('nan')}, self.memory)
