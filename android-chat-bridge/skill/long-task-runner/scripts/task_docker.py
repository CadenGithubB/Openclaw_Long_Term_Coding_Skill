"""Exact pre-enrolled Docker workers for operator-pinned checkpoint tasks.

No discovery, creation, deletion, native agent or model authority. The caller
durably reserves work before execute; an uncertain execution is never replayed.
"""
import base64
import datetime
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import tempfile
import threading
import time
from types import MappingProxyType

IMAGE = 'sha256:2fe369e969550cde8e867afc3fe370b260140cab4a23d467074295b42163d553'
LABEL = 'org.openclaw.devlab.task'
CMD = ['/bin/sh', '-c', 'exec sleep 180']
TMPFS = {'/work': 'rw,nosuid,nodev,noexec,size=4194304,uid=1000,gid=1000,mode=0700',
         '/tmp': 'rw,nosuid,nodev,noexec,size=16777216,mode=1777'}
ZERO_TIME = '0001-01-01T00:00:00Z'
MAX_ARTIFACT = 8192

# This fixed reviewed program is passed only to Node inside an exact worker.
# Its subprocess is confined to that worker; the host always stops the worker.
WORKER_PROGRAM = r'''
'use strict';
const fs = require('node:fs'), crypto = require('node:crypto'), {spawn} = require('node:child_process');
const need = value => { if (!value) throw new Error('worker-protocol-refused'); };
const hash = raw => crypto.createHash('sha256').update(raw).digest('hex');
const target = '/work/artifact', NOFOLLOW = fs.constants.O_NOFOLLOW;
let child, failed = false;
function killOwnedGroup() {
  if (child && Number.isInteger(child.pid) && child.pid > 0) {
    try { process.kill(-child.pid, 'SIGKILL'); } catch {}
  }
}
function failure() {
  if (failed) return;
  failed = true; killOwnedGroup();
  process.stderr.write('worker-protocol-refused\n');
  process.exitCode = 1;
  setTimeout(() => process.exit(1), 250).unref();
}
async function run(raw) {
  const data = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(raw));
  need(data && typeof data === 'object' && !Array.isArray(data));
  need(Object.keys(data).sort().join(',') === 'argv,artifactBase64,kind');
  const {argv, artifactBase64, kind} = data;
  need(Array.isArray(argv) && argv.length >= 1 && argv.length <= 32);
  need(argv.every(v => typeof v === 'string' && v.length > 0 && v.length <= 12000 && !v.includes('\x00')));
  need(kind === 'step' || kind === 'verify');
  need(typeof artifactBase64 === 'string' && artifactBase64.length <= 10924);
  const source = Buffer.from(artifactBase64, 'base64');
  need(source.length <= 8192 && source.toString('base64') === artifactBase64);
  // Only the canonical trusted-host request form is accepted (including key order).
  need(JSON.stringify({argv, artifactBase64, kind}) === raw.toString('utf8'));
  let fd = fs.openSync(target, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | NOFOLLOW, 0o600);
  try { fs.writeFileSync(fd, source); } finally { fs.closeSync(fd); }
  const result = await new Promise((resolve, reject) => {
    let settled = false, total = 0, timer;
    const output = [[], []];
    const stop = () => {
      if (settled) return;
      settled = true; clearTimeout(timer); killOwnedGroup(); reject(new Error('worker-protocol-refused'));
    };
    child = spawn(argv[0], argv.slice(1), {cwd: '/work', env: {PATH: '/usr/local/bin:/usr/bin:/bin'},
      stdio: ['ignore', 'pipe', 'pipe'], detached: true});
    for (const [i, stream] of [child.stdout, child.stderr].entries()) {
      stream.on('data', chunk => {
        if (settled) return;
        total += chunk.length;
        if (total > 8192) return stop();
        output[i].push(Buffer.from(chunk));
      });
      stream.on('error', stop);
    }
    child.on('error', stop);
    child.on('close', (code, signal) => {
      if (settled) return;
      if (signal !== null || !Number.isInteger(code) || code < 0 || code > 255) return stop();
      settled = true; clearTimeout(timer);
      resolve({code, stdout: Buffer.concat(output[0]), stderr: Buffer.concat(output[1])});
    });
    timer = setTimeout(stop, 5000);
  });
  const named = fs.lstatSync(target);
  need(named.isFile() && named.nlink === 1 && named.size <= 8192);
  fd = fs.openSync(target, fs.constants.O_RDONLY | NOFOLLOW);
  let artifact;
  try {
    const opened = fs.fstatSync(fd);
    need(opened.isFile() && opened.nlink === 1 && opened.dev === named.dev && opened.ino === named.ino);
    const bytes = Buffer.alloc(8193); let used = 0;
    while (used < bytes.length) {
      const n = fs.readSync(fd, bytes, used, bytes.length - used, null);
      if (!n) break;
      used += n;
    }
    need(used <= 8192); artifact = bytes.subarray(0, used);
  } finally { fs.closeSync(fd); }
  need(kind !== 'verify' || artifact.equals(source));
  process.stdout.write(JSON.stringify({artifactBase64: artifact.toString('base64'),
    stdoutBase64: result.stdout.toString('base64'), stderrBase64: result.stderr.toString('base64'),
    code: result.code, inputSha256: hash(source), artifactSha256: hash(artifact)}) + '\n');
}
const chunks = []; let size = 0;
process.stdin.on('data', chunk => {
  if (failed) return;
  size += chunk.length;
  if (size > 32768) { process.stdin.destroy(); failure(); return; }
  chunks.push(Buffer.from(chunk));
});
process.stdin.on('error', failure);
process.stdin.on('end', () => { if (!failed) run(Buffer.concat(chunks)).catch(failure); });
'''


def fail():
    raise ValueError('task-docker-unconfirmed')


def exact(value, keys):
    if type(value) is not dict or set(value) != set(keys): fail()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def freeze(value):
    if type(value) is dict: return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if type(value) is list: return tuple(freeze(v) for v in value)
    return value


def ishex(value, length=64):
    return type(value) is str and re.fullmatch('[a-f0-9]{%d}' % length, value) is not None


def parse(raw):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out: fail()
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: fail())


def timestamp(value):
    match = type(value) is str and re.fullmatch(r'(20\d\d-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z', value)
    if not match: fail()
    stamp = datetime.datetime.strptime(match[1], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=datetime.timezone.utc)
    return int(stamp.timestamp()) * 1000000000 + int((match[2] or '').ljust(9, '0'))


def worker_fingerprint(info):
    projection = {key: info[key] for key in ('Id', 'Name', 'Image', 'Created', 'RestartCount', 'Config', 'HostConfig', 'Mounts')}
    host = projection['HostConfig']
    if host.get('OomKillDisable') not in (None, False): fail()
    projection['HostConfig'] = dict(host, OomKillDisable=False)
    return digest(canonical(projection))


def _identity(meta):
    return (meta.st_dev, meta.st_ino, meta.st_uid, stat.S_IMODE(meta.st_mode))


def _binary_identity(meta):
    return _identity(meta) + (meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)


def _decoded(value, limit):
    if type(value) is not str or len(value) > (limit + 2) // 3 * 4: fail()
    raw = base64.b64decode(value, validate=True)
    if len(raw) > limit or base64.b64encode(raw).decode() != value: fail()
    return raw


class TaskDocker:
    def __init__(self, runtime, workers, config_parent=None):
        # JSON round-trip detaches all caller-owned containers.
        self.runtime, self.workers = parse(canonical(runtime)), parse(canonical(workers))
        r = self.runtime
        exact(r, ['docker', 'dockerFile', 'daemonId', 'socket', 'imageId'])
        exact(r['docker'], ['path', 'realpath', 'sha256'])
        exact(r['dockerFile'], ['dev', 'ino', 'uid', 'mode', 'size'])
        exact(r['socket'], ['path', 'dev', 'ino', 'uid', 'mode'])
        binary_pin = r['dockerFile']
        if (any(type(binary_pin[k]) is not int or binary_pin[k] < 0 for k in binary_pin)
                or binary_pin['ino'] == 0 or not 0 < binary_pin['size'] <= 512 * 1024 * 1024
                or not 0 <= binary_pin['mode'] <= 0o777 or not binary_pin['mode'] & 0o111
                or binary_pin['mode'] & 0o022): fail()
        if r['imageId'] != IMAGE or type(r['daemonId']) is not str or not re.fullmatch('[A-Za-z0-9:-]{1,128}', r['daemonId']): fail()
        if not ishex(r['docker']['sha256']): fail()
        for p in (r['docker']['path'], r['docker']['realpath'], r['socket']['path']):
            if type(p) is not str or not os.path.isabs(p) or os.path.normpath(p) != p or re.search(r'[\x00-\x20\x7f]', p): fail()
        s = r['socket']
        if len(s['path'].encode()) > 100 or any(type(s[k]) is not int for k in ('dev', 'ino', 'uid', 'mode')) or s['uid'] != os.getuid() or s['mode'] & 0o022: fail()
        if type(self.workers) is not list or not 2 <= len(self.workers) <= 9: fail()
        seen = set()
        for w in self.workers:
            exact(w, ['id', 'name', 'createdAt', 'startedAt', 'token', 'fingerprint'])
            if not ishex(w['id']) or not ishex(w['token'], 32) or not ishex(w['fingerprint']) or w['name'] != '/devlab-task-' + w['token'] or w['startedAt'] != ZERO_TIME or w['id'] in seen: fail()
            timestamp(w['createdAt']); seen.add(w['id'])
        if len({w['name'] for w in self.workers}) != len(self.workers): fail()
        self.runtime, self.workers = freeze(self.runtime), freeze(self.workers)
        self._closed = False; self._active = None; self._busy = threading.Lock()
        self._spent = set(); self._starts = {}; self._pins = []; self._config = None
        self._cancelled = lambda: False; self._cleaning = False
        self._counts = {'getRequests': 0, 'startRequests': 0, 'execRequests': 0, 'stopRequests': 0}
        try:
            if os.path.realpath(r['docker']['path']) != r['docker']['realpath']: fail()
            fd = os.open(r['docker']['realpath'], os.O_RDONLY | os.O_NOFOLLOW)
            try:
                meta = os.fstat(fd)
                # The operator pins the installed application owner explicitly.
                # macOS applications can belong to a different administrator;
                # never infer an allowed owner from the current filesystem.
                if (not stat.S_ISREG(meta.st_mode) or not meta.st_mode & 0o111 or meta.st_mode & 0o022
                        or _identity(meta) + (meta.st_size,) != tuple(binary_pin[k] for k in ('dev', 'ino', 'uid', 'mode', 'size'))): fail()
                h = hashlib.sha256()
                while True:
                    chunk = os.read(fd, 1048576)
                    if not chunk: break
                    h.update(chunk)
                if h.hexdigest() != r['docker']['sha256'] or _binary_identity(os.fstat(fd)) != _binary_identity(meta): fail()
                self._binary = _binary_identity(meta)
            finally: os.close(fd)
            path = '/'
            for part in [''] + [v for v in os.path.dirname(s['path']).split('/') if v]:
                if part: path = os.path.join(path, part)
                fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                meta = os.fstat(fd); self._pins.append((fd, path, _identity(meta)))
                if meta.st_uid not in (0, os.getuid()) or (meta.st_mode & 0o022 and not (meta.st_uid == 0 and meta.st_mode & stat.S_ISVTX)): fail()
            self._config = tempfile.mkdtemp(prefix='devlab-docker-config-', dir=config_parent)
            self._check()
        except Exception:
            self.close(); raise

    def _check(self):
        if self._closed: fail()
        for fd, path, expected in self._pins:
            if _identity(os.fstat(fd)) != expected or _identity(os.lstat(path)) != expected or not stat.S_ISDIR(os.lstat(path).st_mode): fail()
        d = self.runtime['docker']; s = self.runtime['socket']
        if os.path.realpath(d['path']) != d['realpath'] or _binary_identity(os.stat(d['realpath'], follow_symlinks=False)) != self._binary: fail()
        meta = os.lstat(s['path'])
        if not stat.S_ISSOCK(meta.st_mode) or _identity(meta) != tuple(s[k] for k in ('dev', 'ino', 'uid', 'mode')): fail()

    def _run(self, args, payload=None, timeout=3, limit=131072, cancelled=None):
        self._check()
        cancelled = cancelled if cancelled is not None else (lambda: False) if self._cleaning else self._cancelled
        if cancelled(): fail()
        argv = [self.runtime['docker']['realpath'], '--config', self._config, '--host', 'unix://' + self.runtime['socket']['path']] + args
        env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'DOCKER_API_VERSION': '1.47', 'LANG': 'C', 'LC_ALL': 'C'}
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, close_fds=True)
        counter = {'info': 'getRequests', 'container': 'getRequests', 'start': 'startRequests',
                   'exec': 'execRequests', 'stop': 'stopRequests'}.get(args[0])
        if counter: self._counts[counter] += 1
        self._active = proc; sel = selectors.DefaultSelector(); output = [bytearray(), bytearray()]
        deadline = time.monotonic() + timeout
        sel.register(proc.stdout, selectors.EVENT_READ, 0); sel.register(proc.stderr, selectors.EVENT_READ, 1)
        try:
            pending = memoryview(payload or b'')
            if payload is not None:
                os.set_blocking(proc.stdin.fileno(), False)
                if pending: sel.register(proc.stdin, selectors.EVENT_WRITE, 2)
                else: proc.stdin.close()
            while sel.get_map():
                if self._closed or cancelled() or time.monotonic() >= deadline: fail()
                for key, _ in sel.select(min(.05, max(0, deadline - time.monotonic()))):
                    if key.data == 2:
                        try: pending = pending[os.write(proc.stdin.fileno(), pending[:4096]):]
                        except BrokenPipeError: pending = pending[:0]
                        if not pending: sel.unregister(proc.stdin); proc.stdin.close()
                    else:
                        part = os.read(key.fileobj.fileno(), min(65536, limit + 1))
                        if not part: sel.unregister(key.fileobj); continue
                        output[key.data].extend(part)
                        if sum(map(len, output)) > limit: fail()
            code = proc.wait(timeout=max(.001, deadline - time.monotonic()))
            self._check()
            if cancelled(): fail()
            if code != 0 or output[1]: fail()
            return bytes(output[0])
        finally:
            sel.close()
            if proc.poll() is None: proc.kill()
            proc.wait(timeout=2)
            proc.stdout.close(); proc.stderr.close()
            if proc.stdin is not None and not proc.stdin.closed: proc.stdin.close()
            self._active = None

    def _info(self):
        info = parse(self._run(['info', '--format', '{{json .}}']))
        if info.get('ID') != self.runtime['daemonId'] or info.get('OSType') != 'linux': fail()

    def _inspect(self, index):
        data = parse(self._run(['container', 'inspect', self.workers[index]['id']]))
        if type(data) is not list or len(data) != 1: fail()
        info = data[0]; w = self.workers[index]
        if info.get('Id') != w['id'] or info.get('Name') != w['name'] or info.get('Created') != w['createdAt'] or info.get('Image') != IMAGE or info.get('RestartCount') != 0 or worker_fingerprint(info) != w['fingerprint']: fail()
        h, c, state = info['HostConfig'], info['Config'], info['State']
        required = {'Privileged': False, 'ReadonlyRootfs': True, 'NetworkMode': 'none', 'Memory': 268435456,
                    'MemorySwap': 268435456, 'NanoCpus': 1000000000, 'PidsLimit': 32}
        if any(type(h.get(k)) is not type(v) or h.get(k) != v for k, v in required.items()): fail()
        if c.get('User') != '1000:1000' or c.get('Cmd') != CMD or c.get('Entrypoint') not in (None, []) or c.get('Labels', {}).get(LABEL) != w['token']: fail()
        if set(h.get('CapDrop') or []) != {'ALL'} or h.get('Tmpfs') != TMPFS or h.get('RestartPolicy') != {'Name': 'no', 'MaximumRetryCount': 0} or h.get('LogConfig') != {'Type': 'none', 'Config': {}}: fail()
        if h.get('Init') not in (None, False) or h.get('AutoRemove') is not False or info.get('Mounts') != []: fail()
        if any(h.get(k) for k in ('Binds', 'Mounts', 'CapAdd', 'Devices', 'DeviceRequests', 'PortBindings', 'VolumesFrom', 'Links', 'Dns', 'ExtraHosts', 'PidMode', 'UTSMode', 'CgroupParent', 'GroupAdd')): fail()
        if h.get('IpcMode') not in ('private', '') or h.get('UsernsMode') not in ('', None) or h.get('CgroupnsMode') not in ('private', '', None): fail()
        security = h.get('SecurityOpt') or []
        if security not in (['no-new-privileges'], ['no-new-privileges:true']): fail()
        if c.get('Volumes') or c.get('ExposedPorts') or c.get('Tty') or c.get('OpenStdin'): fail()
        if any(state.get(k) is not False for k in ('Paused', 'Restarting', 'OOMKilled', 'Dead')) or state.get('Error') != '': fail()
        status = state.get('Status'); started = state.get('StartedAt')
        if status == 'created':
            if state.get('Running') is not False or state.get('Pid') != 0 or started != ZERO_TIME: fail()
        elif status in ('running', 'exited'):
            if timestamp(started) < timestamp(w['createdAt']) or timestamp(started) > time.time_ns() + 1000000000: fail()
            if index in self._starts and self._starts[index] != started: fail()
            if status == 'running':
                if state.get('Running') is not True or type(state.get('Pid')) is not int or state['Pid'] <= 0: fail()
            elif state.get('Running') is not False or state.get('Pid') != 0 or timestamp(state.get('FinishedAt')) < timestamp(started) or timestamp(state['FinishedAt']) > time.time_ns() + 1000000000: fail()
        else: fail()
        return {'state': status, 'startedAt': started, 'finishedAt': state.get('FinishedAt')}

    def _snapshot(self, index):
        self._check(); self._info(); first = self._inspect(index)
        second = self._inspect(index); self._info(); self._check()
        if first != second: fail()
        return first

    def _index(self, index):
        if type(index) is not int or not 0 <= index < len(self.workers): fail()

    def validate_pool(self, completed_steps, pending, cancelled=False):
        if type(completed_steps) is not int or not 0 <= completed_steps < len(self.workers) or type(cancelled) is not bool: fail()
        if pending is not None:
            index = pending if type(pending) is int else pending.get('index') if type(pending) is dict else None
            self._index(index)
        else: index = None
        if not self._busy.acquire(False): fail()
        try:
            result = []
            for i in range(len(self.workers)):
                observed = self._snapshot(i)
                if i < completed_steps and observed['state'] != 'exited': fail()
                if i >= completed_steps and i != index and observed['state'] != 'created': fail()
                result.append(observed)
            return result
        finally: self._busy.release()

    def _cleanup(self, index):
        observed = self._snapshot(index)
        if observed['state'] == 'running':
            self._starts.setdefault(index, observed['startedAt'])
            try:
                self._run(['stop', '--time', '2', self.workers[index]['id']], timeout=4, limit=1024)
            except Exception:
                # A lost CLI acknowledgement neither proves success nor permits
                # replay. Fresh exact inspection below is the only stop proof.
                pass
        final = self._snapshot(index)
        if final['state'] not in ('created', 'exited'): fail()
        return {'workerId': self.workers[index]['id'], 'stopped': True, 'state': final['state'], 'startedAt': final['startedAt'], 'clientJoined': True}

    def cleanup(self, index):
        self._index(index)
        if not self._busy.acquire(False): fail()
        try:
            self._cleaning = True
            return self._cleanup(index)
        finally:
            self._cleaning = False; self._busy.release()

    def execute(self, index, argv, artifact_bytes, kind, cancelled=lambda: False):
        self._index(index)
        if type(argv) is not list or not 1 <= len(argv) <= 32 or any(type(v) is not str or not 0 < len(v) <= 12000 or '\x00' in v for v in argv): fail()
        if type(artifact_bytes) is not bytes or len(artifact_bytes) > MAX_ARTIFACT or kind not in ('step', 'verify') or not callable(cancelled): fail()
        if (kind == 'verify') != (index == len(self.workers) - 1): fail()
        payload = canonical({'argv': argv, 'artifactBase64': base64.b64encode(artifact_bytes).decode(), 'kind': kind})
        if len(payload) > 32768 or index in self._spent or not self._busy.acquire(False): fail()
        self._spent.add(index)
        self._cancelled = cancelled
        try:
            if cancelled() or self._snapshot(index)['state'] != 'created': fail()
            if cancelled(): fail()
            self._run(['start', self.workers[index]['id']], limit=1024, cancelled=cancelled)
            running = self._snapshot(index)
            if running['state'] != 'running' or cancelled(): fail()
            self._starts[index] = running['startedAt']
            raw = self._run(['exec', '-i', '--user', '1000:1000', '--workdir', '/work', self.workers[index]['id'],
                             '/usr/local/bin/node', '--max-old-space-size=64', '-e', WORKER_PROGRAM], payload=payload, timeout=7, limit=32768, cancelled=cancelled)
            result = parse(raw)
            exact(result, ['artifactBase64', 'stdoutBase64', 'stderrBase64', 'code', 'inputSha256', 'artifactSha256'])
            artifact = _decoded(result['artifactBase64'], MAX_ARTIFACT); stdout = _decoded(result['stdoutBase64'], 8192); stderr = _decoded(result['stderrBase64'], 8192)
            if len(stdout) + len(stderr) > 8192 or type(result['code']) is not int or not -255 <= result['code'] <= 255 or result['inputSha256'] != digest(artifact_bytes) or result['artifactSha256'] != digest(artifact) or (kind == 'verify' and artifact != artifact_bytes) or cancelled(): fail()
            answer = {'artifactBytes': artifact, 'stdoutBytes': stdout, 'code': result['code'], 'workerId': self.workers[index]['id'],
                      'inputSha256': digest(artifact_bytes), 'artifactSha256': digest(artifact), 'kind': kind, 'index': index, 'stopped': True}
        finally:
            try:
                self._cleaning = True
                self._cleanup(index)
            finally:
                self._cleaning = False; self._cancelled = lambda: False; self._busy.release()
        if cancelled(): fail()
        return answer

    def status(self):
        return dict(self._counts, clientJoined=self._active is None, closed=self._closed)

    def close(self):
        self._closed = True
        if self._active is not None and self._active.poll() is None: self._active.kill()
        for fd, _, _ in self._pins:
            os.close(fd)
        self._pins = []
        if self._config is not None:
            os.rmdir(self._config); self._config = None
