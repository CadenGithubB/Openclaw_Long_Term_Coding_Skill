#!/usr/bin/env python3
"""Cooperative per-attempt guard for commands in an already-authorized Linux worker.

This is not a sandbox, host executor, authentication mechanism or native supervisor.
An abandoned pending operation is never replayed. The parent must confirm inactivity.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

import progress_watch

MAX_STATE = 2 * 1024 * 1024
MAX_OUTPUT = 128 * 1024
MAX_WATCH_BYTES = 256 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def fail(message):
    raise ValueError(message)


def plain_path(path):
    path = Path(os.path.abspath(path))
    for part in [path] + list(path.parents):
        if part.is_symlink():
            fail('symlink path refused')
    return path


def project_path(path):
    path = plain_path(path)
    if not path.is_dir():
        fail('project directory missing')
    return path


def snapshot(project, names):
    """Hash exact, bounded named artifacts, including explicit missing-file markers."""
    records = []
    total = 0
    for name in sorted(names):
        if not name or Path(name).is_absolute() or '..' in Path(name).parts:
            fail('watch paths must stay inside the project')
        path = plain_path(project / name)
        try:
            before = path.stat()
        except FileNotFoundError:
            records.append({'path': name, 'missing': True})
            continue
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            fail('watch target must be a regular single-link file')
        total += before.st_size
        if total > MAX_WATCH_BYTES:
            fail('watched artifacts exceed byte limit')
        h = hashlib.sha256()
        consumed = 0
        with path.open('rb') as stream:
            while True:
                chunk = stream.read(min(65536, before.st_size - consumed + 1))
                if not chunk:
                    break
                consumed += len(chunk)
                if consumed > before.st_size:
                    fail('artifact grew while hashing')
                h.update(chunk)
        if consumed != before.st_size:
            fail('artifact shrank while hashing')
        after = path.stat()
        keys = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        if any(getattr(before, key) != getattr(after, key) for key in keys):
            fail('artifact changed while hashing')
        records.append({'path': name, 'bytes': before.st_size, 'sha256': h.hexdigest()})
    return {'sha256': digest(encode(records)), 'files': records}


def save(root, state):
    raw = encode(state)
    if len(raw) > MAX_STATE:
        fail('attempt journal exceeds limit')
    fd, name = tempfile.mkstemp(prefix='.pending-', dir=str(root))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, str(root / 'state.json'))
        dfd = os.open(str(root), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load(root):
    path = root / 'state.json'
    meta = path.lstat()
    if (not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1 or
            meta.st_uid != os.getuid() or meta.st_mode & 0o077 or meta.st_size > MAX_STATE):
        fail('invalid attempt journal')
    state = json.loads(path.read_text())
    if state.get('format') != 1 or state.get('kind') != 'cooperative-worker-attempt':
        fail('invalid attempt identity')
    return state


def verdict(state):
    return progress_watch.evaluate({'format': 1, 'limits': state['limits'], 'events': state['events']})


def run_command(argv, cwd, timeout):
    """Bounded merged output and process-group deadline. Executes only inside Linux."""
    if sys.platform != 'linux':
        fail('command execution requires an already-authorized Linux worker')
    if (not argv or len(argv) > 64 or any(not isinstance(a, str) or not a or
            '\0' in a or len(a) > 16384 for a in argv)):
        fail('invalid command argument array')
    if type(timeout) is not int or not 1 <= timeout <= 600:
        fail('timeout must be 1..600 seconds')
    start = time.monotonic()
    try:
        process = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    except OSError as exc:
        raw = type(exc).__name__.encode()
        return {'argv': argv, 'exitCode': None, 'reason': 'launch-error',
                'output': raw.decode(), 'outputBytes': raw, 'outputSha256': digest(raw),
                'processStarted': False, 'commandProcessJoined': True, 'workerInactive': 'unknown'}
    output = bytearray()
    reason = 'exit'
    reader = selectors.DefaultSelector()
    reader.register(process.stdout, selectors.EVENT_READ)
    try:
        while reader.get_map():
            left = timeout - (time.monotonic() - start)
            if left <= 0:
                reason = 'timeout'
                break
            for key, _ in reader.select(min(left, 0.25)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    reader.unregister(key.fileobj)
                    continue
                room = MAX_OUTPUT - len(output)
                output.extend(chunk[:room])
                if len(chunk) > room:
                    reason = 'output-limit'
                    break
            if reason != 'exit':
                break
        if reason != 'exit':
            os.killpg(process.pid, signal.SIGKILL)
        remaining = max(0.01, timeout - (time.monotonic() - start))
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            reason = 'timeout'
            os.killpg(process.pid, signal.SIGKILL)
            code = process.wait(timeout=5)
    finally:
        reader.close()
        process.stdout.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    return {'argv': argv, 'exitCode': code, 'reason': reason,
            'elapsedMs': int((time.monotonic() - start) * 1000),
            'outputSha256': digest(bytes(output)),
            'outputBytes': bytes(output),
            'output': bytes(output).decode('utf8', errors='replace'),
            'processStarted': True, 'commandProcessJoined': True, 'workerInactive': 'unknown'}


def initialize(root, project, watched, limits):
    if not watched or len(watched) > 64 or len(watched) != len(set(watched)):
        fail('provide 1..64 distinct watch paths')
    state = {'format': 1, 'kind': 'cooperative-worker-attempt',
             'project': str(project_path(project)), 'watch': watched,
             'limits': limits, 'events': [], 'receipts': []}
    if verdict(state)['decision'] == 'invalid':
        fail('invalid attempt limits')
    snapshot(Path(state['project']), watched)
    root = plain_path(root)
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    save(root, state)
    return state


def execute(root, state, phase, key, kind, check, argv, timeout):
    previous = verdict(state)
    if previous['decision'] != 'continue':
        return {'executed': False, 'watch': previous}
    if sys.platform != 'linux':
        fail('command execution requires an already-authorized Linux worker')
    if not argv or type(timeout) is not int or not 1 <= timeout <= 600:
        fail('command and bounded timeout required')
    project = project_path(state['project'])
    before = snapshot(project, state['watch'])
    event = {'actionId': str(len(state['events']) + 1), 'phaseId': phase,
             'actionKey': key, 'checkId': check, 'kind': kind, 'state': 'pending',
             'beforeSha256': before['sha256'], 'afterSha256': None,
             'resultFingerprint': None, 'verifiedMilestoneIds': []}
    state['events'].append(event)
    pending = verdict(state)
    if pending['decision'] == 'invalid':
        state['events'].pop()
        fail('invalid action identity')
    save(root, state)  # Reserve before launch. Crash leaves uncertainty, never a replay.
    # Any post-launch exception leaves the durable reservation pending.
    receipt = run_command(argv, project, timeout)
    after = snapshot(project, state['watch'])
    if kind == 'check' and before['sha256'] != after['sha256']:
        receipt['reason'] = 'check-modified-artifacts'
    okay = receipt['reason'] == 'exit' and receipt['exitCode'] == 0
    event.update({'state': ('passed' if kind == 'check' else 'completed') if okay else 'failed',
                  'afterSha256': after['sha256'],
                  'resultFingerprint': digest(encode({k: receipt[k] for k in ('exitCode', 'reason', 'outputSha256')}))})
    # A zero exit is a command result, not authority to assert a user milestone.
    receipt.update({'actionId': event['actionId'], 'phaseId': phase, 'actionKey': key,
                    'kind': kind, 'checkId': check, 'completedAtMs': time.time_ns() // 1000000,
                    'before': before, 'after': after, 'evidenceLevel': 'command-result'})
    # Retain bounded output separately; the event journal stays small.
    output = receipt.pop('output')
    raw = receipt.pop('outputBytes', output.encode('utf8'))
    if digest(raw) != receipt['outputSha256']:
        fail('captured output digest mismatch')
    output_name = 'action-' + event['actionId'] + '.output'
    fd = os.open(str(root / output_name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    receipt['outputLog'] = output_name
    receipt['outputLogSha256'] = digest(raw)
    receipt['outputPreview'] = output[:2048]
    receipt['recordSha256'] = digest(encode(receipt))
    state['receipts'].append(receipt)
    save(root, state)
    return {'executed': True, 'receipt': receipt, 'watch': verdict(state)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    init = sub.add_parser('init')
    init.add_argument('--state', required=True)
    init.add_argument('--project', required=True)
    init.add_argument('--watch', action='append', required=True)
    init.add_argument('--max-actions', type=int, default=24)
    init.add_argument('--repetitions', type=int, default=3)
    status = sub.add_parser('status')
    status.add_argument('--state', required=True)
    run = sub.add_parser('run')
    run.add_argument('--state', required=True)
    run.add_argument('--phase', required=True)
    run.add_argument('--key', required=True)
    run.add_argument('--kind', choices=['edit', 'check', 'inspect', 'execute'], required=True)
    run.add_argument('--check')
    run.add_argument('--timeout', type=int, default=60)
    run.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.mode == 'init':
            state = initialize(args.state, args.project, args.watch,
                               {'maxActions': args.max_actions, 'repetitionLimit': args.repetitions})
            result = {'initialized': True, 'watch': verdict(state)}
        else:
            root = plain_path(args.state)
            meta = root.stat()
            if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
                fail('attempt directory must be private and owned')
            fd = os.open(str(root / 'attempt.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                state = load(root)
                if args.mode == 'status':
                    result = {'watch': verdict(state), 'receiptCount': len(state['receipts'])}
                else:
                    argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
                    result = execute(root, state, args.phase, args.key, args.kind, args.check, argv, args.timeout)
            finally:
                os.close(fd)
        print(json.dumps(result, sort_keys=True))
        if result.get('executed') and (result['receipt']['exitCode'] != 0 or result['receipt']['reason'] != 'exit'):
            return 1
        return 0 if result['watch']['decision'] in ('continue', 'wait') else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc), 'executed': False, 'decision': 'review'}))
        return 2


if __name__ == '__main__':
    sys.exit(main())
