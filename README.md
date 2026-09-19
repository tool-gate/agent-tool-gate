# agent-tool-gate

A small fail-closed gate between an LLM agent and the tools it is allowed to call.
Python standard library only, no dependencies, ~240 lines of production code.

## The idea

Most agent "safety" layers filter command strings: a blocklist of `rm -rf`,
`curl | sh`, and so on. A blocklist is a race you lose — `rm -r -f /`,
`rm --recursive --force /`, `find / -delete` all mean the same thing and none
of them look alike. This gate takes the other side of the trade: **a command
string in a tool argument is already a loss.** There is nothing here that
executes text.

What is left is four properties:

1. **One entrance.** `run(task, name, *args, **kwargs)` is the only public
   surface. Tools are registered, never handed out: nothing in the API returns
   a tool function. This is an API boundary inside one Python process, not a
   sandbox — reflection such as `__closure__` can reach anything (see "What is
   NOT in here").
2. **Allowlist with a narrow signature.** Every tool argument is
   `Annotated[type, predicate]`, the type is one of `str/int/float/bool/list`,
   and the predicate is checked on a deep copy — a predicate that mutates its
   argument is a refusal, not a side effect.
3. **A journal of refusals, under a lock, fsynced before the gate moves on.**
   Refusals and the history shown by the hook are appended under `flock` and
   `fsync`ed. If the history record cannot be written, the tool body does not
   start; if a refusal cannot be written, the JSON says
   `"log_status": "unavailable"`. Nothing is written on a clean success, so
   an unwritable journal shows up only at the first refusal.
4. **"Ask before you work" hook.** Before every call, `before()` looks at the
   tail of the refusal journal and prints the closest past refusals for the
   same task. The agent sees its own history before repeating it.

Everything the gate refuses comes back as JSON, never as an exception escaping
into the agent's loop:

```json
{"error": "NE_ZNAYU: FailClosedError", "outcome": "rejected", "body_started": false, "log_status": "written"}
```

`outcome` distinguishes *rejected* (the tool body never started — safe to
retry) from *unknown* (the body started and the result is not known — not safe
to retry). `NE_ZNAYU` is Russian for "I do not know": the gate says "unknown"
out loud instead of guessing. Operator stops — `KeyboardInterrupt`,
`SystemExit`, `GeneratorExit` — are re-raised untouched and never turned into
JSON; a human pressing Ctrl-C is not a tool error.

## Files

| file | what it is |
| --- | --- |
| `core.py` | the gate: registry, argument checking, refusal journal, hook |
| `instruments.py` | three example tools (`read_file`, `write_file`, `run_program`) built on the gate |
| `mutate.py` | the mutation probe — the only proof that the tests are real |
| `tests/` | 61 tests, standard library `unittest` |
| `evidence/` | logs of the runs recorded when this version was accepted |

## Run it

Python 3.10 or newer on a POSIX system (Linux, macOS): locking uses `fcntl`,
so Windows is not supported. No install step, no dependencies.

```
python3 -m unittest discover -s tests -t .
python3 mutate.py --self-test
python3 mutate.py
```

Expected: `Ran 61 tests ... OK`; `self_test: OK`; `caught=49 total=49`.

`mutate.py` breaks the code in 49 known ways, one at a time, in a fresh copy of
the tree in a fresh process, and requires the test suite to go red for every
one of them. A green test suite proves nothing on its own; a test suite that
stays green while `fsync` is removed or the allowlist is disabled is decoration.
`mutate.py --self-test` checks the probe itself — that it refuses an empty
suite, a skipped test, a duplicated test, a test importing code from outside
the tree, and that state does not leak between trials.

Exit codes are three-valued throughout: `0` good, `1` bad, `2` "the measurement
refused to give a number" — an unmeasured state is reported as unmeasured, not
as a pass.

## What is NOT in here

- **No sandbox.** The gate limits *which* tool runs with *which* arguments. It
  does not contain a process, a filesystem, or a network. Put it inside a real
  sandbox; do not use it as one.
- **No network tools, no shell tool.** The three example tools are
  deliberately dull. `run_program` runs exactly one allowlisted binary (`wc`)
  with one of three flags and a file as stdin — `shell=False`, no string is
  ever interpreted.
- **No intent journal and no crash recovery.** The gate records refusals and
  unknown outcomes, but it does not write an intent record before starting a
  tool body. If the process dies mid-call, a later reader cannot tell that the
  call happened. Repeating a call after `outcome: "unknown"` is therefore not
  safe automatically — a human decides.
- **No external deadline.** `run_program` has a 5-second timeout; the gate
  itself has no wall-clock budget for a whole session.
- **No prompt handling, no model client, no agent loop.** This is the tool
  boundary only.

Known limits are listed here on purpose. They were found by pointing an
independent agent at the code with instructions to break it, and they are open,
not fixed.

## License

Apache License 2.0 — see `LICENSE`.
