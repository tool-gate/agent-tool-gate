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


class RevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'files'
        self.root.mkdir()
        self.memory = self.root.parent / 'memory.jsonl'
        self.register, self.run = core.registry(self.memory)

    def test_stops_escape_every_stage(self):
        for stop in (KeyboardInterrupt('stop'), SystemExit(3), GeneratorExit()):
            for stage in ('history', 'predicate', 'body', 'serialize', 'refusal_log'):
                with self.subTest(stop=type(stop).__name__, stage=stage):
                    register, run = core.registry(self.memory)
                    def raise_stop(*args, **kwargs):
                        raise stop
                    def tool(value: Annotated[int, raise_stop]):
                        self.fail('body called')
                    def body():
                        raise stop
                    register('guarded', tool)
                    register('body', body)
                    register('ok', lambda: 'ok')
                    if stage == 'history':
                        manager = patch.object(core, 'before', side_effect=raise_stop)
                        call = lambda: run('task', 'ok')
                    elif stage == 'predicate':
                        manager = contextlib.nullcontext()
                        call = lambda: run('task', 'guarded', 1)
                    elif stage == 'body':
                        manager = contextlib.nullcontext()
                        call = lambda: run('task', 'body')
                    elif stage == 'serialize':
                        manager = patch.object(core.json, 'dumps', side_effect=raise_stop)
                        call = lambda: run('task', 'ok')
                    else:
                        manager = patch.object(core, 'remember', side_effect=raise_stop)
                        call = lambda: run('task', 'missing')
                    with manager, self.assertRaises(type(stop)) as caught:
                        call()
                    self.assertIs(caught.exception, stop)

    def test_stop_does_not_attempt_logging(self):
        def tool():
            raise SystemExit(7)
        self.register('tool', tool)
        with patch.object(core, 'remember') as write:
            with self.assertRaises(SystemExit) as caught:
                self.run('task', 'tool')
        self.assertEqual(caught.exception.code, 7)
        write.assert_not_called()

    def test_predicate_nested_mutation_rejects_without_body(self):
        original = [[{'data': ['safe']}]]
        calls = []
        def predicate(value):
            value[0][0]['data'].append('changed')
            return True
        def tool(value: Annotated[list, predicate]):
            calls.append(value)
        self.register('tool', tool)
        answer = json.loads(self.run('task', 'tool', original))
        self.assertEqual(answer['outcome'], 'rejected')
        self.assertFalse(answer['body_started'])
        self.assertEqual(original, [[{'data': ['safe']}]])
        self.assertEqual(calls, [])

    def test_retained_predicate_reference_cannot_change_body_data(self):
        retained = []
        def first(value):
            retained.append(value)
            return True
        def second(value):
            retained[0][0].append('late')
            return True
        def tool(a: Annotated[list, first], b: Annotated[int, second]):
            return a
        self.register('tool', tool)
        self.assertEqual(json.loads(self.run('task', 'tool', [['safe']], 1)), {'result': [['safe']]})

    def test_deepcopy_hooks_are_not_called(self):
        calls = []
        class Hostile:
            def __deepcopy__(self, memo):
                calls.append(True)
                return 'safe'
        def tool(value: Annotated[list, lambda value: True]):
            self.fail('body called')
        self.register('tool', tool)
        answer = json.loads(self.run('task', 'tool', [Hostile()]))
        self.assertEqual(answer['outcome'], 'rejected')
        self.assertEqual(calls, [])

    def test_effect_then_error_is_unknown_even_if_log_fails(self):
        effect = self.root / 'effect'
        def tool():
            effect.write_text('happened')
            raise ValueError('after effect')
        self.register('tool', tool)
        for log_fails in (False, True):
            with self.subTest(log_fails=log_fails):
                manager = patch.object(core, 'remember', side_effect=OSError()) if log_fails else contextlib.nullcontext()
                with manager:
                    answer = json.loads(self.run('task', 'tool'))
                self.assertEqual(effect.read_text(), 'happened')
                self.assertEqual(answer['outcome'], 'unknown')
                self.assertTrue(answer['body_started'])
                self.assertEqual(answer['log_status'], 'unavailable' if log_fails else 'written')
        self.assertEqual(json.loads(self.memory.read_text())['event'], 'execution_unknown')

    def test_before_bounds_actual_read_bytes(self):
        record = b'{"event":"refusal","task":"match"}\n'
        self.memory.write_bytes(record * (core.HISTORY_BYTES // len(record) * 4))
        actual_open = open
        reads = []
        class Reader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def fileno(self):
                return self.stream.fileno()
            def seek(self, offset):
                return self.stream.seek(offset)
            def read(self, size=-1):
                data = self.stream.read(size)
                reads.append((size, len(data)))
                return data
        def opened(path, mode='r', **kwargs):
            stream = actual_open(path, mode, **kwargs)
            if mode == 'rb':
                self.assertEqual(kwargs['buffering'], 0)
                return Reader(stream)
            return stream
        with patch.object(core, 'open', side_effect=opened, create=True):
            with contextlib.redirect_stdout(io.StringIO()):
                hits = core.before('match', self.memory)
        self.assertEqual(len(hits), core.SHOW_LIMIT)
        self.assertEqual(len(reads), 1)
        self.assertGreater(reads[0][0], 0)
        self.assertLessEqual(sum(n for _, n in reads), core.HISTORY_BYTES)

    def test_tail_boundary_and_corruption_scope(self):
        good = b'{"event":"refusal","task":"match"}\n'
        for prefix in (b'x' * (core.HISTORY_BYTES * 2), b'\xff' * (core.HISTORY_BYTES * 2)):
            self.memory.write_bytes(prefix + b'\n' + good)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(len(core.before('match', self.memory)), 1)
        for bad in (b'not-json\n', b'{broken\n', b'{}', b'x' * (core.HISTORY_BYTES + 1)):
            self.memory.write_bytes(bad)
            with self.assertRaises(core.FailClosedError):
                core.before('match', self.memory)

    def test_memory_outside_data_root(self):
        for path in (self.root / 'memory.jsonl', self.root / 'nested' / 'memory.jsonl'):
            with self.assertRaises(core.FailClosedError):
                instruments.build(self.root, path)

    def test_task_truncation(self):
        task = 'А' * 1_000_000
        with contextlib.redirect_stdout(io.StringIO()):
            self.run(task, 'missing')
            self.run(task, 'missing')
        rows = [json.loads(line) for line in self.memory.read_text().splitlines()]
        self.assertTrue(all(len(row['task']) == 1024 for row in rows))
        self.assertLess(self.memory.stat().st_size, 65536)

    def test_atomic_write_failure_and_publication(self):
        run = instruments.build(self.root, self.memory)
        target = self.root / 'target.txt'
        old, new = 'old\n' * 200, 'new\n' * 300
        target.write_text(old)
        actual_replace, actual_fdopen = os.replace, os.fdopen
        class PartialWriter:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def write(self, text):
                self.stream.write(text[:5])
                self.stream.flush()
                raise OSError('partial write')
        def partial(fd, *args, **kwargs):
            return PartialWriter(actual_fdopen(fd, *args, **kwargs))
        for stage in ('partial', 'fsync', 'replace'):
            with self.subTest(stage=stage):
                manager = (patch.object(instruments.os, 'fdopen', side_effect=partial) if stage == 'partial'
                           else patch.object(instruments.os, stage, side_effect=OSError(stage)))
                with manager:
                    answer = json.loads(run('task', 'write_file', path='target.txt', text=new))
                self.assertEqual(answer['outcome'], 'unknown')
                self.assertEqual(target.read_text(), old)
                self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['target.txt'])
        witnessed = []
        def publish(source, dest):
            witnessed.append((target.read_text(), Path(source).read_text()))
            actual_replace(source, dest)
            witnessed.append(target.read_text())
        with patch.object(instruments.os, 'replace', side_effect=publish):
            self.assertEqual(json.loads(run('task', 'write_file', path='target.txt', text=new)), {'result': len(new)})
        self.assertEqual(witnessed, [(old, new), new])
        self.assertEqual(target.read_text(), new)
