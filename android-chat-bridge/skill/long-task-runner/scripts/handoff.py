#!/usr/bin/env python3
"""Trusted native artifact -> one fixed Y job. No native dispatch or claim release.

The pinned host receipt is evidence supplied by the operator, never model output.
Its native lifecycle observation alone does not prove artifact provenance or stop.
Copied Y modules are unchanged. H uses their private-file primitives, a separate
journal, and a lifetime lock held across import and all Y work.
"""
import argparse
import base64
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_store as y
import task_runner as runner

MAX_MANIFEST = 49152
MAX_RECEIPT = 24576
MAX_EVENT = 32768
TERMINAL = {'blocked', 'cancelled', 'exhausted'}
need, exact, sha, compact = y.demand, y.exact, y.digest, y.compact


def root_pin(value):
    exact(value, ('path', 'dev', 'ino', 'uid', 'mode'))
    need(type(value['path']) is str and os.path.isabs(value['path'])
         and os.path.normpath(value['path']) == value['path'] and value['path'] != '/')
    need(all(y.integer(value[k]) for k in ('dev', 'ino', 'uid', 'mode'))
         and value['dev'] > 0 and value['ino'] > 0 and value['uid'] == os.getuid()
         and value['mode'] == 0o700)


def source_hashes(value):
    need(type(value) is dict and 1 <= len(value) <= 32)
    need(all(type(k) is str and 0 < len(k) <= 128 and k.isascii()
             and y.hex_value(v) for k, v in value.items()))


def manifest_value(text):
    value = y.canonical(text, MAX_MANIFEST)
    exact(value, ('format', 'handoffId', 'rootPin', 'native', 'sourceHashes', 'budget', 'target'))
    need(type(value['format']) is int and value['format'] == 1 and y.hex_value(value['handoffId'], 32))
    root_pin(value['rootPin'])
    native = value['native']
    exact(native, ('jobId', 'runId', 'sessionKey', 'gatewayInstanceId', 'operationId',
                   'inputSha256', 'programSha256', 'policySha256', 'registrationSha256'))
    need(all(type(native[k]) is str and 0 < len(native[k]) <= 256
             and native[k].isascii() and all(ord(c) >= 32 for c in native[k])
             for k in ('jobId', 'runId', 'sessionKey')))
    need(all(y.hex_value(native[k], 32) for k in ('gatewayInstanceId', 'operationId')))
    need(all(y.hex_value(native[k]) for k in ('inputSha256', 'programSha256', 'policySha256', 'registrationSha256')))
    source_hashes(value['sourceHashes'])
    budget = value['budget']
    exact(budget, ('maxNativeAttempts', 'maxNativeOperations', 'maxWorkInvocations',
                   'maxVerificationInvocations', 'expiresAtMs'))
    need(type(budget['maxNativeAttempts']) is int and budget['maxNativeAttempts'] == 1
         and type(budget['maxNativeOperations']) is int and budget['maxNativeOperations'] == 1
         and y.integer(budget['maxWorkInvocations'], 1, 9)
         and y.integer(budget['maxVerificationInvocations'], 1, 2)
         and y.integer(budget['expiresAtMs'], 1))
    target = value['target']
    exact(target, ('rootPin', 'plan', 'planSha256'))
    root_pin(target['rootPin'])
    hp, yp = value['rootPin']['path'], target['rootPin']['path']
    need(hp != yp and not hp.startswith(yp + '/') and not yp.startswith(hp + '/')
         and (value['rootPin']['dev'], value['rootPin']['ino']) !=
             (target['rootPin']['dev'], target['rootPin']['ino']))
    plan = y.plan_value(compact(target['plan']))
    need(sha(compact(plan)) == target['planSha256'])
    need(plan['limits'] == {'maxWorkInvocations': max(1, budget['maxWorkInvocations'] - 1),
                           'maxVerificationInvocations': budget['maxVerificationInvocations'],
                           'expiresAtMs': budget['expiresAtMs']})
    return value


def receipt_value(value, manifest, checksum):
    y.canonical(compact(value), MAX_RECEIPT)
    exact(value, ('format', 'handoffSha256', 'native', 'sourceHashes', 'nativeOutcome',
                   'usage', 'execution', 'artifactBase64', 'stop'))
    need(type(value['format']) is int and value['format'] == 1 and value['handoffSha256'] == checksum
         and value['native'] == manifest['native'] and value['sourceHashes'] == manifest['sourceHashes'])
    exact(value['usage'], ('attempts', 'operations'))
    need(all(type(value['usage'][k]) is int and value['usage'][k] == 1 for k in value['usage']))
    observed, native = value['nativeOutcome'], manifest['native']
    exact(observed, ('registrationSha256', 'phase', 'sessionId', 'sessionCreateOperationId',
                     'launchOperationId', 'nativeJoined', 'capabilityRetired', 'cancellationRequested',
                     'token', 'identity', 'holderRetained', 'providerInactive', 'reason',
                     'nextPermittedAction', 'allowDispatch', 'allowRelease', 'allowRetry', 'allowPublication'))
    need(observed['registrationSha256'] == native['registrationSha256']
         and observed['phase'] == 'observed-returned' and observed['nativeJoined'] is True
         and observed['capabilityRetired'] is True and observed['cancellationRequested'] is False
         and observed['holderRetained'] is True and observed['providerInactive'] == 'unknown'
         and observed['nextPermittedAction'] == 'inspect-only')
    need(all(observed[k] is False for k in ('allowDispatch', 'allowRelease', 'allowRetry', 'allowPublication')))
    need(observed['identity'] == {k: native[k] for k in ('jobId', 'runId', 'sessionKey', 'gatewayInstanceId')})
    y.token(observed['token'])
    need(observed['token']['revision'] == 4 and type(observed['sessionId']) is str
         and 0 < len(observed['sessionId']) <= 256 and observed['sessionId'].isascii()
         and all(y.hex_value(observed[k], 32) for k in ('sessionCreateOperationId', 'launchOperationId'))
         and type(observed['reason']) is str and len(observed['reason']) <= 512)
    execution = value['execution']
    exact(execution, ('operationId', 'inputSha256', 'programSha256', 'policySha256', 'workerId',
                       'startedAt', 'artifactSha256', 'code', 'clientJoined', 'sessionId', 'nativeLaunchOperationId'))
    need(all(execution[k] == native[k] for k in ('operationId', 'inputSha256', 'programSha256', 'policySha256'))
         and y.hex_value(execution['workerId']) and execution['clientJoined'] is True
         and type(execution['code']) is int and execution['code'] == 0
         and execution['sessionId'] == observed['sessionId']
         and execution['nativeLaunchOperationId'] == observed['launchOperationId'])
    raw = y.artifact(value['artifactBase64'])
    need(sha(raw) == execution['artifactSha256']
         and value['artifactBase64'] == manifest['target']['plan']['initialArtifactBase64'])
    stopped = value['stop']
    exact(stopped, ('workerId', 'startedAt', 'finishedAt', 'state', 'clientJoined'))
    need(stopped['workerId'] == execution['workerId'] and stopped['startedAt'] == execution['startedAt']
         and stopped['state'] == 'exited' and stopped['clientJoined'] is True)
    def stamp(text):
        need(type(text) is str)
        matched = re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z', text)
        need(matched is not None and not text.startswith('0001-'))
        parsed = datetime.datetime.strptime(matched.group(1), '%Y-%m-%dT%H:%M:%S')
        return parsed, (matched.group(2) or '').ljust(9, '0')
    need(stamp(stopped['finishedAt']) >= stamp(stopped['startedAt']))
    return value


def apply(state, event, manifest, checksum):
    exact(event, ('kind', 'atMs', 'data'))
    kind, data = event['kind'], event['data']
    need(state['status'] not in TERMINAL)
    if kind == 'native-reserved':
        exact(data, ('operationId',))
        need(state['status'] == 'registered' and data['operationId'] == manifest['native']['operationId']
             and event['atMs'] < manifest['budget']['expiresAtMs'])
        state.update(status='native-pending', nativeAttempts=1, nativeOperations=1)
    elif kind == 'artifact-captured':
        exact(data, ('receiptSha256', 'receipt'))
        need(state['status'] == 'native-pending' and sha(compact(data['receipt'])) == data['receiptSha256'])
        proof = receipt_value(data['receipt'], manifest, checksum)
        state.update(status='captured', receiptSha256=data['receiptSha256'],
                     artifactSha256=proof['execution']['artifactSha256'], receipt=proof)
    elif kind == 'import-intent':
        exact(data, ('planSha256', 'rootPin'))
        need(state['status'] == 'captured' and data == {k: manifest['target'][k] for k in ('planSha256', 'rootPin')}
             and manifest['budget']['maxWorkInvocations'] > 1 and event['atMs'] < manifest['budget']['expiresAtMs'])
        state['status'] = 'import-pending'
    elif kind == 'imported':
        exact(data, ('yToken',))
        need(state['status'] == 'import-pending' and data['yToken'] ==
             {'revision': 0, 'chainSha256': manifest['target']['planSha256']})
        state.update(status='imported', yMinimumToken=data['yToken'])
    elif kind == 'y-observed':
        exact(data, ('snapshot',))
        snap = data['snapshot']
        exact(snap, ('jobSha256', 'status', 'completedSteps', 'artifactBase64', 'artifactSha256',
                     'workInvocations', 'verificationInvocations', 'pending', 'cancelRequested', 'token'))
        need(state['status'] == 'imported'
             and snap['jobSha256'] == manifest['target']['planSha256'])
        y.token(snap['token'])
        prior = state['yMinimumToken']
        need(snap['token']['revision'] >= prior['revision']
             and (snap['token']['revision'] != prior['revision'] or snap['token'] == prior))
        need(y.integer(snap['workInvocations'], high=manifest['target']['plan']['limits']['maxWorkInvocations'])
             and y.integer(snap['verificationInvocations'], high=manifest['budget']['maxVerificationInvocations'])
             and y.integer(snap['completedSteps'], high=len(manifest['target']['plan']['steps']))
             and snap['workInvocations'] >= snap['completedSteps']
             and type(snap['cancelRequested']) is bool
             and snap['status'] in ('ready', 'running', 'complete', 'cancelled', 'blocked', 'exhausted')
             and sha(y.artifact(snap['artifactBase64'])) == snap['artifactSha256'])
        if snap['pending'] is not None:
            exact(snap['pending'], ('id', 'kind', 'index', 'inputSha256'))
            need(y.hex_value(snap['pending']['id'], 32) and snap['pending']['kind'] in ('step', 'verify')
                 and snap['pending']['index'] == snap['completedSteps']
                 and y.hex_value(snap['pending']['inputSha256']))
        if snap['status'] == 'complete':
            need(snap['completedSteps'] == len(manifest['target']['plan']['steps'])
                 and snap['verificationInvocations'] >= 1 and snap['pending'] is None)
        if state['ySnapshot'] is not None:
            old = state['ySnapshot']
            need(all(snap[k] >= old[k] for k in ('workInvocations', 'verificationInvocations', 'completedSteps'))
                 and (not old['cancelRequested'] or snap['cancelRequested']))
        state.update(yMinimumToken=snap['token'], ySnapshot=snap)
    elif kind == 'terminal':
        exact(data, ('status', 'reason', 'cancelRequested'))
        need(data['status'] in TERMINAL and state['status'] != 'imported'
             and type(data['reason']) is str and 0 < len(data['reason']) <= 160
             and type(data['cancelRequested']) is bool
             and (data['status'] != 'cancelled' or data['cancelRequested'])
             and (data['status'] != 'exhausted' or manifest['budget']['maxWorkInvocations'] <= 1
                  or event['atMs'] >= manifest['budget']['expiresAtMs']))
        state.update(status=data['status'], reason=data['reason'], cancelRequested=data['cancelRequested'])
    else:
        y.fail()


class Handoff(y._Store):
    def __init__(self, files, database, text, anchor):
        self._files, self._database = files, database
        self._plan_text, self._plan = text, manifest_value(text)
        self._sha, self._anchor_bytes = sha(text), anchor
        self._closed, self._poisoned, self._cancel_seen = False, False, False
        self._high_water, self._pid = None, os.getpid()

    def _state(self):
        self._check()
        need(self._database.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY name LIMIT 4').fetchall()
             == [('table', name, y.SCHEMA[name]) for name in sorted(y.SCHEMA)])
        need(self._database.execute('SELECT * FROM binding LIMIT 2').fetchall() == [(1, self._plan_text, self._sha)])
        headers = self._database.execute('SELECT * FROM header LIMIT 2').fetchall()
        need(len(headers) == 1 and headers[0][0] == 1 and y.integer(headers[0][1], high=64))
        rows = self._database.execute('SELECT revision,body,parent,digest FROM events ORDER BY revision LIMIT 65').fetchall()
        need(len(rows) == headers[0][1])
        state = {'handoffSha256': self._sha, 'status': 'registered', 'nativeAttempts': 0, 'nativeOperations': 0,
                 'receiptSha256': None, 'artifactSha256': None, 'receipt': None, 'yMinimumToken': None,
                 'ySnapshot': None, 'cancelRequested': False, 'claimReleased': False,
                 'nativeReplayAllowed': False, 'providerInactive': 'unknown'}
        parent, last, heads = self._sha, 0, [self._sha]
        for index, (revision, body, previous, checksum) in enumerate(rows, 1):
            need(revision == index and previous == parent and checksum == sha(parent + '\n' + body))
            event = y.canonical(body, MAX_EVENT)
            need(y.integer(event.get('atMs'), max(1, last)))
            apply(state, event, self._plan, self._sha)
            parent, last = checksum, event['atMs']
            heads.append(parent)
        need(headers[0][2] == parent)
        anchored = y.anchor_value(self._anchor_bytes, self._files, self._sha)['token']
        for expected in (anchored, self._high_water):
            if expected is not None:
                need(headers[0][1] >= expected['revision'] and heads[expected['revision']] == expected['chainSha256'])
        self._high_water = {'revision': headers[0][1], 'chainSha256': parent}
        state['token'] = dict(self._high_water)
        state['cancelRequested'] = self._cancel_present() or state['cancelRequested']
        return state, last

    def _transaction(self, mutate=None):
        began = False
        try:
            with y.transition_lock(self._files):
                self._check()
                self._database.execute('BEGIN IMMEDIATE' if mutate else 'BEGIN')
                began = True
                current, at = self._state()
                if mutate:
                    need(current['token']['revision'] < 64)
                    now = time.time_ns() // 1000000
                    need(y.integer(now, max(1, at)))
                    kind, data = mutate(current, now)
                    body = compact({'kind': kind, 'atMs': now, 'data': data})
                    y.canonical(body, MAX_EVENT)
                    parent = current['token']['chainSha256']
                    revision, checksum = current['token']['revision'] + 1, sha(parent + '\n' + body)
                    self._database.execute('INSERT INTO events VALUES (?,?,?,?)', (revision, body, parent, checksum))
                    changed = self._database.execute('UPDATE header SET revision=?,digest=? WHERE singleton=1 AND revision=? AND digest=?',
                                                     (revision, checksum, current['token']['revision'], parent))
                    need(changed.rowcount == 1)
                    current, _ = self._state()
                self._check()
                self._database.execute('COMMIT')
                began = False
                if mutate:
                    self._files.sync()
                    self._advance_anchor(current['token'])
                return current
        except Exception:
            if began:
                try:
                    self._database.execute('ROLLBACK')
                except Exception:
                    pass
            self._poisoned = True
            y.fail()

    def event(self, kind, data, permit_cancel=False):
        expected = self.snapshot()['token']
        def mutation(current, now):
            need(current['token'] == expected and current['status'] not in TERMINAL)
            need(permit_cancel or not current['cancelRequested'])
            return kind, data
        return self._transaction(mutation)

    def terminal(self, status, reason):
        return self.event('terminal', {'status': status, 'reason': reason,
                                      'cancelRequested': self.snapshot()['cancelRequested']}, True)


def initialize(root, text):
    manifest = manifest_value(text)
    files = database = None
    try:
        need(str(root) == manifest['rootPin']['path'])
        files = y._Files(root, manifest['rootPin'])
        target = y._Files(manifest['target']['rootPin']['path'], manifest['target']['rootPin'])
        try:
            need(not os.listdir(files.root) and not os.listdir(target.root))
        finally:
            target.close()
        fd = files.open_file(y.LOCK, 0, True)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        need(set(os.listdir(files.root)) == {y.LOCK})
        files.open_file(y.CANCEL_LOCK, 0, True)
        with y.transition_lock(files):
            fd = files.open_file(y.REGISTRATION, MAX_MANIFEST, True)
            y.write_all(fd, text.encode('ascii'))
            os.fsync(fd)
            files.open_file(y.DATABASE, y.MAX_DATABASE, True)
            database = y.connect(files)
            database.execute('BEGIN IMMEDIATE')
            for sql in y.SCHEMA.values():
                database.execute(sql)
            checksum = sha(text)
            database.execute('INSERT INTO binding VALUES (1,?,?)', (text, checksum))
            database.execute('INSERT INTO header VALUES (1,0,?)', (checksum,))
            database.execute('COMMIT')
            files.sync()
            raw = y.anchor_bytes(files, checksum, {'revision': 0, 'chainSha256': checksum})
            fd = files.open_file(y.ANCHOR, 768, True)
            y.write_all(fd, raw)
            os.fsync(fd)
            os.fsync(files.directories[-1][1])
        handle = Handoff(files, database, text, raw)
        result = handle.event('native-reserved', {'operationId': manifest['native']['operationId']})
        return result
    finally:
        if database is not None:
            database.close()
        if files is not None:
            files.close()


def open_handoff(root, text):
    manifest = manifest_value(text)
    files = database = None
    try:
        need(str(root) == manifest['rootPin']['path'])
        files = y._Files(root, manifest['rootPin'])
        fd = files.open_file(y.LOCK, 0)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        files.open_file(y.CANCEL_LOCK, 0)
        with y.transition_lock(files):
            files.open_file(y.REGISTRATION, MAX_MANIFEST)
            files.open_file(y.DATABASE, y.MAX_DATABASE)
            files.open_file(y.ANCHOR, 768)
            need(files.read(y.REGISTRATION) == text.encode('ascii'))
            anchor = files.read(y.ANCHOR)
            y.anchor_value(anchor, files, sha(text))
            database = y.connect(files)
        handle = Handoff(files, database, text, anchor)
        handle.snapshot()
        return handle
    except BaseException:
        if database is not None:
            database.close()
        if files is not None:
            files.close()
        raise


def request_cancel(root, text):
    manifest = manifest_value(text)
    files, fd = None, None
    try:
        need(str(root) == manifest['rootPin']['path'])
        files = y._Files(root, manifest['rootPin'])
        for name, cap in ((y.LOCK, 0), (y.CANCEL_LOCK, 0), (y.REGISTRATION, MAX_MANIFEST),
                          (y.DATABASE, y.MAX_DATABASE), (y.ANCHOR, 768)):
            files.open_file(name, cap)
        with y.transition_lock(files):
            need(files.read(y.REGISTRATION) == text.encode('ascii'))
            y.anchor_value(files.read(y.ANCHOR), files, sha(text))
            raw = y.cancellation_bytes(sha(text))
            if os.path.lexists(os.path.join(files.root, y.CANCEL)):
                files.open_file(y.CANCEL, 128)
                need(files.read(y.CANCEL) == raw)
            else:
                temporary = os.path.join(files.root, y.TEMP_CANCEL)
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
                y.write_all(fd, raw)
                os.fsync(fd)
                y.regular(os.fstat(fd), 128)
                need(y.identity(os.fstat(fd)) == y.identity(os.lstat(temporary)))
                files.check_directories()
                os.rename(temporary, os.path.join(files.root, y.CANCEL))
                os.fsync(files.directories[-1][1])
        # Dispatch/commit always consult H's mailbox while holding its short
        # lock. Forwarding is not required here and could race Y initialization.
        return {'requested': True, 'handoffSha256': sha(text)}
    finally:
        if fd is not None:
            os.close(fd)
        if files is not None:
            files.close()


class GatedY:
    """Order H cancellation against unchanged Y mutations; same lock order."""
    def __init__(self, handoff, job):
        self.handoff, self.job = handoff, job

    @property
    def plan_text(self):
        return self.job.plan_text

    def _call(self, name, *args):
        h, target = self.handoff, self.handoff._plan['target']
        with y.transition_lock(h._files):
            h._check()
            if h._cancel_present():
                y.request_cancel(target['rootPin']['path'], target['planSha256'], target['rootPin'])
            return getattr(self.job, name)(*args)

    def snapshot(self):
        return self._call('snapshot')

    def cancel_requested(self):
        return self._call('cancel_requested')

    def reserve(self, *args):
        return self._call('reserve', *args)

    def checkpoint(self, *args):
        return self._call('checkpoint', *args)

    def complete(self, *args):
        return self._call('complete', *args)

    def mark_terminal(self, *args):
        return self._call('mark_terminal', *args)


def validate_y_head(handle, expected):
    snapshot = handle.snapshot()
    if expected is not None:
        need(snapshot['token']['revision'] >= expected['revision'])
        actual = snapshot['jobSha256'] if expected['revision'] == 0 else handle._database.execute(
            'SELECT digest FROM events WHERE revision=?', (expected['revision'],)).fetchone()[0]
        need(actual == expected['chainSha256'])
    return snapshot


def resume(handle, receipt=None, adapter_factory=None, crash_hook=None):
    """Host-only import/run. adapter_factory is the inert local test seam."""
    hook = crash_hook or (lambda *_: None)
    state, manifest = handle.snapshot(), handle._plan
    if receipt is not None and state['receiptSha256'] is not None:
        need(sha(compact(receipt)) == state['receiptSha256'])
    if state['status'] in TERMINAL:
        return state
    if state['status'] != 'imported':
        if state['cancelRequested']:
            return handle.terminal('cancelled', 'handoff-cancel-observed-before-import')
        if time.time_ns() // 1000000 >= manifest['budget']['expiresAtMs']:
            return handle.terminal('exhausted', 'handoff-deadline-reached')
        if state['status'] == 'registered':
            return handle.terminal('blocked', 'native-reservation-unconfirmed')
        if state['status'] == 'native-pending':
            if receipt is None:
                return state  # No native retry or invented completion.
            proof = receipt_value(receipt, manifest, handle._sha)
            state = handle.event('artifact-captured', {'receiptSha256': sha(compact(proof)), 'receipt': proof})
            hook('captured', state)
        elif receipt is not None:
            need(sha(compact(receipt)) == state['receiptSha256'])
        if manifest['budget']['maxWorkInvocations'] <= 1:
            return handle.terminal('exhausted', 'native-operation-consumed-work-budget')
        if state['status'] == 'captured':
            state = handle.event('import-intent', {k: manifest['target'][k] for k in ('planSha256', 'rootPin')})
            hook('import-intent', state)
        if state['status'] == 'import-pending':
            target = manifest['target']
            files = y._Files(target['rootPin']['path'], target['rootPin'])
            try:
                empty = not os.listdir(files.root)
            finally:
                files.close()
            if empty:
                y.initialize(target['rootPin']['path'], compact(target['plan']))
                hook('initialized', handle.snapshot())
            # Missing/partial state is never removed or repaired. A valid init
            # with lost acknowledgement is adopted, only at untouched rev zero.
            with y.open_job(target['rootPin']['path'], target['planSha256'], target['rootPin']) as job:
                snap = job.snapshot()
                need(snap['token'] == {'revision': 0, 'chainSha256': target['planSha256']}
                     and snap['status'] == 'ready' and snap['pending'] is None
                     and not snap['cancelRequested'])
                state = handle.event('imported', {'yToken': snap['token']})
            hook('imported', state)
    target = manifest['target']
    with y.open_job(target['rootPin']['path'], target['planSha256'], target['rootPin']) as job:
        state = handle.snapshot()
        validate_y_head(job, state['yMinimumToken'])
        adapter = (adapter_factory(target['plan']) if adapter_factory else
                   runner.LazyDocker(target['plan'], os.path.dirname(target['rootPin']['path'])))
        try:
            outcome = runner.run_workflow(GatedY(handle, job), adapter, hook)
        finally:
            adapter.close()
        state = handle.snapshot()
        if state['ySnapshot'] != outcome:
            state = handle.event('y-observed', {'snapshot': outcome}, True)
    return state


def read_pinned(path, checksum, maximum):
    need(y.hex_value(checksum) and type(path) is str and os.path.isabs(path)
         and os.path.normpath(path) == path)
    files = y._Files(os.path.dirname(path))
    try:
        name = os.path.basename(path)
        files.open_file(name, maximum)
        raw = files.read(name)
        need(sha(raw) == checksum)
        return raw.decode('ascii')
    finally:
        files.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'status', 'resume', 'cancel'))
    parser.add_argument('--root', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--receipt')
    parser.add_argument('--receipt-sha256')
    parser.add_argument('--crash-after', choices=('captured', 'import-intent', 'initialized', 'imported', 'checkpoint', 'reserved'))
    args = parser.parse_args(argv)
    if bool(args.receipt) != bool(args.receipt_sha256) or ((args.receipt or args.crash_after) and args.command != 'resume'):
        parser.error('receipt/hash and fault hook require resume')
    try:
        text = read_pinned(args.manifest, args.sha256, MAX_MANIFEST)
        manifest_value(text)
        if args.command == 'prepare':
            result = initialize(args.root, text)
        elif args.command == 'cancel':
            result = request_cancel(args.root, text)
        else:
            with open_handoff(args.root, text) as handle:
                if args.command == 'status':
                    result = handle.snapshot()
                else:
                    proof = y.canonical(read_pinned(args.receipt, args.receipt_sha256, MAX_RECEIPT), MAX_RECEIPT) if args.receipt else None
                    adapters = []
                    def factory(plan):
                        adapter = runner.LazyDocker(plan, os.path.dirname(handle._plan['target']['rootPin']['path']))
                        adapters.append(adapter)
                        return adapter
                    def adapter_status():
                        if adapters:
                            return adapters[-1].status()
                        return {'getRequests': 0, 'startRequests': 0, 'execRequests': 0, 'stopRequests': 0,
                                'closed': True, 'clientJoined': True}
                    def crash(point, snapshot):
                        if point == args.crash_after:
                            runner.emit('handoffCrashed', {'point': point, 'snapshot': snapshot, 'adapter': adapter_status()})
                            os.kill(os.getpid(), signal.SIGKILL)
                    try:
                        result = resume(handle, proof, adapter_factory=factory, crash_hook=crash)
                    finally:
                        runner.emit('handoffAdapter', adapter_status())
        runner.emit('handoffResult', result)
        return 0
    except Exception:
        print('native-artifact-handoff-unconfirmed', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
