import contextlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Annotated
import unittest
from unittest.mock import patch
import core
import instruments


def shared_lock_probe(path, queue):
    with open(path) as stream:
        try:
            core.fcntl.flock(stream, core.fcntl.LOCK_EX | core.fcntl.LOCK_NB)
            queue.put(False)
        except BlockingIOError:
            queue.put(True)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'files'
        self.root.mkdir()
        self.memory = self.root.parent / 'memory.jsonl'
        self.run = instruments.build(self.root, self.memory)
        (self.root / 'data.txt').write_text('data')

    def call(self, name, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return json.loads(self.run('task', name, **kwargs))

    def test_read_file_unexpected_name_keyword(self):
        answer = json.loads(self.run('task', 'read_file', name='x'))
        self.assertTrue(answer['error'].startswith('NE_ZNAYU:'))
        self.assertEqual(json.loads(self.memory.read_text())['event'], 'refusal')

    def test_timeout_and_unknown_outcome(self):
        def launch(command, **kwargs):
            self.assertEqual(kwargs['timeout'], 5)
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        with patch.object(instruments.subprocess, 'run', side_effect=launch) as process:
            self.assertIn('error', self.call('run_program', program='wc', args=['-l', 'data.txt']))
        self.assertEqual(process.call_args.kwargs['timeout'], 5)

    def test_nonzero_exit_is_unknown(self):
        actual = subprocess.run
        def launch(command, **kwargs):
            return actual([sys.executable, '-c', 'raise SystemExit(7)'], **kwargs)
        with patch.object(instruments.subprocess, 'run', side_effect=launch):
            self.assertIn('error', self.call('run_program', program='wc', args=['-l', 'data.txt']))

    def test_new_file_mode(self):
        previous = os.umask(0)
        try:
            self.assertIn('result', self.call('write_file', path='private.txt', text='data'))
        finally:
            os.umask(previous)
        self.assertEqual(stat.S_IMODE((self.root / 'private.txt').stat().st_mode), 0o600)

    def test_variadic_schema_is_rejected(self):
        register, run = core.registry(self.memory)
        def tool(*values: Annotated[int, lambda value: True]):
            return values
        with self.assertRaises(core.FailClosedError):
            register('variadic', tool)

    def test_write_limit(self):
        self.assertIn('error', self.call('write_file', path='large.txt', text='x' * 65537))
        self.assertFalse((self.root / 'large.txt').exists())

    def test_read_limit(self):
        (self.root / 'large.txt').write_text('x' * 65537)
        self.assertEqual(self.call('read_file', path='large.txt'), {'result': 'x' * 65536})

    def test_root_must_exist(self):
        with self.assertRaises(FileNotFoundError):
            instruments.build(self.root / 'absent', self.memory)

    def test_executable_must_exist(self):
        with patch.object(instruments.shutil, 'which', return_value=str(self.root / 'absent')):
            with self.assertRaises(FileNotFoundError):
                instruments.build(self.root, self.memory)

    def test_before_holds_shared_lock_while_reading(self):
        import multiprocessing as mp
        core.remember({'event': 'refusal', 'task': 'match'}, self.memory)
        ctx = mp.get_context('spawn')
        original = core.json.loads
        observed = []
        def inspect_read(line):
            queue = ctx.Queue()
            process = ctx.Process(target=shared_lock_probe, args=(self.memory, queue))
            process.start()
            locked = queue.get(timeout=15)
            process.join(15)
            self.assertEqual(process.exitcode, 0)
            observed.append(locked)
            return original(line)
        with patch.object(core.json, 'loads', side_effect=inspect_read):
            with contextlib.redirect_stdout(io.StringIO()):
                core.before('match', self.memory)
        self.assertEqual(observed, [True])
