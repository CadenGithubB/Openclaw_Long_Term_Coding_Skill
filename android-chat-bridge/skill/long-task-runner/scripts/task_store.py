"""Durable single-artifact task state. No worker, command or network execution.

A lifetime task.lock serializes runners. A separate short cancel.lock serializes
state transitions with cancellation publication; requesting cancellation never
needs the task lock. Immutable plan and verified checkpoint bytes are host owned.
"""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from urllib.parse import quote

LOCK = 'task.lock'
CANCEL_LOCK = 'cancel.lock'
REGISTRATION = 'plan.json'
DATABASE = 'state.sqlite'
ANCHOR = 'anchor.json'
TEMP_ANCHOR = 'anchor.pending'
CANCEL = 'cancel.request'
TEMP_CANCEL = 'cancel.pending'
MAX_EVENTS = 64
MAX_DATABASE = 2097152
MAX_PLAN = 32768
MAX_ARTIFACT = 8192
SCHEMA = {
    'binding': 'CREATE TABLE binding (singleton INTEGER PRIMARY KEY CHECK(singleton=1), body TEXT NOT NULL, digest TEXT NOT NULL) STRICT',
    'events': 'CREATE TABLE events (revision INTEGER PRIMARY KEY, body TEXT NOT NULL, parent TEXT NOT NULL, digest TEXT NOT NULL) STRICT',
    'header': 'CREATE TABLE header (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL, digest TEXT NOT NULL) STRICT',
}
TERMINAL = ('complete', 'cancelled', 'blocked', 'exhausted')


class StoreError(RuntimeError):
    pass


def fail():
    raise StoreError('task-store-unavailable-or-unconfirmed')


def demand(value):
    if not value:
        fail()


def compact(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode('ascii') if isinstance(value, str) else value).hexdigest()


def hex_value(value, length=64):
    return type(value) is str and re.fullmatch('[a-f0-9]{%d}' % length, value) is not None


def integer(value, low=0, high=9007199254740991):
    return type(value) is int and low <= value <= high


def exact(value, keys):
    demand(type(value) is dict and set(value) == set(keys))


def unique(pairs):
    value = {}
    for key, item in pairs:
        demand(key not in value)
        value[key] = item
    return value


def primitive(value, depth=0):
    demand(depth <= 8)
    if type(value) is dict:
        demand(len(value) <= 128 and all(type(k) is str for k in value))
        for item in value.values():
            primitive(item, depth + 1)
    elif type(value) is list:
        demand(len(value) <= 128)
        for item in value:
            primitive(item, depth + 1)
    else:
        demand(value is None or type(value) in (str, bool, int, float))


def canonical(text, maximum):
    demand(type(text) is str and 0 < len(text) <= maximum and text.isascii())
    value = json.loads(text, object_pairs_hook=unique, parse_constant=lambda _: fail())
    primitive(value)
    demand(compact(value) == text)
    return value


def token(value):
    exact(value, ('revision', 'chainSha256'))
    demand(integer(value['revision'], high=MAX_EVENTS) and hex_value(value['chainSha256']))
    return dict(value)


def artifact(text):
    demand(type(text) is str and len(text) <= 10924 and text.isascii())
    raw = base64.b64decode(text, validate=True)
    demand(len(raw) <= MAX_ARTIFACT and base64.b64encode(raw).decode('ascii') == text)
    return raw


def argv(value):
    demand(type(value) is list and 1 <= len(value) <= 32)
    demand(all(type(s) is str and 0 < len(s) <= 1024 and '\0' not in s for s in value))


def plan_value(text):
    value = canonical(text, MAX_PLAN)
    exact(value, ('format', 'jobId', 'initialArtifactBase64', 'steps', 'verifier', 'limits', 'runtime', 'workers'))
    demand(type(value['format']) is int and value['format'] == 1 and hex_value(value['jobId'], 32))
    artifact(value['initialArtifactBase64'])
    steps = value['steps']
    demand(type(steps) is list and 1 <= len(steps) <= 4)
    names = set()
    for step in steps:
        exact(step, ('id', 'argv'))
        demand(type(step['id']) is str and re.fullmatch('[a-z][a-z0-9-]{0,47}', step['id']) is not None
               and step['id'] not in names)
        names.add(step['id'])
        argv(step['argv'])
    exact(value['verifier'], ('argv', 'expectedStdoutSha256'))
    argv(value['verifier']['argv'])
    demand(hex_value(value['verifier']['expectedStdoutSha256']))
    limits = value['limits']
    exact(limits, ('maxWorkInvocations', 'maxVerificationInvocations', 'expiresAtMs'))
    demand(integer(limits['maxWorkInvocations'], 1, 8)
           and integer(limits['maxVerificationInvocations'], 1, 2)
           and integer(limits['expiresAtMs'], 1))
    demand(type(value['runtime']) is dict and bool(value['runtime']))
    demand(type(value['workers']) is list and len(value['workers']) == len(steps) + 1
           and all(type(w) is dict and bool(w) for w in value['workers']))
    return value


def receipt_value(value):
    demand(type(value) is dict and bool(value))
    return canonical(compact(value), 4096)


def identity(meta):
    return meta.st_dev, meta.st_ino


def regular(meta, maximum):
    demand(stat.S_ISREG(meta.st_mode) and meta.st_uid == os.getuid() and meta.st_nlink == 1
           and stat.S_IMODE(meta.st_mode) == 0o600 and 0 <= meta.st_size <= maximum)


class _Files:
    def __init__(self, root, root_pin=None):
        self.root = str(root)
        self.directories = []
        self.files = {}
        try:
            demand(os.path.isabs(self.root) and os.path.normpath(self.root) == self.root
                   and self.root != '/' and len(self.root) <= 2048
                   and not any(ord(c) < 32 for c in self.root))
            current = '/'
            for part in ('', *self.root[1:].split('/')):
                if part:
                    current = os.path.join(current, part)
                fd = os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                self.directories.append((current, fd, identity(os.fstat(fd))))
            self.check_directories()
            actual = os.fstat(self.directories[-1][1])
            self.root_pin = {'path': self.root, 'dev': actual.st_dev, 'ino': actual.st_ino,
                             'uid': actual.st_uid, 'mode': stat.S_IMODE(actual.st_mode)}
            if root_pin is not None:
                exact(root_pin, ('path', 'dev', 'ino', 'uid', 'mode'))
                demand(all(type(root_pin[k]) is int for k in ('dev', 'ino', 'uid', 'mode'))
                       and root_pin == self.root_pin)
        except BaseException:
            self.close()
            raise

    def check_directories(self):
        for name, fd, expected in self.directories:
            actual, named = os.fstat(fd), os.lstat(name)
            sticky_root = actual.st_uid == 0 and actual.st_mode & stat.S_ISVTX
            demand(stat.S_ISDIR(actual.st_mode) and stat.S_ISDIR(named.st_mode)
                   and identity(actual) == identity(named) == expected
                   and actual.st_uid in (0, os.getuid())
                   and (not actual.st_mode & 0o022 or sticky_root))
        leaf = os.fstat(self.directories[-1][1])
        demand(leaf.st_uid == os.getuid() and stat.S_IMODE(leaf.st_mode) == 0o700)

    def open_file(self, name, maximum, create=False):
        self.check_directories()
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        fd = os.open(os.path.join(self.root, name), flags, 0o600)
        try:
            regular(os.fstat(fd), maximum)
            self.files[name] = (fd, identity(os.fstat(fd)), maximum)
            self.check_file(name)
            return fd
        except BaseException:
            if name in self.files:
                del self.files[name]
            os.close(fd)
            raise

    def check_file(self, name):
        fd, expected, maximum = self.files[name]
        actual, named = os.fstat(fd), os.lstat(os.path.join(self.root, name))
        regular(actual, maximum)
        regular(named, maximum)
        demand(identity(actual) == identity(named) == expected)

    def check(self):
        self.check_directories()
        for name in self.files:
            self.check_file(name)
        names = set(os.listdir(self.root))
        demand(names <= {LOCK, CANCEL_LOCK, REGISTRATION, DATABASE, ANCHOR, CANCEL, DATABASE + '-journal'})
        if DATABASE + '-journal' in names:
            regular(os.lstat(os.path.join(self.root, DATABASE + '-journal')), MAX_DATABASE * 2)

    def read(self, name):
        self.check_file(name)
        fd, _, maximum = self.files[name]
        size = os.fstat(fd).st_size
        raw = os.pread(fd, maximum + 1, 0)
        demand(len(raw) == size and len(raw) <= maximum)
        self.check_file(name)
        return raw

    def sync(self):
        self.check()
        os.fsync(self.files[DATABASE][0])
        os.fsync(self.directories[-1][1])
        self.check()

    def close(self):
        # Closing the same persistent lock FD releases the kernel lock. Never
        # unlink it: a second inode would allow two independently locked writers.
        for fd, _, _ in self.files.values():
            os.close(fd)
        self.files.clear()
        for _, fd, _ in reversed(self.directories):
            os.close(fd)
        self.directories.clear()


def write_all(fd, raw):
    offset = 0
    while offset < len(raw):
        count = os.write(fd, raw[offset:])
        demand(count > 0)
        offset += count


def connect(files):
    files.check()
    database = sqlite3.connect('file:' + quote(os.path.join(files.root, DATABASE), safe='/') + '?mode=rw',
                               uri=True, timeout=0, isolation_level=None)
    try:
        database.execute('PRAGMA trusted_schema=OFF')
        database.execute('PRAGMA synchronous=FULL')
        database.execute('PRAGMA fullfsync=ON')
        database.execute('PRAGMA busy_timeout=0')
        database.execute('PRAGMA cell_size_check=ON')
        database.execute('PRAGMA max_page_count=512')
        for key, expected in {'journal_mode': 'delete', 'synchronous': 2, 'fullfsync': 1,
                              'busy_timeout': 0, 'trusted_schema': 0, 'cell_size_check': 1,
                              'page_size': 4096, 'max_page_count': 512}.items():
            demand(database.execute('PRAGMA ' + key).fetchone() == (expected,))
        files.check()
        return database
    except BaseException:
        database.close()
        raise


@contextmanager
def transition_lock(files):
    files.check_file(CANCEL_LOCK)
    fd = files.files[CANCEL_LOCK][0]
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        files.check_file(CANCEL_LOCK)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


def anchor_value(raw, files, checksum):
    value = canonical(raw.decode('ascii'), 768)
    exact(value, ('format', 'jobSha256', 'lockIdentity', 'cancelLockIdentity', 'token'))
    demand(type(value['format']) is int and value['format'] == 1 and value['jobSha256'] == checksum)
    for key, name in (('lockIdentity', LOCK), ('cancelLockIdentity', CANCEL_LOCK)):
        exact(value[key], ('dev', 'ino'))
        demand(all(type(value[key][k]) is int for k in ('dev', 'ino'))
               and (value[key]['dev'], value[key]['ino']) == files.files[name][1])
    token(value['token'])
    return value


def anchor_bytes(files, checksum, current):
    def pin(name):
        dev, ino = files.files[name][1]
        return {'dev': dev, 'ino': ino}
    return compact({'format': 1, 'jobSha256': checksum, 'lockIdentity': pin(LOCK),
                    'cancelLockIdentity': pin(CANCEL_LOCK), 'token': current}).encode('ascii')


def cancellation_bytes(checksum):
    return compact({'format': 1, 'jobSha256': checksum}).encode('ascii')


def exhausted(state, plan, at_ms):
    limits = plan['limits']
    return (at_ms >= limits['expiresAtMs']
            or (state['completedSteps'] < len(plan['steps'])
                and state['workInvocations'] >= limits['maxWorkInvocations'])
            or (state['completedSteps'] == len(plan['steps'])
                and state['verificationInvocations'] >= limits['maxVerificationInvocations']))


def apply_event(state, event, plan, seen):
    """Replay and independently enforce every durable transition."""
    exact(event, ('kind', 'atMs', 'data'))
    kind, data, now = event['kind'], event['data'], event['atMs']
    demand(state['status'] not in TERMINAL)
    if kind == 'reserved':
        exact(data, ('id', 'kind', 'index', 'inputSha256'))
        demand(state['status'] == 'ready' and state['pending'] is None
               and not state['cancelRequested'] and now < plan['limits']['expiresAtMs']
               and hex_value(data['id'], 32) and data['id'] not in seen
               and data['kind'] in ('step', 'verify')
               and integer(data['index'], high=len(plan['steps']))
               and data['index'] == state['completedSteps']
               and data['inputSha256'] == state['artifactSha256'])
        if data['kind'] == 'step':
            demand(data['index'] < len(plan['steps'])
                   and state['workInvocations'] < plan['limits']['maxWorkInvocations'])
            state['workInvocations'] += 1
        else:
            demand(data['index'] == len(plan['steps'])
                   and state['verificationInvocations'] < plan['limits']['maxVerificationInvocations'])
            state['verificationInvocations'] += 1
        seen.add(data['id'])
        state['pending'] = dict(data)
        state['status'] = 'running'
    elif kind in ('checkpoint', 'completed'):
        keys = ('operationId', 'receipt') if kind == 'completed' else ('operationId', 'artifactBase64', 'artifactSha256', 'receipt')
        exact(data, keys)
        pending = state['pending']
        demand(state['status'] == 'running' and not state['cancelRequested']
               and now < plan['limits']['expiresAtMs'] and pending is not None
               and data['operationId'] == pending['id']
               and pending['inputSha256'] == state['artifactSha256'])
        receipt_value(data['receipt'])
        if kind == 'checkpoint':
            demand(pending['kind'] == 'step' and pending['index'] == state['completedSteps'])
            raw = artifact(data['artifactBase64'])
            demand(data['artifactSha256'] == digest(raw))
            state['artifactBase64'], state['artifactSha256'] = data['artifactBase64'], data['artifactSha256']
            state['completedSteps'] += 1
            state['status'] = 'ready'
        else:
            demand(pending['kind'] == 'verify' and pending['index'] == len(plan['steps'])
                   and state['completedSteps'] == len(plan['steps']))
            state['status'] = 'complete'
        state['pending'] = None
    elif kind == 'terminal':
        exact(data, ('status', 'reason', 'cancelRequested'))
        demand(data['status'] in ('cancelled', 'blocked', 'exhausted')
               and type(data['reason']) is str and 0 < len(data['reason']) <= 512
               and type(data['cancelRequested']) is bool
               and (not state['cancelRequested'] or data['cancelRequested']))
        demand(data['status'] != 'cancelled' or data['cancelRequested'])
        demand(data['status'] != 'exhausted' or exhausted(state, plan, now))
        state['status'], state['cancelRequested'] = data['status'], data['cancelRequested']
        # Unknown work is retained; there is deliberately no pending replay API.
    else:
        fail()


class _Store:
    def __init__(self, files, database, plan_text, anchor_raw):
        self._files, self._database = files, database
        self._plan_text, self._plan = plan_text, plan_value(plan_text)
        self._sha, self._anchor_bytes = digest(plan_text), anchor_raw
        self._closed, self._poisoned, self._cancel_seen = False, False, False
        self._high_water, self._pid = None, os.getpid()

    def _check(self):
        demand(os.getpid() == self._pid and not self._closed and not self._poisoned)
        self._files.check()
        demand(self._files.read(REGISTRATION) == self._plan_text.encode('ascii')
               and self._files.read(ANCHOR) == self._anchor_bytes)

    def _cancel_present(self):
        present = os.path.lexists(os.path.join(self._files.root, CANCEL))
        demand(present or not self._cancel_seen)
        if present:
            if CANCEL not in self._files.files:
                self._files.open_file(CANCEL, 128)
            demand(self._files.read(CANCEL) == cancellation_bytes(self._sha))
            self._cancel_seen = True
        return present

    def _state(self):
        self._check()
        schema = self._database.execute('SELECT type,name,sql FROM sqlite_schema ORDER BY name LIMIT 4').fetchall()
        demand(schema == [('table', name, SCHEMA[name]) for name in sorted(SCHEMA)])
        demand(self._database.execute('SELECT * FROM binding LIMIT 2').fetchall() == [(1, self._plan_text, self._sha)])
        headers = self._database.execute('SELECT * FROM header LIMIT 2').fetchall()
        demand(len(headers) == 1 and headers[0][0] == 1 and integer(headers[0][1], high=MAX_EVENTS)
               and hex_value(headers[0][2]))
        rows = self._database.execute('SELECT revision,body,parent,digest FROM events ORDER BY revision LIMIT 65').fetchall()
        demand(len(rows) == headers[0][1])
        initial = self._plan['initialArtifactBase64']
        state = {'jobSha256': self._sha, 'status': 'ready', 'completedSteps': 0,
                 'artifactBase64': initial, 'artifactSha256': digest(artifact(initial)),
                 'workInvocations': 0, 'verificationInvocations': 0, 'pending': None,
                 'cancelRequested': False}
        parent, last_at, heads, seen = self._sha, 0, [self._sha], set()
        for index, (revision, body, previous, checksum) in enumerate(rows, 1):
            demand(revision == index and previous == parent and checksum == digest(parent + '\n' + body))
            event = canonical(body, 16384)
            demand(integer(event.get('atMs'), max(1, last_at)))
            apply_event(state, event, self._plan, seen)
            parent, last_at = checksum, event['atMs']
            heads.append(parent)
        demand(headers[0][2] == parent)
        anchored = anchor_value(self._anchor_bytes, self._files, self._sha)['token']
        for expected in (anchored, self._high_water):
            if expected is not None:
                demand(headers[0][1] >= expected['revision'] and heads[expected['revision']] == expected['chainSha256'])
        self._high_water = {'revision': headers[0][1], 'chainSha256': parent}
        state['token'] = dict(self._high_water)
        state['cancelRequested'] = self._cancel_present() or state['cancelRequested']
        return state, last_at

    def _advance_anchor(self, current):
        self._check()
        raw = anchor_bytes(self._files, self._sha, current)
        name = os.path.join(self._files.root, TEMP_ANCHOR)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            regular(os.fstat(fd), 768)
            write_all(fd, raw)
            os.fsync(fd)
            regular(os.fstat(fd), 768)
            demand(identity(os.fstat(fd)) == identity(os.lstat(name)))
            self._files.check_directories()
            self._files.check_file(ANCHOR)
            demand(self._files.read(ANCHOR) == self._anchor_bytes)
            os.replace(name, os.path.join(self._files.root, ANCHOR))
            os.fsync(self._files.directories[-1][1])
        finally:
            os.close(fd)
        old, _, _ = self._files.files.pop(ANCHOR)
        os.close(old)
        self._files.open_file(ANCHOR, 768)
        self._anchor_bytes = raw
        self._check()

    def _transaction(self, mutate=None):
        began = False
        try:
            with transition_lock(self._files):
                self._check()
                self._database.execute('BEGIN IMMEDIATE' if mutate is not None else 'BEGIN')
                began = True
                current, at_ms = self._state()
                if mutate is not None:
                    demand(current['token']['revision'] < MAX_EVENTS)
                    now = time.time_ns() // 1000000
                    demand(integer(now, max(1, at_ms)))
                    kind, data = mutate(current, now)
                    body = compact({'kind': kind, 'atMs': now, 'data': data})
                    canonical(body, 16384)
                    parent = current['token']['chainSha256']
                    revision, checksum = current['token']['revision'] + 1, digest(parent + '\n' + body)
                    self._database.execute('INSERT INTO events VALUES (?,?,?,?)', (revision, body, parent, checksum))
                    changed = self._database.execute('UPDATE header SET revision=?,digest=? WHERE singleton=1 AND revision=? AND digest=?',
                                                     (revision, checksum, current['token']['revision'], parent))
                    demand(changed.rowcount == 1)
                    current, _ = self._state()
                self._check()
                self._database.execute('COMMIT')
                began = False
                if mutate is not None:
                    self._files.sync()
                    self._advance_anchor(current['token'])
                self._check()
                return current
        except Exception:
            if began:
                try:
                    self._database.execute('ROLLBACK')
                except Exception:
                    pass
            self._poisoned = True
            fail()

    @property
    def plan_text(self):
        self._transaction()
        return self._plan_text

    def snapshot(self):
        return self._transaction()

    def cancel_requested(self):
        return self._transaction()['cancelRequested']

    def _mutate(self, expected, action):
        def mutation(current, now):
            demand(token(expected) == current['token'] and current['status'] not in TERMINAL)
            return action(current, now)
        return self._transaction(mutation)

    def reserve(self, expected, kind, index):
        def action(current, now):
            demand(not current['cancelRequested'])
            return 'reserved', {'id': os.urandom(16).hex(), 'kind': kind, 'index': index,
                                'inputSha256': current['artifactSha256']}
        return self._mutate(expected, action)

    def checkpoint(self, expected, operation_id, artifact_bytes, receipt):
        def action(current, now):
            demand(not current['cancelRequested'] and type(artifact_bytes) is bytes and len(artifact_bytes) <= MAX_ARTIFACT)
            return 'checkpoint', {'operationId': operation_id,
                                  'artifactBase64': base64.b64encode(artifact_bytes).decode('ascii'),
                                  'artifactSha256': digest(artifact_bytes), 'receipt': receipt_value(receipt)}
        return self._mutate(expected, action)

    def complete(self, expected, operation_id, receipt):
        def action(current, now):
            demand(not current['cancelRequested'])
            return 'completed', {'operationId': operation_id, 'receipt': receipt_value(receipt)}
        return self._mutate(expected, action)

    def mark_terminal(self, expected, status, reason):
        return self._mutate(expected, lambda current, now: ('terminal', {
            'status': status, 'reason': reason, 'cancelRequested': current['cancelRequested']}))

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._database.close()
        finally:
            self._files.close()

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *_):
        self.close()


def initialize(root, plan_text):
    files, database = None, None
    try:
        plan_value(plan_text)
        files = _Files(root)
        demand(not os.listdir(files.root))
        lock = files.open_file(LOCK, 0, create=True)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        demand(set(os.listdir(files.root)) == {LOCK})
        files.open_file(CANCEL_LOCK, 0, create=True)
        with transition_lock(files):
            fd = files.open_file(REGISTRATION, MAX_PLAN, create=True)
            write_all(fd, plan_text.encode('ascii'))
            os.fsync(fd)
            files.open_file(DATABASE, MAX_DATABASE, create=True)
            database = connect(files)
            checksum = digest(plan_text)
            database.execute('BEGIN IMMEDIATE')
            for sql in SCHEMA.values():
                database.execute(sql)
            database.execute('INSERT INTO binding VALUES (1,?,?)', (plan_text, checksum))
            database.execute('INSERT INTO header VALUES (1,0,?)', (checksum,))
            database.execute('COMMIT')
            files.sync()
            raw = anchor_bytes(files, checksum, {'revision': 0, 'chainSha256': checksum})
            fd = files.open_file(ANCHOR, 768, create=True)
            write_all(fd, raw)
            os.fsync(fd)
            os.fsync(files.directories[-1][1])
        store = _Store(files, database, plan_text, raw)
        return store.snapshot()
    except Exception:
        fail()
    finally:
        if database is not None:
            database.close()
        if files is not None:
            files.close()


def open_job(root, plan_sha256, root_pin=None):
    files, database = None, None
    try:
        demand(hex_value(plan_sha256))
        files = _Files(root, root_pin)
        lock = files.open_file(LOCK, 0)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        files.open_file(CANCEL_LOCK, 0)
        with transition_lock(files):
            files.open_file(REGISTRATION, MAX_PLAN)
            files.open_file(DATABASE, MAX_DATABASE)
            files.open_file(ANCHOR, 768)
            files.check()
            text = files.read(REGISTRATION).decode('ascii')
            plan_value(text)
            demand(digest(text) == plan_sha256)
            raw = files.read(ANCHOR)
            anchor_value(raw, files, plan_sha256)
            database = connect(files)
        store = _Store(files, database, text, raw)
        store.snapshot()
        return store
    except Exception:
        if database is not None:
            database.close()
        if files is not None:
            files.close()
        fail()


def request_cancel(root, plan_sha256, root_pin=None):
    """Publish intent while a runner owns task.lock; no termination claim."""
    files, fd = None, None
    try:
        demand(hex_value(plan_sha256))
        files = _Files(root, root_pin)
        files.open_file(LOCK, 0)
        files.open_file(CANCEL_LOCK, 0)
        with transition_lock(files):
            files.open_file(REGISTRATION, MAX_PLAN)
            files.open_file(DATABASE, MAX_DATABASE)
            files.open_file(ANCHOR, 768)
            files.check()
            text = files.read(REGISTRATION).decode('ascii')
            plan_value(text)
            demand(digest(text) == plan_sha256)
            anchor_value(files.read(ANCHOR), files, plan_sha256)
            raw = cancellation_bytes(plan_sha256)
            if os.path.lexists(os.path.join(files.root, CANCEL)):
                files.open_file(CANCEL, 128)
                demand(files.read(CANCEL) == raw)
            else:
                temporary = os.path.join(files.root, TEMP_CANCEL)
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
                write_all(fd, raw)
                os.fsync(fd)
                regular(os.fstat(fd), 128)
                demand(identity(os.fstat(fd)) == identity(os.lstat(temporary)))
                files.check_directories()
                files.check_file(REGISTRATION)
                files.check_file(ANCHOR)
                demand(not os.path.lexists(os.path.join(files.root, CANCEL)))
                os.rename(temporary, os.path.join(files.root, CANCEL))
                os.fsync(files.directories[-1][1])
                files.open_file(CANCEL, 128)
                demand(files.read(CANCEL) == raw)
            files.check()
        return {'requested': True, 'jobSha256': plan_sha256}
    except Exception:
        fail()
    finally:
        if fd is not None:
            os.close(fd)
        if files is not None:
            files.close()
