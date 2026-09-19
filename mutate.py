import os
import sys
import json
import tempfile
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

MUTATIONS = {'gate_always_allows': ('core.py', 'bound = gate(signature, args, kwargs)', 'bound = signature.bind(*args, **kwargs)'), 'types_disabled': ('core.py', 'type(value) is not expected:', 'False:'), 'constraints_disabled': ('core.py', 'if not get_args(parameter.annotation)[1](probe):', 'if False:'), 'silent_none': ('core.py', 'bound = gate(signature, args, kwargs)', 'try:\n                bound = gate(signature, args, kwargs)\n            except FailClosedError:\n                return None'), 'lock_removed': ('core.py', 'fcntl.flock(stream, fcntl.LOCK_EX)', 'pass'), 'raw_function': ('core.py', 'tools[name] = sealed', 'tools[name] = lambda state, *a, **kw: function(*a, **kw)'), 'defaults_skipped': ('core.py', 'bound.apply_defaults()', 'pass'), 'hook_skipped': ('core.py', 'before(task_str, path)', 'pass'), 'original_arguments': ('core.py', 'return function(*bound.args, **bound.kwargs)', 'return function(*args, **kwargs)'), 'fsync_removed': ('core.py', 'os.fsync(stream.fileno())', 'pass'), 'flush_removed': ('core.py', 'stream.flush()', 'pass'), 'paths_unrestricted': ('instruments.py', "re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value) is not None", 'True'), 'programs_unrestricted': ('instruments.py', 'lambda value: value in programs', 'lambda value: True'), 'options_unrestricted': ('instruments.py', "values[0] in ('-l', '-w', '-c')", 'True'), 'shell_enabled': ('instruments.py', 'shell=False', 'shell=True'), 'timeout_removed': ('instruments.py', 'timeout=5', 'timeout=None'), 'exit_check_removed': ('instruments.py', 'check=True', 'check=False'), 'read_lock_removed': ('core.py', 'fcntl.flock(stream, fcntl.LOCK_SH)', 'pass'), 'public_file_mode': ('instruments.py', '0o600', '0o666'), 'varargs_allowed': ('core.py', '(p.VAR_POSITIONAL, p.VAR_KEYWORD)', '(p.VAR_KEYWORD,)'), 'text_limit_removed': ('instruments.py', 'len(value) <= 65536', 'True'), 'read_limit_removed': ('instruments.py', 'stream.read(65536)', 'stream.read()'), 'root_not_strict': ('instruments.py', 'Path(root).resolve(strict=True)', 'Path(root).resolve()'), 'executable_not_strict': ('instruments.py', 'Path(executable).resolve(strict=True)', 'Path(executable).resolve()'), 'boundary_narrowed': ('core.py', 'except BaseException as error:', 'except FailClosedError as error:'), 'base_exceptions_escape': ('core.py', 'except BaseException as error:', 'except Exception as error:'), 'context_unchecked': ('core.py', 'if type(task) is not str or type(name) is not str:', 'if False:'), 'keyword_collision': ('core.py', 'name=None, /, *args', 'name=None, *args'), 'nonfinite_json': ('core.py', "json.dumps({'result': result}, ensure_ascii=False, allow_nan=False)", "json.dumps({'result': result}, ensure_ascii=False)"), 'unbounded_history': ('core.py', '[:SHOW_LIMIT]', ''), 'shown_per_match': ('core.py', 'if hits:', 'for unused in hits:'), 'refusal_not_logged': ('core.py', "else 'refusal'", "else 'ignore'"), 'stop_exceptions_swallowed': ('core.py', 'except (KeyboardInterrupt, SystemExit, GeneratorExit):\n            # Never delay operator stop by attempting another journal write.\n            raise', 'except ():\n            raise'), 'memory_outside_root_ignored': ('instruments.py', 'if memory.is_relative_to(root):', 'if False:'), 'task_not_truncated': ('core.py', "                task_str = task[:1024] if type(task) is str else ''", "                task_str = task if type(task) is str else ''"), 'file_read_entirely': ('core.py', 'offset = max(0, size - HISTORY_BYTES)\n            stream.seek(offset)\n            data = stream.read(HISTORY_BYTES)', 'offset = 0\n            stream.seek(offset)\n            data = stream.read()'), 'deep_copy_removed': ('core.py', 'copy.deepcopy(v)', 'v'), 'shown_indices_kept': ('core.py', "'count': len(hits), 'worked': True}", "'count': len(hits), 'records': [i for i, r in hits], 'worked': True}"), 'atomic_write_removed': ('instruments.py', 'os.replace(tmp_path, target)', 'pass'), 'defaults_after_snapshot': ('core.py', "    bound.apply_defaults()\n    for name, value in bound.arguments.items():\n        parameter = signature.parameters[name]\n        expected = get_args(parameter.annotation)[0]\n        if not (value is None and parameter.default is None) and type(value) is not expected:\n            raise FailClosedError('argument type: ' + name)\n        plain(value)\n    bound.arguments = {n: copy.deepcopy(v) for n, v in bound.arguments.items()}", "    for name, value in bound.arguments.items():\n        parameter = signature.parameters[name]\n        expected = get_args(parameter.annotation)[0]\n        if not (value is None and parameter.default is None) and type(value) is not expected:\n            raise FailClosedError('argument type: ' + name)\n        plain(value)\n    bound.arguments = {n: copy.deepcopy(v) for n, v in bound.arguments.items()}\n    bound.apply_defaults()"), 'write_without_encoding': ('instruments.py', "with os.fdopen(fd, 'w', encoding='utf-8') as stream:", "with os.fdopen(fd, 'w') as stream:"), 'read_without_encoding': ('instruments.py', "with os.fdopen(fd, encoding='utf-8') as stream:", 'with os.fdopen(fd) as stream:'), 'annotated_three_args': ('core.py', 'len(get_args(p.annotation)) != 2', 'len(get_args(p.annotation)) < 2'), 'regex_anchor_removed': ('instruments.py', "re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value)", "re.match(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value)"), 'o_nonblock_removed': ('instruments.py', 'fd = os.open(root / path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)', 'fd = os.open(root / path, os.O_RDONLY | os.O_NOFOLLOW)'), 'broad_except_before': ('core.py', 'except (OSError, ValueError, TypeError, KeyError) as error:', 'except Exception as error:'), 'casefold_to_lower': ('core.py', 'text.casefold()', 'text.lower()'), 'shown_without_task': ('core.py', "'task': task_str, 'count': len(hits)", "'count': len(hits)"), 'allow_nan_in_remember': ('core.py', 'json.dumps(record, ensure_ascii=False, allow_nan=False)', 'json.dumps(record, ensure_ascii=False)')}

class MeasurementError(RuntimeError):
    pass


def runner():
    import contextlib
    import hashlib
    import io
    import sysconfig
    import unittest

    root = ROOT
    sys.path.insert(0, str(root))
    stdlib = Path(sysconfig.get_path('stdlib')).resolve()
    violations = []

    def allowed(filename):
        if not filename or filename.startswith('<'):
            return True
        path = Path(filename).resolve()
        return path.is_relative_to(root) or (path.is_relative_to(stdlib)
                and 'site-packages' not in path.parts and 'dist-packages' not in path.parts)

    def audit(event, args):
        if event == 'exec':
            filename = args[0].co_filename
            if not allowed(filename):
                violations.append(filename)
                raise MeasurementError('external executed module: ' + filename)

    sys.addaudithook(audit)

    def origins():
        for name, module in list(sys.modules.items()):
            filename = getattr(module, '__file__', None)
            if filename and not allowed(filename):
                violations.append(name + ':' + filename)
        if violations:
            raise MeasurementError('module provenance: ' + repr(violations))

    def collect(suite):
        found = []
        for test in suite:
            if isinstance(test, unittest.TestSuite):
                found.extend(collect(test))
            else:
                found.append(test.id())
        return found

    class Result(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.started_ids = []
        def startTest(self, test):
            self.started_ids.append(test.id())
            super().startTest(test)

    try:
        loader = unittest.TestLoader()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            suite = loader.discover(str(root / 'tests'), top_level_dir=str(root))
            identities = sorted(collect(suite))
            origins()
            if loader.errors or not identities or len(set(identities)) != len(identities):
                raise MeasurementError('empty, duplicate or failed test discovery')
            result = unittest.TextTestRunner(stream=output, resultclass=Result).run(suite)
            origins()
        if sorted(result.started_ids) != identities or result.testsRun != len(identities):
            raise MeasurementError('executed test identities differ')
        if result.skipped or result.expectedFailures or result.unexpectedSuccesses:
            raise MeasurementError('skip/expectedFailure/unexpectedSuccess forbidden')
        failed = sorted(test.id() for test, detail in result.failures + result.errors)
        print(json.dumps({'ids': identities, 'tests': result.testsRun, 'skip': 0,
                          'failures': len(failed), 'failed_ids': failed}, sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({'measurement_error': str(error)}, sort_keys=True))
        return 2


def command(tree):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('PYTHON')}
    result = subprocess.run([sys.executable, '-I', '-S', '-B', str(tree / 'mutate.py'), '--runner'],
                            cwd=tree, env=env, text=True, capture_output=True, timeout=120)
    try:
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as error:
        raise MeasurementError('invalid runner output: ' + result.stderr) from error
    if result.returncode != 0 or 'measurement_error' in data:
        raise MeasurementError('runner refused: ' + str(data))
    if not data.get('ids') or data.get('tests') != len(data['ids']) or data.get('skip') != 0:
        raise MeasurementError('invalid runner accounting')
    return data


def snapshot(root):
    files = [root / 'core.py', root / 'instruments.py', root / 'mutate.py',
             *sorted((root / 'tests').rglob('*.py'))]
    if not (root / 'tests' / '__init__.py').is_file():
        raise MeasurementError('missing tests/__init__.py')
    result = {}
    for file in files:
        if not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise MeasurementError('missing or nonlocal file: ' + str(file))
        result[str(file.relative_to(root))] = file.read_bytes()
    return result


def apply_mutation(sources, mutation):
    file, old, new = mutation
    if file not in sources or old == new or not old:
        raise MeasurementError('invalid mutation')
    source = sources[file].decode('utf-8')
    count = source.count(old)
    if count != 1:
        raise MeasurementError('expected exactly one match, got ' + str(count))
    changed = source.replace(old, new, 1)
    try:
        compile(changed, file, 'exec')
    except SyntaxError as error:
        raise MeasurementError('mutation is not valid Python') from error
    result = dict(sources)
    result[file] = changed.encode('utf-8')
    return result


def trial(sources):
    # Fresh complete package and fresh process for baseline and every mutation.
    with tempfile.TemporaryDirectory(prefix='yadro-mutation-') as temporary:
        tree = Path(temporary).resolve() / 'copy'
        if tree.is_relative_to(ROOT):
            raise MeasurementError('temporary tree is inside source')
        for file, content in sources.items():
            path = tree / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return command(tree)


def self_test():
    # Deliberate control uses existing types_disabled, not a new catalog entry.
    code = ('def accepts(value, expected):\n'
            '    if type(value) is not expected:\n'
            '        return False\n'
            '    return True\n')
    test = ('import unittest\nimport core\n'
            'class Control(unittest.TestCase):\n'
            '    def test_reject(self):\n'
            '        self.assertFalse(core.accepts(True, int))\n')
    base = {'core.py': code.encode(), 'instruments.py': b'',
            'mutate.py': Path(__file__).read_bytes(), 'tests/__init__.py': b'',
            'tests/test_control.py': test.encode()}
    green = trial(base)
    red = trial(apply_mutation(base, MUTATIONS['types_disabled']))
    if green['tests'] != 1 or green['failures'] or red['ids'] != green['ids'] or red['failures'] != 1:
        raise MeasurementError('control mutation not caught')
    print('control types_disabled: tests=1 baseline_failures=0 mutant_failures=1')
    cases = {'empty': dict(base), 'skip': dict(base), 'duplicate': dict(base),
             'late_external': dict(base), 'dirty_state': dict(base)}
    del cases['empty']['tests/test_control.py']
    cases['skip']['tests/test_control.py'] = test.replace('    def test_reject',
        "    @unittest.skip('control')\n    def test_reject").encode()
    cases['duplicate']['tests/test_control.py'] = (test +
        '\ndef load_tests(loader, suite, pattern):\n    return unittest.TestSuite([suite, loader.loadTestsFromTestCase(Control)])\n').encode()
    with tempfile.TemporaryDirectory(prefix='external-control-') as temporary:
        outside = Path(temporary) / 'foreign.py'
        outside.write_text('VALUE = 1\n')
        cases['late_external']['tests/test_control.py'] = (
            'import unittest, importlib.util\n'
            'class Control(unittest.TestCase):\n'
            '    def test_late(self):\n'
            f'        spec = importlib.util.spec_from_file_location("late_alias", {str(outside)!r})\n'
            '        spec.loader.exec_module(importlib.util.module_from_spec(spec))\n').encode()
        for name in ('empty', 'skip', 'duplicate', 'late_external'):
            try:
                trial(cases[name])
            except MeasurementError:
                print(name + ': refused=2')
            else:
                raise MeasurementError('control accepted: ' + name)
    cases['dirty_state']['tests/test_control.py'] = (
        'import unittest\nfrom pathlib import Path\n'
        'class Control(unittest.TestCase):\n'
        '    def test_clean(self):\n'
        '        p = Path("leftover")\n'
        '        self.assertFalse(p.exists())\n'
        '        p.write_text("leftover")\n').encode()
    for _ in range(2):
        if trial(cases['dirty_state'])['failures']:
            raise MeasurementError('state leaked between trials')
    print('fresh_trees: runs=2 failures=0')
    for name, text in (('missing', ''), ('multiple', code + code)):
        broken = dict(base, **{'core.py': text.encode()})
        try:
            apply_mutation(broken, MUTATIONS['types_disabled'])
        except MeasurementError:
            print(name + '_template: refused=2')
        else:
            raise MeasurementError('bad template accepted')
    print('self_test: OK')
    return 0


def main():
    try:
        if sys.argv[1:] == ['--self-test']:
            return self_test()
        if sys.argv[1:]:
            raise MeasurementError('usage: mutate.py [--self-test]')
        sources = snapshot(ROOT)
        baseline = trial(sources)
        print('baseline: tests={tests} skip={skip} failures={failures}'.format(**baseline), flush=True)
        if baseline['failures']:
            raise MeasurementError('baseline is red')
        # Freeze and validate all replacements; never mutate the catalog in a loop.
        prepared = [(name, apply_mutation(sources, mutation)) for name, mutation in MUTATIONS.items()]
        caught = 0
        for name, changed in prepared:
            result = trial(changed)
            if result['ids'] != baseline['ids']:
                raise MeasurementError('test identities changed: ' + name)
            caught += bool(result['failures'])
            print(name + ': failures=' + str(result['failures']), flush=True)
        print('caught=' + str(caught) + ' total=' + str(len(prepared)), flush=True)
        return 0 if caught == len(prepared) else 1
    except (OSError, MeasurementError, subprocess.TimeoutExpired) as error:
        print('MEASUREMENT REFUSED: ' + str(error), flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(runner() if sys.argv[1:] == ['--runner'] else main())
