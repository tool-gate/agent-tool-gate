import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import instruments


class InstrumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'files'
        self.root.mkdir()
        self.run = instruments.build(self.root, Path(self.temp.name) / 'memory.jsonl')

    def call(self, name, **arguments):
        with contextlib.redirect_stdout(io.StringIO()):
            return json.loads(self.run('task', name, **arguments))

    def test_read_write_and_program(self):
        self.assertEqual(self.call('write_file', path='data.txt', text='one\ntwo\n'), {'result': 8})
        self.assertEqual(self.call('read_file', path='data.txt'), {'result': 'one\ntwo\n'})
        actual = subprocess.run
        with patch.object(instruments.subprocess, 'run', wraps=actual) as launch:
            answer = self.call('run_program', program='wc', args=['-l', 'data.txt'])
        self.assertEqual(int(answer['result']), 2)
        positional, keywords = launch.call_args
        self.assertIs(type(positional[0]), list)
        self.assertTrue(Path(positional[0][0]).is_absolute())
        self.assertEqual(positional[0][1:], ['-l'])
        self.assertIs(keywords['shell'], False)

    def test_commands_cannot_select_execution(self):
        commands = ('rm -r -f /', 'rm --recursive --force /', 'find / -delete', 'cat x | /bin/sh')
        with patch.object(instruments.subprocess, 'run') as launch:
            for command in commands:
                self.assertIn('error', self.call('run_program', program=command, args=[]))
                self.assertIn('error', self.call('run_program', program='wc', args=command))
                self.assertIn('error', self.call('read_file', path=command))
                self.assertIn('error', self.call('write_file', path=command, text='data'))
            launch.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_command_text_is_only_file_data(self):
        text = 'rm -r -f /\nrm --recursive --force /\nfind / -delete\ncat x | /bin/sh'
        with patch.object(instruments.subprocess, 'run') as launch:
            self.assertEqual(self.call('write_file', path='data.txt', text=text), {'result': len(text)})
            self.assertEqual(self.call('read_file', path='data.txt'), {'result': text})
            launch.assert_not_called()

    def test_paths_and_symlinks(self):
        outside = self.root.parent / 'outside.txt'
        outside.write_text('keep')
        (self.root / 'link.txt').symlink_to(outside)
        for path in ('../outside.txt', str(outside), 'link.txt'):
            self.assertIn('error', self.call('read_file', path=path))
            self.assertIn('error', self.call('write_file', path=path, text='changed'))
            self.assertIn('error', self.call('run_program', program='wc', args=['-l', path]))
        self.assertEqual(outside.read_text(), 'keep')

    def test_program_allowlist(self):
        (self.root / 'data.txt').write_text('safe')
        with patch.object(instruments.subprocess, 'run') as launch:
            with patch.object(instruments.os, 'open') as opened:
                self.assertIn('error', self.call('run_program', program='sh', args=['-l', 'data.txt']))
                opened.assert_not_called()
            launch.assert_not_called()

    def test_argument_grammar(self):
        (self.root / 'data.txt').write_text('safe')
        with patch.object(instruments.subprocess, 'run') as launch:
            for args in (['-x', 'data.txt'], ['-l', '../outside'], ['-l', 'data.txt', 'extra'], [True, 'data.txt']):
                self.assertIn('error', self.call('run_program', program='wc', args=args))
            launch.assert_not_called()
