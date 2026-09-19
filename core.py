import copy
import fcntl
import inspect
import json
import math
import os
import re
from pathlib import Path
from typing import Annotated, get_args, get_origin

ROOT = Path(__file__).resolve().parent
SHOW_LIMIT = 3
HISTORY_BYTES = 65536


class FailClosedError(Exception):
    def __init__(self, message):
        super().__init__('NE_ZNAYU: ' + str(message))


def remember(record, path=ROOT / 'failures.jsonl'):
    try:
        line = json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n'
        with open(path, 'a', encoding='utf-8') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                stream.write(line)
                stream.flush()
                os.fsync(stream.fileno())
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
    except (OSError, TypeError, ValueError) as error:
        raise FailClosedError(error) from error


def before(task, path=ROOT / 'failures.jsonl'):
    try:
        if not Path(path).exists():
            return []
        # Unbuffered binary I/O: at most HISTORY_BYTES delivered by read.
        with open(path, 'rb', buffering=0) as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            size = os.fstat(stream.fileno()).st_size
            offset = max(0, size - HISTORY_BYTES)
            stream.seek(offset)
            data = stream.read(HISTORY_BYTES)
            if offset:
                boundary = data.find(b'\n')
                if boundary < 0:
                    raise FailClosedError('history record exceeds window')
                data = data[boundary + 1:]
            if data and not data.endswith(b'\n'):
                raise FailClosedError('incomplete history record')
            records = [json.loads(line) for line in data.decode('utf-8').splitlines()
                       if line.strip()]
        task_str = task[:1024] if type(task) is str else ''
        words = lambda text: set(re.findall(r'\w+', text.casefold()))
        ranked = sorted(((len(words(task_str) & words(r.get('task', ''))), i, r)
                         for i, r in enumerate(records) if r.get('event') == 'refusal'), reverse=True)
        hits = [(i, r) for score, i, r in ranked if score][:SHOW_LIMIT]
        for index, record in hits:
            print(json.dumps(record, ensure_ascii=False), flush=True)
        if hits:
            remember({'event': 'shown', 'task': task_str, 'count': len(hits), 'worked': True}, path)
        return [r for i, r in hits]
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise FailClosedError(error) from error


def plain(value):
    """Validate before deepcopy; do not invoke hooks on user-defined objects."""
    kind = type(value)
    if value is None or kind in (str, int, bool):
        return
    if kind is float and math.isfinite(value):
        return
    if kind is list:
        for item in value:
            plain(item)
        return
    if kind is dict and all(type(key) is str for key in value):
        for item in value.values():
            plain(item)
        return
    raise FailClosedError('non-plain argument')


def gate(signature, args, kwargs):
    try:
        bound = signature.bind(*args, **kwargs)
    except TypeError as error:
        raise FailClosedError(error) from error
    bound.apply_defaults()
    for name, value in bound.arguments.items():
        parameter = signature.parameters[name]
        expected = get_args(parameter.annotation)[0]
        if not (value is None and parameter.default is None) and type(value) is not expected:
            raise FailClosedError('argument type: ' + name)
        plain(value)
    bound.arguments = {n: copy.deepcopy(v) for n, v in bound.arguments.items()}
    for name, value in bound.arguments.items():
        parameter = signature.parameters[name]
        if value is None and parameter.default is None:
            continue
        probe = copy.deepcopy(value)
        if not get_args(parameter.annotation)[1](probe):
            raise FailClosedError('argument constraint: ' + name)
        if probe != value:
            raise FailClosedError('predicate changed argument: ' + name)
    return bound


def registry(path=ROOT / 'failures.jsonl'):
    tools = {}

    def register(name, function):
        try:
            signature = inspect.signature(function)
        except (TypeError, ValueError) as error:
            raise FailClosedError(error) from error
        if name in tools or any(get_origin(p.annotation) is not Annotated or
                len(get_args(p.annotation)) != 2 or not callable(get_args(p.annotation)[1]) or
                get_args(p.annotation)[0] not in (str, int, float, bool, list) or
                p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in signature.parameters.values()):
            raise FailClosedError('invalid tool schema')

        def sealed(state, /, *args, **kwargs):
            bound = gate(signature, args, kwargs)
            # Conservative marker immediately before invocation, not a durable intent.
            state['started'] = True
            return function(*bound.args, **bound.kwargs)

        tools[name] = sealed

    def run(task=None, name=None, /, *args, **kwargs):
        state = {'started': False}
        try:
            if type(task) is not str or type(name) is not str:
                raise FailClosedError('invalid context')
            task_str = task[:1024]
            before(task_str, path)
            if name not in tools:
                raise FailClosedError('unknown tool')
            result = tools[name](state, *args, **kwargs)
            return json.dumps({'result': result}, ensure_ascii=False, allow_nan=False)
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            # Never delay operator stop by attempting another journal write.
            raise
        except BaseException as error:
            message = 'NE_ZNAYU: ' + type(error).__name__
            outcome = 'unknown' if state['started'] else 'rejected'
            event = 'execution_unknown' if state['started'] else 'refusal'
            log_status = 'written'
            try:
                task_str = task[:1024] if type(task) is str else ''
                remember({'event': event, 'task': task_str, 'error': message}, path)
            except (KeyboardInterrupt, SystemExit, GeneratorExit):
                raise
            except BaseException:
                log_status = 'unavailable'
            return json.dumps({'error': message, 'outcome': outcome,
                               'body_started': state['started'], 'log_status': log_status},
                              ensure_ascii=False)

    return register, run
