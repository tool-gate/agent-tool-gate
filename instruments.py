import os
import tempfile
from pathlib import Path
import re
import shutil
import subprocess
from typing import Annotated
from core import FailClosedError, registry


def build(root, memory):
    root = Path(root).resolve(strict=True)
    memory = Path(memory).resolve()
    if memory.is_relative_to(root):
        raise FailClosedError('memory is inside root')
    executable = shutil.which('wc')
    if executable is None:
        raise FailClosedError('required program unavailable')
    programs = {'wc': str(Path(executable).resolve(strict=True))}
    register, run = registry(memory)
    filename = lambda value: re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value) is not None
    arguments = lambda values: (len(values) == 2 and all(type(v) is str for v in values)
                               and values[0] in ('-l', '-w', '-c') and filename(values[1]))
    File = Annotated[str, filename]
    Text = Annotated[str, lambda value: len(value) <= 65536]
    Program = Annotated[str, lambda value: value in programs]
    Arguments = Annotated[list, arguments]

    def read_file(path: File):
        try:
            fd = os.open(root / path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, encoding='utf-8') as stream:
                return stream.read(65536)
        except (OSError, ValueError) as error:
            raise FailClosedError(error) from error

    def write_file(path: File, text: Text):
        tmp_path = None
        try:
            target = root / path
            if target.is_symlink():
                raise FailClosedError('symlink target')
            fd, tmp_path = tempfile.mkstemp(dir=root, text=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                written = stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, target)
            return written
        except (OSError, ValueError) as error:
            raise FailClosedError(error) from error
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass

    def run_program(program: Program, args: Arguments):
        try:
            fd = os.open(root / args[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                result = subprocess.run([programs[program], args[0]], stdin=stream,
                                        shell=False, capture_output=True, text=True, timeout=5, check=True)
            return result.stdout
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise FailClosedError(error) from error

    register('read_file', read_file)
    register('write_file', write_file)
    register('run_program', run_program)
    return run
