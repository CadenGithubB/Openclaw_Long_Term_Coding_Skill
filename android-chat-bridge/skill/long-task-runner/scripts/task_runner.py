#!/usr/bin/env python3
"""Resume a bounded, operator-pinned single-artifact workflow in confined workers.

This runner neither creates workers nor acquires native OpenClaw session authority.
An interrupted, uncommitted operation is retained and blocked, never replayed.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import time

# -I excludes the script directory. Load only the reviewed installed siblings.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from task_store import StoreError, initialize, open_job, request_cancel
from task_docker import TaskDocker

MAX_ARTIFACT = 8192
TERMINAL = {'complete', 'cancelled', 'blocked', 'exhausted'}


class RunnerError(RuntimeError):
    pass


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def emit(key, value):
    print(json.dumps({key: value}, separators=(',', ':'), ensure_ascii=True), flush=True)


def artifact(snapshot):
    raw = base64.b64decode(snapshot['artifactBase64'], validate=True)
    if len(raw) > MAX_ARTIFACT or sha(raw) != snapshot['artifactSha256']:
        raise RunnerError('checkpoint-artifact-unconfirmed')
    return raw


def receipt(result, plan, snapshot, kind, index):
    """Accept adapter evidence only for this reservation and exact input/output."""
    if type(result) is not dict:
        raise RunnerError('worker-result-unconfirmed')
    raw, stdout = result.get('artifactBytes'), result.get('stdoutBytes')
    if (type(raw) is not bytes or len(raw) > MAX_ARTIFACT or
            type(stdout) is not bytes or len(stdout) > MAX_ARTIFACT or
            type(result.get('code')) is not int or result['code'] != 0 or
            result.get('workerId') != plan['workers'][index]['id'] or
            result.get('inputSha256') != snapshot['artifactSha256'] or
            result.get('artifactSha256') != sha(raw) or
            result.get('kind') != kind or type(result.get('index')) is not int or
            result['index'] != index or result.get('stopped') is not True):
        raise RunnerError('worker-result-unconfirmed')
    if kind == 'verify' and (raw != artifact(snapshot) or
                            sha(stdout) != plan['verifier']['expectedStdoutSha256']):
        raise RunnerError('verification-failed')
    return {'workerId': result['workerId'], 'kind': kind, 'index': index,
            'inputSha256': result['inputSha256'], 'artifactSha256': sha(raw),
            'stdoutSha256': sha(stdout), 'code': 0, 'stopped': True}


def run_workflow(handle, adapter, crash_hook=None, now_ms=None):
    """One lifetime host lock covers reservations, all worker IO and commits."""
    plan = json.loads(handle.plan_text)
    clock = now_ms or (lambda: int(time.time() * 1000))
    snapshot = handle.snapshot()

    def terminal(status, reason):
        current = handle.snapshot()
        handle.mark_terminal(current['token'], status, reason)
        return handle.snapshot()

    def clean(index):
        # The adapter raises unless a fresh inspection confirms this exact worker
        # inactive. A stop request or CLI acknowledgement is not proof.
        proof = adapter.cleanup(index)
        if (type(proof) is not dict or proof.get('stopped') is not True or
                proof.get('workerId') != plan['workers'][index]['id'] or
                proof.get('clientJoined') is not True):
            raise RunnerError('worker-cleanup-unconfirmed')
        return proof

    if snapshot['status'] in TERMINAL:
        return snapshot
    pending = snapshot['pending']
    if pending is not None:
        try:
            clean(pending['index'])
        except Exception:
            return terminal('blocked', 'interrupted-operation-cleanup-unconfirmed')
        if handle.cancel_requested():
            return terminal('cancelled', 'cancel-request-observed-after-interrupted-operation')
        return terminal('blocked', 'interrupted-operation-retained-no-replay')
    if handle.cancel_requested():
        return terminal('cancelled', 'cancel-request-observed-before-work')

    pool_validated = False
    while True:
        snapshot = handle.snapshot()
        if handle.cancel_requested():
            return terminal('cancelled', 'cancel-request-observed-before-work')
        if clock() >= plan['limits']['expiresAtMs']:
            return terminal('exhausted', 'task-deadline-reached')
        index = snapshot['completedSteps']
        kind = 'step' if index < len(plan['steps']) else 'verify'
        counter = 'workInvocations' if kind == 'step' else 'verificationInvocations'
        budget = 'maxWorkInvocations' if kind == 'step' else 'maxVerificationInvocations'
        if snapshot[counter] >= plan['limits'][budget]:
            return terminal('exhausted', 'task-invocation-budget-reached')
        if not pool_validated:
            try:
                adapter.validate_pool(snapshot['completedSteps'], None, cancelled=False)
            except Exception:
                return terminal('blocked', 'worker-pool-unconfirmed')
            pool_validated = True
        handle.reserve(snapshot['token'], kind, index)
        snapshot = handle.snapshot()
        operation = snapshot['pending']['id']
        if crash_hook:
            crash_hook('reserved', snapshot)
        # A cancel published while pool inspection/reservation ran is observed
        # here before execution. Reservation remains spent and retained.
        if handle.cancel_requested():
            return terminal('cancelled', 'cancel-request-observed-after-reservation')
        args = plan['steps'][index]['argv'] if kind == 'step' else plan['verifier']['argv']
        try:
            result = adapter.execute(index, args, artifact(snapshot), kind, handle.cancel_requested)
            verified = receipt(result, plan, snapshot, kind, index)
        except Exception:
            try:
                clean(index)
            except Exception:
                return terminal('blocked', 'worker-operation-and-cleanup-unconfirmed')
            if handle.cancel_requested():
                return terminal('cancelled', 'cancel-request-observed-after-worker-stop')
            return terminal('blocked', 'worker-operation-unconfirmed-no-replay')
        if handle.cancel_requested():
            return terminal('cancelled', 'cancel-request-observed-after-worker-stop')
        if clock() >= plan['limits']['expiresAtMs']:
            return terminal('exhausted', 'task-deadline-reached-after-worker-stop')
        if kind == 'verify':
            handle.complete(snapshot['token'], operation, verified)
            return handle.snapshot()
        handle.checkpoint(snapshot['token'], operation, result['artifactBytes'], verified)
        if crash_hook:
            crash_hook('checkpoint', handle.snapshot())


def read_plan(path, expected):
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise RunnerError('absolute-plan-path-required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before, named = os.fstat(fd), os.lstat(path)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or
                before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600 or
                not 0 < before.st_size <= 32768 or
                (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)):
            raise RunnerError('private-plan-file-required')
        raw = os.read(fd, 32769)
        after = os.fstat(fd)
        if ((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) !=
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns) or
                len(raw) != before.st_size or sha(raw) != expected):
            raise RunnerError('immutable-plan-hash-mismatch')
        return raw.decode('ascii')
    finally:
        os.close(fd)


class LazyDocker:
    """A terminal or cancelled safe-point job needs no live Docker connection."""
    def __init__(self, plan, parent):
        self.plan, self.parent, self.instance = plan, parent, None
        self.closed = False
    def get(self):
        if self.instance is None:
            self.instance = TaskDocker(self.plan['runtime'], self.plan['workers'], config_parent=self.parent)
        return self.instance
    def validate_pool(self, *args, **kwargs):
        return self.get().validate_pool(*args, **kwargs)
    def execute(self, *args, **kwargs):
        return self.get().execute(*args, **kwargs)
    def cleanup(self, *args, **kwargs):
        return self.get().cleanup(*args, **kwargs)
    def status(self):
        if self.instance is not None:
            return self.instance.status()
        return {'getRequests': 0, 'startRequests': 0, 'execRequests': 0, 'stopRequests': 0,
                'clientJoined': True, 'closed': self.closed}
    def close(self):
        self.closed = True
        if self.instance is not None:
            self.instance.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('init', 'run', 'resume', 'status', 'cancel'))
    parser.add_argument('--job', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--plan')
    parser.add_argument('--crash-after', choices=('checkpoint', 'reserved'),
                        help='Explicit fault-injection hook for disposable acceptance tests only')
    args = parser.parse_args(argv)
    if (args.command == 'init') != bool(args.plan):
        parser.error('--plan is required only for init')
    if args.crash_after and args.command not in ('run', 'resume'):
        parser.error('--crash-after requires run or resume')
    try:
        if args.command == 'init':
            text = read_plan(args.plan, args.sha256)
            parent = os.path.dirname(args.job)
            if (not os.path.isabs(args.job) or os.path.normpath(args.job) != args.job or
                    os.path.realpath(parent) != parent):
                raise RunnerError('absolute-private-job-parent-required')
            meta = os.stat(parent, follow_symlinks=False)
            if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o700:
                raise RunnerError('private-job-parent-required')
            os.mkdir(args.job, 0o700)
            result = initialize(args.job, text)
        elif args.command == 'cancel':
            result = request_cancel(args.job, args.sha256)
        else:
            with open_job(args.job, args.sha256) as handle:
                if args.command == 'status':
                    result = handle.snapshot()
                else:
                    plan = json.loads(handle.plan_text)
                    adapter = LazyDocker(plan, os.path.dirname(args.job))
                    def crash(point, snapshot):
                        if point == args.crash_after:
                            emit('taskRunnerCrashed', {'point': point, 'snapshot': snapshot, 'adapter': adapter.status()})
                            os.kill(os.getpid(), signal.SIGKILL)
                    try:
                        result = run_workflow(handle, adapter, crash)
                    finally:
                        adapter.close()
                        emit('taskRunnerAdapter', adapter.status())
        emit('taskRunnerResult', result)
        return 2 if result.get('status') in ('cancelled', 'blocked', 'exhausted') else 0
    except Exception as error:
        emit('taskRunnerError', {'type': type(error).__name__, 'message': str(error)[:256]})
        return 3


if __name__ == '__main__':
    sys.exit(main())
