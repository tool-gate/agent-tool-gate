import contextlib
import io
import json
import multiprocessing as mp
import os
import tempfile
import unittest
from pathlib import Path
from typing import Annotated
from unittest.mock import patch
import core


def writer(path, worker):
    for number in range(100):
        core.remember({'worker': worker, 'number': number}, path)


def reader(path, queue):
    with open(path, 'a+') as stream:
        try:
            core.fcntl.flock(stream, core.fcntl.LOCK_EX | core.fcntl.LOCK_NB)
            locked = False
        except BlockingIOError:
            locked = True
        stream.seek(0)
        queue.put((locked, os.fstat(stream.fileno()).st_size, stream.read()))


def control_writer(path, worker, append, barrier):
    fd = os.open(path, os.O_WRONLY | (os.O_APPEND if append else 0))
    for number in range(30):
        os.lseek(fd, 0, os.SEEK_END)
        barrier.wait(timeout=10)
        os.write(fd, (json.dumps([worker, number]) + '\n').encode())
        barrier.wait(timeout=10)
    os.fsync(fd)
    os.close(fd)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'memory.jsonl'

    def test_disk_and_lock_until_fsync(self):
        original_sync = core.os.fsync
        events = []
        ctx = mp.get_context('spawn')
        def observe(fd):
            queue = ctx.Queue()
            process = ctx.Process(target=reader, args=(self.path, queue))
            process.start()
            locked, size, content = queue.get(timeout=15)
            process.join(15)
            self.assertEqual(process.exitcode, 0)
            self.assertTrue(locked)
            self.assertGreater(size, 0)
            self.assertEqual(json.loads(content), {'event': 'probe'})
            original_sync(fd)
            events.append('synced')
        original_lock = core.fcntl.flock
        def lock(fd, operation):
            if operation == core.fcntl.LOCK_UN:
                self.assertEqual(events, ['synced'])
            return original_lock(fd, operation)
        with patch.object(core.os, 'fsync', observe), patch.object(core.fcntl, 'flock', lock):
            core.remember({'event': 'probe'}, self.path)
        self.assertEqual(events, ['synced'])

    def test_two_processes_and_controls(self):
        ctx = mp.get_context('spawn')
        for mode in ('locked', 'append', 'overwrite'):
            self.path.write_text('')
            barrier = ctx.Barrier(2)
            processes = [ctx.Process(target=writer, args=(self.path, i)) if mode == 'locked'
                         else ctx.Process(target=control_writer,
                              args=(self.path, i, mode == 'append', barrier)) for i in range(2)]
            for process in processes:
                process.start()
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
            queue = ctx.Queue()
            witness = ctx.Process(target=reader, args=(self.path, queue))
            witness.start()
            locked, size, content = queue.get(timeout=15)
            witness.join(15)
            self.assertEqual(witness.exitcode, 0)
            rows = [json.loads(line) for line in content.splitlines()]
            if mode == 'locked':
                self.assertEqual({(r['worker'], r['number']) for r in rows},
                                 {(i, j) for i in range(2) for j in range(100)})
                self.assertEqual(len(rows), 200)
            elif mode == 'append':
                self.assertEqual(len(rows), 60)
            else:
                self.assertLess(len(rows), 60)
