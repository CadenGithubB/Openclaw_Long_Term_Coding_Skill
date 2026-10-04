#!/usr/bin/python3
"""Session-bound Android operations. No model-supplied host commands or paths."""
from pathlib import Path
import base64
import copy
import fcntl
import hashlib
import io
import json
import math
import os
import plistlib
import re
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import zipfile

ROOT = Path('/CONFIGURE/android-chat-bridge')
VOLUME = Path('/CONFIGURE/project-volume')
VOLUME_UUID = '00000000-0000-0000-0000-000000000000'
MARKER = 'CONFIGURE_VOLUME_MARKER'
IMAGE = 'sha256:f2b3b185ef6204a9729a82b338936d352fa3cbffadfebb629c4e70da1996f707'
DOCKER = '/Applications/Docker.app/Contents/Resources/bin/docker'
MODEL = 'qwen3.6:35b'
MODEL_SHA = '07d35212591fc27746f0a317c975a6d68754fb38e9053d82e25f06057af28522'
PACKAGE = 'org.openclaw.trial'
CONFIG = Path('/CONFIGURE/openclaw.json')
SOCKET = ROOT / 'control.sock'
ENV = {'HOME': '/CONFIGURE/service-home', 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
       'DOCKER_HOST': 'unix:///CONFIGURE/docker.sock'}
MAX_REQUEST = 600 * 1024
MAX_RESPONSE = 5 * 1024 * 1024
DEFAULT_TIME_LIMIT_MINUTES = 30
MIN_TIME_LIMIT_MINUTES = 5
MAX_TIME_LIMIT_MINUTES = 60
MAX_REPORT_BYTES = 128 * 1024
JAVA_NAME = re.compile(r'[A-Z][A-Za-z0-9_]{0,63}\.java\Z')
JOB_NAME = re.compile(r'am-[a-f0-9]{32}\Z')
TERMINAL = {'stopped', 'expired', 'failed', 'cleanup-uncertain'}
OPERATOR_UID = 502
RECONCILIATION_PURPOSE = 'Permit a new session after operator reconciliation; preserve the original uncertain job.'

SCAFFOLD = {
    'build.gradle': "plugins { id 'com.android.application' version '8.13.2' apply false }\n",
    'settings.gradle': "pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }\ndependencyResolutionManagement { repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS); repositories { google(); mavenCentral() } }\nrootProject.name = 'AndroidChatTrial'\ninclude ':app'\n",
    'gradle.properties': 'org.gradle.jvmargs=-Xmx1024m -Dfile.encoding=UTF-8\norg.gradle.workers.max=2\norg.gradle.daemon=false\norg.gradle.java.installations.auto-download=false\nandroid.builder.sdkDownload=false\n',
    'app/build.gradle': "plugins { id 'com.android.application' }\nandroid { namespace 'org.openclaw.trial'; compileSdk 35; buildToolsVersion '35.0.0'; defaultConfig { applicationId 'org.openclaw.trial'; minSdk 23; targetSdk 35; versionCode 1; versionName '1.0' } }\n",
    'app/src/main/AndroidManifest.xml': '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application android:label="Android Chat Trial" android:theme="@android:style/Theme.Material.Light.NoActionBar" android:allowBackup="false"><activity android:name=".MainActivity" android:exported="true" android:screenOrientation="portrait"><intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter></activity></application></manifest>\n',
}

class Refused(RuntimeError):
    pass

class InputRejected(Refused):
    """Refused before any input reached the guest; the running test is unchanged."""
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details

def require(condition, message):
    if not condition:
        raise Refused(message)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def validate_time_limit(value):
    require(type(value) is int and MIN_TIME_LIMIT_MINUTES <= value <= MAX_TIME_LIMIT_MINUTES,
            'timeLimitMinutes must be an integer from 5 to 60, chosen only for a new job')
    return value

def recorded_time_limit(state):
    # deadlineSeconds predates the configurable limit; never substitute today's
    # default into historical evidence or reconstruct an old monotonic deadline.
    seconds = state.get('deadlineSeconds')
    return seconds // 60 if type(seconds) is int and seconds > 0 and seconds % 60 == 0 else None

def cancellation_path(actor):
    return ROOT / 'cancelled-sessions' / (digest(actor.encode()) + '.json')

def cancel_actor(actor):
    path = cancellation_path(actor)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic(path, {'actor': actor, 'cancelledAt': time.time()})

def evidence_bytes(directory):
    total = 0
    for path in directory.rglob('*'):
        if path.name.endswith('.new') or any(part.startswith('emulator-') for part in path.relative_to(directory).parts):
            continue
        try:
            value = path.lstat()
        except FileNotFoundError:
            continue  # Atomic receipt replacement raced this inventory.
        if stat.S_ISREG(value.st_mode):
            total += value.st_size
    return total

def atomic(path, value):
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.new')
    with tmp.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(tmp, 0o600)
    tmp.replace(path)

def safe_regular(path, root, maximum):
    require('..' not in path.parts and path.is_absolute() and root.is_absolute(), 'noncanonical retained path')
    require(path.is_relative_to(root) if hasattr(path, 'is_relative_to') else str(path).startswith(str(root) + '/'), 'path outside retained job')
    for parent in [path, *path.parents]:
        require(not parent.is_symlink(), 'symlink refused')
        if parent == root:
            break
    value = path.lstat()
    require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and 0 <= value.st_size <= maximum, 'invalid retained file')
    return path

def open_directory_nofollow(path):
    """Pin every component; Path.resolve alone cannot protect a deletion."""
    require(path.is_absolute() and '..' not in path.parts, 'noncanonical directory')
    required = (os.open, os.stat, os.unlink, os.rmdir)
    require(all(fn in os.supports_dir_fd for fn in required) and os.scandir in os.supports_fd,
            'descriptor-relative cache retirement is unavailable')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise

def cache_tree(fd, device, deadline, remove=False, budget=None, depth=0):
    """Count or unlink only descriptor-relative cache entries, never link targets."""
    budget = {'entries': 0} if budget is None else budget
    require(depth <= 64, 'cache directory depth limit')
    total = 0
    with os.scandir(fd) as entries:
        for entry in entries:
            require(time.monotonic() < deadline, 'cache retirement time limit')
            budget['entries'] += 1
            require(budget['entries'] <= 100000, 'cache entry limit')
            before = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
            require(before.st_dev == device, 'cache crosses a filesystem boundary')
            if stat.S_ISDIR(before.st_mode):
                child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino), 'cache directory identity changed')
                    total += cache_tree(child, device, deadline, remove, budget, depth + 1)
                    if remove:
                        current = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                        require((current.st_dev, current.st_ino) == (before.st_dev, before.st_ino), 'cache directory replaced during retirement')
                        os.rmdir(entry.name, dir_fd=fd)
                finally:
                    os.close(child)
            else:
                require(stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode), 'unexpected special cache entry')
                total += before.st_size
                if remove:
                    os.unlink(entry.name, dir_fd=fd)
    return total

def with_report(job_dir, result, include_text=False):
    """Expose only the fixed report of an already-authorized retained job."""
    result = dict(result)
    path = job_dir / 'report.md'
    result['reportPath'] = str(path)
    if not include_text:
        return result
    try:
        safe_regular(path, job_dir, MAX_REPORT_BYTES)
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            value = os.fstat(stream.fileno())
            require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and 0 <= value.st_size <= MAX_REPORT_BYTES, 'invalid retained report')
            data = stream.read(MAX_REPORT_BYTES + 1)
        require(len(data) <= MAX_REPORT_BYTES, 'retained report byte limit')
        result.update(reportStatus='available', reportText=data.decode('utf-8'), reportSha256=digest(data))
    except (OSError, Refused, UnicodeError) as error:
        result.update(reportStatus='unavailable', reportError=str(error)[:300])
    return result

def with_progress(state, result, deadline=None):
    """Describe recorded progress, without granting more time or qualification."""
    def count(key):
        value = state.get(key)
        return value if type(value) is int and value >= 0 else None

    revision = count('sourceRevision')
    apk = state.get('apk')
    qualified = (revision is not None and revision > 0 and isinstance(apk, dict)
                 and type(apk.get('sourceRevision')) is int and apk['sourceRevision'] == revision
                 and all(isinstance(apk.get(key), dict) and type(apk[key].get('exitCode')) is int
                         and apk[key]['exitCode'] == 0 for key in ('build', 'signature', 'packageCheck')))
    ended = state.get('status') in TERMINAL
    seconds = 0 if ended else (max(0, int(deadline - time.monotonic())) if deadline is not None else None)
    progress = {'sourceRevision': revision, 'qualifiedApkSourceRevision': revision if qualified else None}
    for key, limit in (('writes', 8), ('builds', 3), ('actions', 40)):
        used = count(key)
        progress[key + 'Remaining'] = max(0, limit - used) if used is not None else None
    progress['controllerSecondsRemaining'] = seconds
    progress['controllerTimeLimitMinutes'] = recorded_time_limit(state)
    if revision is None:
        source = 'The source revision is unknown; no current qualified APK is established.'
    elif revision == 0:
        source = 'No app source revision or qualified APK is recorded yet.'
    elif qualified:
        source = 'Source revision %d has a recorded qualified APK; this alone does not verify Android behavior.' % revision
    else:
        source = 'Source revision %d has no current qualified APK; a successful build is still required.' % revision
    def amount(key):
        value = progress[key + 'Remaining']
        return str(value) if value is not None else 'unknown'
    limits = 'Unused limits: %s source writes, %s builds and %s actions.' % (amount('writes'), amount('builds'), amount('actions'))
    duration = progress['controllerTimeLimitMinutes']
    selected = ('The recorded controller time limit is %d minutes, fixed at preparation.' % duration
                if duration is not None else 'The recorded controller time limit is unknown.')
    if ended:
        clock = 'This ended job has no controller work time left and cannot continue; that does not confirm cleanup.'
    else:
        clock = ('Controller work time remaining: %s seconds; this is separate from the chat deadline, and a chat timeout does not confirm cleanup.'
                 % (seconds if seconds is not None else 'unknown'))
    result = dict(result)
    result.update(status=state.get('status'), progress=progress)
    result['progressSummary'] = source + ' ' + limits + ' ' + selected + ' ' + clock
    return result

def validate_params(params):
    require(isinstance(params, dict), 'parameters must be an object')
    action = params.get('action')
    common = {'action', 'jobId', 'reason'}
    extra = {'prepare': {'timeLimitMinutes'}, 'status': set(), 'stop': set(), 'write_sources': {'files'},
             'build': set(), 'start_test': set(), 'observe': set(),
             'tap': {'x', 'y', 'count', 'intervalMs'}}
    require(action in extra, 'unknown Android action')
    require(set(params) <= common | extra[action], 'unknown parameters refused')
    if 'jobId' in params:
        require(isinstance(params['jobId'], str) and JOB_NAME.fullmatch(params['jobId']), 'invalid job ID')
    require(action != 'prepare' or 'jobId' not in params, 'prepare does not accept a job ID')
    if 'timeLimitMinutes' in params:
        validate_time_limit(params['timeLimitMinutes'])
    require(isinstance(params.get('reason', ''), str) and len(params.get('reason', '')) <= 1000, 'reason must be short text')
    if action == 'write_sources':
        files = params.get('files')
        require(isinstance(files, list) and 1 <= len(files) <= 16, 'provide 1 to 16 Java source files')
        seen, total = set(), 0
        for item in files:
            require(isinstance(item, dict) and set(item) == {'name', 'content'}, 'source accepts only name and content')
            name, content = item['name'], item['content']
            require(isinstance(name, str) and JAVA_NAME.fullmatch(name) and name not in seen, 'source name must be a unique Java class filename')
            require(isinstance(content, str) and '\x00' not in content, 'source must be UTF-8 text')
            size = len(content.encode('utf-8'))
            require(0 < size <= 128 * 1024, 'source size limit')
            total += size
            seen.add(name)
        require(total <= 512 * 1024, 'source batch size limit')
    if action == 'tap':
        for key in ('x', 'y'):
            require(type(params.get(key)) is int and 0 <= params[key] <= 8192, 'tap coordinate must be a bounded integer')
        count, interval = params.get('count', 1), params.get('intervalMs', 300)
        require(type(count) is int and 1 <= count <= 30, 'tap count limit')
        require(type(interval) is int and 80 <= interval <= 1500, 'tap interval limit')
        require((count - 1) * interval <= 15000, 'tap burst duration limit')
    return params

def validate_request(request):
    require(isinstance(request, dict) and set(request) == {'actor', 'requestId', 'params'}, 'invalid controller request')
    actor = request['actor']
    require(isinstance(actor, str) and actor.startswith('agent:main:') and '|' in actor and len(actor) <= 768, 'trusted main-session identity required')
    params = validate_params(request['params'])
    require(isinstance(request['requestId'], str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}', request['requestId']), 'invalid request identity')
    return actor, params

def retained_job(actor, job_id=None):
    """Read a terminal record without constructing, adopting or updating a Job."""
    if job_id is not None:
        require(isinstance(job_id, str) and JOB_NAME.fullmatch(job_id), 'invalid job ID')
        paths = [ROOT / 'jobs' / job_id / 'state.json']
    else:
        paths = sorted((ROOT / 'jobs').glob('*/state.json'))
    owned = []
    for path in paths:
        require(JOB_NAME.fullmatch(path.parent.name), 'invalid retained job directory')
        try:
            safe_regular(path, ROOT, MAX_RESPONSE)
        except FileNotFoundError:
            require(job_id is None, 'job not owned by this session')
            continue
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            value = os.fstat(stream.fileno())
            require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and 0 <= value.st_size <= MAX_RESPONSE, 'invalid retained state')
            data = stream.read(MAX_RESPONSE + 1)
        require(len(data) <= MAX_RESPONSE, 'retained state byte limit')
        state = json.loads(data)
        require(isinstance(state, dict) and state.get('jobId') == path.parent.name, 'retained job identity differs')
        if state.get('actor') != actor:
            require(job_id is None, 'job not owned by this session')
            continue
        created = state.get('createdAt')
        require(type(created) in (int, float) and math.isfinite(created) and created >= 0, 'invalid retained creation time')
        require(state.get('status') in TERMINAL, 'retained job is unfinished; operator reconciliation is required, and no job was restarted')
        owned.append((path.parent, state))
    if not owned:
        return None
    return max(owned, key=lambda item: (item[1]['createdAt'], item[0].name))

def retained_receipt(retained, action):
    """Serve recorded evidence only; do not infer cleanup from terminal status."""
    require(action in ('status', 'stop', 'prepare'), 'retained jobs cannot perform actions')
    directory, state = retained
    if action == 'prepare':
        return with_report(directory, with_progress(state, {'ok': False, 'jobId': state['jobId'], 'retryAllowed': False,
            'summary': 'This Android job has ended and cannot be restarted in this session. Use status to inspect its retained report; its limits have not been reset.',
            'status': state['status']}))
    cleanup = state.get('cleanup')
    cache = cleanup.get('cache') if isinstance(cleanup, dict) else None
    worker = cleanup.get('worker') if isinstance(cleanup, dict) else None
    emulator = cleanup.get('emulator') if isinstance(cleanup, dict) else None
    complete = (state['status'] != 'cleanup-uncertain' and isinstance(cleanup, dict)
                and cleanup.get('stopped') is True and cleanup.get('cleanupComplete') is True
                and cleanup.get('errors') == [] and isinstance(cache, dict) and cache.get('complete') is True
                and isinstance(worker, dict) and worker.get('running') is False
                and ('emulator' not in cleanup or (isinstance(emulator, dict) and emulator.get('status') == 'stopped'
                     and not emulator.get('errors') and not emulator.get('survivingProcessGroups'))))
    result = {'ok': complete, 'jobId': state['jobId'], 'retained': True,
              'summary': ("This ended job's retained receipts confirm cleanup completed. Its evidence remains available; no job was restarted." if complete
                          else "This ended job's retained receipts do not confirm full cleanup, including its derived cache. Read the report; no job was restarted."),
              'state': {k: v for k, v in state.items() if k not in ('actor', 'events')}}
    if action == 'stop':
        result['cleanup'] = cleanup
    return with_report(directory, with_progress(state, result), include_text=True)

def validate_container(c, work, cid=None, job_id=None):
    h, config = c['HostConfig'], c['Config']
    require(cid is None or c['Id'] == cid, 'container identity changed')
    require(c['Image'] == IMAGE and config['User'] == '1000:1000', 'wrong Android image or user')
    require(h['NetworkMode'] == 'none' and h['ReadonlyRootfs'] and not h['Privileged'], 'container isolation differs')
    require(h['CapDrop'] == ['ALL'] and not h.get('CapAdd'), 'container capabilities differ')
    require(h['Memory'] == h['MemorySwap'] == 2147483648 and h['NanoCpus'] == 2000000000 and h['PidsLimit'] == 128, 'container resource limits differ')
    require(h.get('PidMode', '') == '' and h.get('IpcMode') in ('private', '') and not h.get('Devices'), 'host namespaces/devices refused')
    require(h.get('SecurityOpt') in (['no-new-privileges'], ['no-new-privileges:true']), 'security options differ')
    require(not any(h.get(k) for k in ('UTSMode', 'DeviceRequests', 'VolumesFrom', 'StorageOpt', 'Links', 'ExtraHosts', 'PortBindings')), 'extra host access refused')
    require(h.get('CgroupnsMode', 'private') == 'private', 'cgroup namespace differs')
    mounts = c.get('Mounts', [])
    require(len(mounts) == 1, 'extra worker mount refused')
    m = mounts[0]
    require(m['Type'] == 'bind' and m['Source'] == str(work) and m['Destination'] == '/workspace' and m['RW'] and m.get('Propagation') == 'rprivate', 'wrong worker mount')
    require(h.get('Tmpfs') == {'/tmp':'rw,noexec,nosuid,size=128m', '/run':'rw,noexec,nosuid,size=16m', '/var/tmp':'rw,noexec,nosuid,size=16m'}, 'temporary mount policy differs')
    limits = {x['Name']:(x['Soft'],x['Hard']) for x in h.get('Ulimits') or []}
    require(limits == {'nofile':(1024,1024), 'fsize':(268435456,268435456)}, 'ulimits differ')
    require(h.get('LogConfig') == {'Type':'local', 'Config':{'max-size':'4m','max-file':'1','compress':'false'}}, 'container logging limits differ')
    if job_id:
        require(config.get('Labels', {}).get('org.openclaw.android-job') == job_id, 'job label differs')
    return c

def raw_command(argv, timeout=20):
    p = subprocess.run(argv, env=ENV, capture_output=True, timeout=timeout)
    require(p.returncode == 0, p.stderr.decode(errors='replace')[:2000] or 'operator command failed')
    require(len(p.stdout) <= 4 * 1024 * 1024, 'operator output limit')
    return p.stdout

def read_admission_bytes(path, maximum=MAX_RESPONSE, private=True):
    """Read operator-owned evidence through pinned, no-follow descriptors."""
    require(os.getuid() == os.geteuid() == OPERATOR_UID, 'operator account required for history admission')
    parent = open_directory_nofollow(path.parent)
    try:
        owner = os.fstat(parent)
        require(owner.st_uid == OPERATOR_UID and not owner.st_mode & (0o077 if private else 0o022), 'unsafe operator evidence directory')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            value = os.fstat(stream.fileno())
            require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and value.st_uid == OPERATOR_UID
                    and not value.st_mode & (0o077 if private else 0o022) and 0 <= value.st_size <= maximum, 'unsafe operator evidence file')
            data = stream.read(maximum + 1)
        require(len(data) <= maximum, 'operator evidence byte limit')
        current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        require((current.st_dev, current.st_ino) == (value.st_dev, value.st_ino), 'operator evidence file replaced during read')
        return data
    finally:
        os.close(parent)

def admission_state(path):
    require(path.name == 'state.json' and JOB_NAME.fullmatch(path.parent.name)
            and path == ROOT / 'jobs' / path.parent.name / 'state.json', 'unexpected retained state path')
    data = read_admission_bytes(path)
    try:
        state = json.loads(data)
    except (ValueError, UnicodeError):
        raise Refused('retained state JSON is invalid')
    require(isinstance(state, dict) and state.get('jobId') == path.parent.name, 'retained job identity differs')
    return state, data

def reconciliation_inventory(argv, deadline, allowed=(0,)):
    remaining = deadline - time.monotonic()
    require(remaining > 0, 'history admission observation deadline expired')
    try:
        result = subprocess.run(argv, env=ENV, capture_output=True, timeout=min(3, remaining))
    except (OSError, subprocess.TimeoutExpired):
        raise Refused('history admission inventory unavailable')
    require(len(result.stdout) + len(result.stderr) <= 4 * 1024 * 1024, 'history admission inventory byte limit')
    require(result.returncode in allowed and not result.stderr, 'history admission inventory failed')
    require(time.monotonic() < deadline, 'history admission observation deadline expired')
    return result.stdout

def check_reconciliation_resources(path, expected_sha256, deadline=None):
    """Observe old resources only. This does not reconcile state or own old PIDs."""
    deadline = time.monotonic() + 6 if deadline is None else deadline
    state, original = admission_state(path)
    require(digest(original) == expected_sha256, 'original uncertain state hash differs')
    require(state.get('status') == 'cleanup-uncertain', 'only uncertain terminal history can be reconciled')
    actor = state.get('actor')
    require(isinstance(actor, str) and actor.startswith('agent:main:') and '|' in actor and len(actor) <= 768, 'retained actor identity is invalid')
    cid = state.get('containerId')
    require(isinstance(cid, str) and re.fullmatch(r'[a-f0-9]{64}', cid), 'retained full worker identity required')
    work = VOLUME / 'workspace/project' / ('android-chat-' + state['jobId'][3:])
    require(state.get('workspace') == str(work), 'retained workspace path differs')
    identity = state.get('workspaceIdentity')
    require(isinstance(identity, dict) and set(identity) == {'device', 'inode'}
            and all(type(n) is int and n >= 0 for n in identity.values()), 'retained workspace identity required')
    cleanup = state.get('cleanup')
    require(isinstance(cleanup, dict) and isinstance(cleanup.get('worker'), dict)
            and cleanup['worker'].get('containerId') == cid, 'retained cleanup worker identity differs')
    emulator = cleanup.get('emulator', {})
    require(isinstance(emulator, dict), 'retained emulator cleanup is invalid')
    require(not state.get('emulatorStatus') or bool(emulator), 'retained emulator ownership receipt is missing')
    require(not emulator or (emulator.get('status') in ('stopped', 'cleanup-unconfirmed')
            and 'ownedProcesses' in emulator and 'survivingProcessGroups' in emulator), 'retained emulator ownership inventory is incomplete')
    children, survivors = emulator.get('ownedProcesses', []), emulator.get('survivingProcessGroups', [])
    require(isinstance(children, list) and len(children) <= 16 and isinstance(survivors, list) and len(survivors) <= 128, 'retained process inventory is invalid')
    owned = set()
    for child in children:
        require(isinstance(child, dict) and type(child.get('pid')) is int and child['pid'] > 1, 'retained process identity is invalid')
        owned.add(child['pid'])
    groups = set(owned)
    for line in survivors:
        require(isinstance(line, str) and re.fullmatch(r'\s*[1-9][0-9]*\s+[1-9][0-9]*\s*', line), 'retained process group is invalid')
        group, pid = map(int, line.split())
        require(group in groups, 'retained survivor has no owned process group')
        owned.add(pid)
    volume = plistlib.loads(reconciliation_inventory(['/usr/sbin/diskutil', 'info', '-plist', str(VOLUME)], deadline))
    require(volume.get('VolumeUUID') == VOLUME_UUID and volume.get('MountPoint') == str(VOLUME)
            and volume.get('TotalSize') == 2147442688, 'expected project volume is not mounted for admission')
    fds = [open_directory_nofollow(VOLUME)]
    try:
        device = os.fstat(fds[0]).st_dev
        for name in ('workspace', 'project', work.name):
            fds.append(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fds[-1]))
            require(os.fstat(fds[-1]).st_dev == device, 'workspace crosses a filesystem boundary')
        work_fd = fds[-1]
        opened = os.fstat(work_fd)
        require((opened.st_dev, opened.st_ino) == (identity['device'], identity['inode']), 'retained workspace identity changed')
        fd = os.open('.devlab-volume-id', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=work_fd)
        with os.fdopen(fd, 'rb') as stream:
            marker = os.fstat(stream.fileno())
            require(stat.S_ISREG(marker.st_mode) and marker.st_nlink == 1 and marker.st_size <= 64
                    and stream.read(65).strip() == MARKER.encode(), 'workspace volume marker differs')
        listing = reconciliation_inventory(['/bin/ps', '-axo', 'uid=,pid=,pgid=,command='], deadline)
        sdk = ROOT.parent / 'offline-toolchains/android-emulator-v1/sdk'
        for line in listing.decode('utf-8', 'strict').splitlines():
            parts = line.strip().split(None, 3)
            require(len(parts) == 4 and re.fullmatch(r'-?[0-9]+', parts[0])
                    and all(re.fullmatch(r'[0-9]+', part) for part in parts[1:3]), 'process inventory format is invalid')
            _, pid, group, argv = parts
            require(int(pid) not in owned and int(group) not in owned and str(sdk) not in argv
                    and str(ROOT / 'jobs') not in argv, 'a recorded process/group or scoped Android process remains')
        ports = reconciliation_inventory(['/usr/sbin/lsof', '-nP', '-iTCP:5038', '-iTCP:5580', '-iTCP:5581', '-iTCP:5582', '-sTCP:LISTEN'], deadline, allowed=(1,))
        require(not ports, 'private Android ports remain occupied')
        labelled = reconciliation_inventory([DOCKER, 'ps', '--filter', 'label=org.openclaw.android-job', '--format', '{{.ID}}'], deadline)
        require(not labelled.strip(), 'a controller-labelled Android worker is running')
        containers = json.loads(reconciliation_inventory([DOCKER, 'inspect', cid], deadline))
        require(isinstance(containers, list) and len(containers) == 1, 'unexpected exact worker inventory')
        validate_container(containers[0], work, cid, state['jobId'])
        require(containers[0]['State']['Running'] is False, 'reconciled worker is running')
        try:
            os.stat('.android-runtime', dir_fd=work_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise Refused('derived job cache is still present')
        current = os.stat(work.name, dir_fd=fds[-2], follow_symlinks=False)
        require((current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino), 'workspace replaced during admission')
        require(digest(read_admission_bytes(path)) == expected_sha256, 'uncertain state changed during admission')
        return {'jobId': state['jobId'], 'stateSha256': expected_sha256, 'actorSha256': digest(actor.encode()),
                'containerId': cid, 'workspaceIdentity': identity, 'resourcesConfirmedStopped': True,
                'cachePresent': False, 'observedOwnedPidsOrGroups': [], 'checkedAt': time.time(),
                'processInventorySha256': digest(listing), 'originalStatus': 'cleanup-uncertain'}
    finally:
        for fd in reversed(fds):
            os.close(fd)

def verify_operator_reconciliation(path, deadline=None):
    marker_path = ROOT / 'operator-reconciliations' / (path.parent.name + '.json')
    try:
        marker_bytes = read_admission_bytes(marker_path, 8192)
    except FileNotFoundError:
        raise Refused('a trusted operator reconciliation marker is missing')
    try:
        marker = json.loads(marker_bytes)
    except (ValueError, UnicodeError):
        raise Refused('operator reconciliation JSON is invalid')
    fields = {'schemaVersion', 'purpose', 'operatorUid', 'jobId', 'stateSha256', 'actorSha256', 'containerId',
              'workspaceIdentity', 'operatorProofSha256', 'recordedAt', 'resourcesConfirmedStopped'}
    require(isinstance(marker, dict) and set(marker) == fields and type(marker.get('schemaVersion')) is int and marker.get('schemaVersion') == 1
            and marker.get('purpose') == RECONCILIATION_PURPOSE and marker.get('operatorUid') == OPERATOR_UID
            and marker.get('resourcesConfirmedStopped') is True, 'operator reconciliation marker differs')
    require(marker['jobId'] == path.parent.name and all(isinstance(marker[k], str) and re.fullmatch(r'[a-f0-9]{64}', marker[k])
            for k in ('stateSha256', 'actorSha256', 'containerId', 'operatorProofSha256')), 'operator reconciliation identity is invalid')
    require(type(marker['recordedAt']) in (int, float) and math.isfinite(marker['recordedAt'])
            and 0 < marker['recordedAt'] <= time.time(), 'operator reconciliation time is invalid')
    checked = check_reconciliation_resources(path, marker['stateSha256'], deadline)
    require(all(marker[key] == checked[key] for key in ('jobId', 'stateSha256', 'actorSha256', 'containerId', 'workspaceIdentity')), 'operator reconciliation binding differs')
    require(read_admission_bytes(marker_path, 8192) == marker_bytes, 'operator reconciliation marker changed during admission')
    return dict(checked, operatorMarkerSha256=digest(marker_bytes), operatorProofSha256=marker['operatorProofSha256'])

def history_admission():
    """A reconciled old failure may permit a new actor; it never resumes that job."""
    reconciled = []
    deadline = time.monotonic() + 6
    for path in sorted((ROOT / 'jobs').glob('*/state.json')):
        try:
            state, _ = admission_state(path)
            require(state.get('status') in TERMINAL, 'retained job is unfinished')
            if state['status'] == 'cleanup-uncertain':
                reconciled.append(verify_operator_reconciliation(path, deadline))
        except Exception as error:
            raise Refused('Historical Android job %s requires operator reconciliation: %s. No new job was started.' % (path.parent.name, str(error)[:300]))
    return reconciled

def preflight():
    require(os.getuid() == 502, 'controller must run as the configured service account')
    volume = plistlib.loads(raw_command(['/usr/sbin/diskutil', 'info', '-plist', str(VOLUME)]))
    require(volume.get('VolumeUUID') == VOLUME_UUID and volume.get('MountPoint') == str(VOLUME) and volume.get('TotalSize') == 2147442688, 'expected project volume is not mounted')
    require(not VOLUME.is_symlink(), 'project volume symlink refused')
    free = shutil.disk_usage(VOLUME).free
    require(free >= 520 * 1024 * 1024, 'at least 520 MiB free project capacity required; no automatic resize or cleanup')
    from emulator_adapter import SDK
    require(all((SDK / p).is_file() for p in ('emulator/emulator', 'platform-tools/adb', 'system-images/android-35/default/arm64-v8a/system.img')), 'prepared official Android emulator files missing')
    require(shutil.disk_usage(ROOT).free >= 6 * 1024**3, 'at least 6 GiB free host capacity required for the disposable emulator')
    cfg = json.loads(CONFIG.read_text())
    model = cfg['agents']['defaults']['model']
    require(model.get('primary') == 'ollama/' + MODEL and not model.get('fallbacks'), 'local-only model defaults changed')
    provider = cfg['models']['providers']['ollama']
    require(provider.get('baseUrl', '').rstrip('/') == 'http://127.0.0.1:11434', 'local model endpoint changed')
    require(json.loads(Path('/CONFIGURE/ollama-server.json').read_text()).get('disable_ollama_cloud') is True, 'Ollama cloud disable setting missing')
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=10) as stream:
        models = json.load(stream)['models']
    selected = next((m for m in models if m['name'] == MODEL), {})
    require(selected.get('digest') == MODEL_SHA and not selected.get('remote_model') and not selected.get('remote_host'), 'local Qwen weights not verified')
    image = json.loads(raw_command([DOCKER, 'image', 'inspect', IMAGE]))[0]
    require(image['Id'] == IMAGE, 'prepared Android image absent')
    return {'volumeUUID': VOLUME_UUID, 'freeBytes': free, 'image': IMAGE, 'model': MODEL,
            'modelDigest': MODEL_SHA, 'cloudDisabled': True, 'configSHA256': digest(CONFIG.read_bytes())}

WRITE_SOURCES = '''import json,os,re,sys
from pathlib import Path
items=json.load(sys.stdin)
root=Path('/workspace/project/app/src/main/java/org/openclaw/trial')
for p in [root,*root.parents]:
 if p.is_symlink():raise RuntimeError('symlink')
root.mkdir(parents=True,exist_ok=True)
for item in items:
 name=item['name'];content=item['content'].encode('utf-8')
 if not re.fullmatch(r'[A-Z][A-Za-z0-9_]{0,63}\\.java',name):raise RuntimeError('name')
 target=root/name;temporary=root/(name+'.new')
 fd=os.open(str(temporary),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(content);f.flush();os.fsync(f.fileno())
 os.replace(str(temporary),str(target))
print('Java sources written inside isolated Android worker')
'''

MEASURE_SEED = '''import json,os,stat,time
root='/opt/gradle-seed'
limit=time.monotonic()+20
total=files=directories=0
def fail(error):raise error
for directory,names,leaves in os.walk(root,followlinks=False,onerror=fail):
 if time.monotonic()>=limit:raise RuntimeError('seed inventory timeout')
 mode=os.lstat(directory).st_mode
 if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):raise RuntimeError('invalid seed directory')
 directories+=1
 for name in names+leaves:
  value=os.lstat(os.path.join(directory,name))
  if stat.S_ISLNK(value.st_mode):raise RuntimeError('seed symlink refused')
  if name in leaves:
   if not stat.S_ISREG(value.st_mode):raise RuntimeError('invalid seed file')
   total+=value.st_size;files+=1
 if files+directories>100000:raise RuntimeError('seed inventory limit')
print(json.dumps({'bytes':total,'files':files,'directories':directories}))
'''

class Job:
    def __init__(self, actor, job_id=None, *, time_limit_minutes=DEFAULT_TIME_LIMIT_MINUTES):
        seconds = validate_time_limit(time_limit_minutes) * 60
        self.actor = actor
        self.id = job_id or 'am-' + uuid.uuid4().hex
        self.path = ROOT / 'jobs' / self.id
        self.work = VOLUME / 'workspace/project' / ('android-chat-' + self.id[3:])
        self.container_name = 'oc-android-' + self.id[3:]
        self.cid = None
        self.deadline = time.monotonic() + seconds
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.cleanup_lock = threading.Lock()
        self.lifecycle_lock = threading.RLock()
        self.state_lock = threading.RLock()
        self.proc_lock = threading.Lock()
        self.processes = set()
        self.emulator = None
        self.build_receipt = None
        self.requests = {}
        self.state = {'jobId': self.id, 'actor': actor, 'createdAt': time.time(),
                      'timeLimitMinutes': time_limit_minutes, 'deadlineSeconds': seconds,
                      'status': 'preparing', 'builds': 0,
                      'writes': 0, 'actions': 0, 'sourceRevision': 0, 'events': []}
        self.path.mkdir(mode=0o700, parents=True)
        self.persist()

    def persist(self):
        with self.state_lock:
            atomic(self.path / 'state.json', copy.deepcopy(self.state))
            from report import write_report
            write_report(self.path, copy.deepcopy(self.state))

    def update_state(self, **values):
        with self.state_lock:
            self.check()
            self.state.update(values)
            self.persist()

    def receipt(self, result, include_text=False):
        with self.state_lock:
            result = with_progress(self.state, result, self.deadline)
        return with_report(self.path, result, include_text=include_text)

    def event(self, action, reason, result):
        with self.state_lock:
            self.state['events'].append({'at': time.time(), 'action': action,
                                         'reason': reason or 'No reason supplied.', 'result': result})
            self.persist()

    def check(self):
        require(not self.cancelled.is_set(), 'job cancelled')
        require(time.monotonic() < self.deadline, 'job deadline expired')
        require(self.state['status'] not in TERMINAL, 'job is terminal')

    def command(self, args, label, seconds=60, input_bytes=None):
        self.check()
        log = self.path / ('%03d-%s.log' % (self.state['actions'], label))
        stderr_log = log.with_name(log.stem + '.stderr.log')
        started = time.time()
        error = None
        with log.open('xb') as output, stderr_log.open('xb') as diagnostics:
            p = subprocess.Popen(args, env=ENV, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                                 stdout=output, stderr=diagnostics, start_new_session=True)
            with self.proc_lock:
                self.processes.add(p)
            end = min(self.deadline, time.monotonic() + seconds)
            def check_output_quota():
                require(log.stat().st_size + stderr_log.stat().st_size <= 4 * 1024 * 1024, 'command log quota: ' + label)
            try:
                if input_bytes is not None:
                    os.set_blocking(p.stdin.fileno(), False)
                    remaining = memoryview(input_bytes)
                    while remaining:
                        self.check()
                        require(time.monotonic() < end, 'command input timeout: ' + label)
                        check_output_quota()
                        try:
                            written = os.write(p.stdin.fileno(), remaining)
                            remaining = remaining[written:]
                        except BlockingIOError:
                            self.cancelled.wait(.05)
                    p.stdin.close()
                while p.poll() is None:
                    self.check()
                    require(time.monotonic() < end, 'command timeout: ' + label)
                    check_output_quota()
                    self.cancelled.wait(.1)
                check_output_quota()
            except Exception as failure:
                error = failure
            finally:
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
                    try:
                        p.wait(3)
                    except subprocess.TimeoutExpired:
                        os.killpg(p.pid, signal.SIGKILL)
                        p.wait(3)
                with self.proc_lock:
                    self.processes.discard(p)
        def file_hash(path):
            hashed = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(65536), b''):
                    hashed.update(block)
            return hashed.hexdigest()
        def tail(path):
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 12000))
                return stream.read(12000).decode(errors='replace')
        receipt = {'argv': args, 'exitCode': p.returncode, 'startedAt': started,
                   'finishedAt': time.time(), 'log': str(log), 'logSha256': file_hash(log),
                   'stdoutBytes': log.stat().st_size, 'stderrLog': str(stderr_log),
                   'stderrSha256': file_hash(stderr_log), 'stderrBytes': stderr_log.stat().st_size}
        if error is not None:
            receipt['error'] = str(error)
        atomic(log.with_suffix('.json'), receipt)
        if error is not None:
            raise error
        require(p.returncode == 0, label + ' failed: ' + tail(log) + '\n' + tail(stderr_log))
        # Machine-readable results must come solely from stdout. Diagnostics are
        # retained separately and can never become a container identifier.
        return log.read_text(errors='replace'), receipt

    def inspect(self):
        c = json.loads(raw_command([DOCKER, 'inspect', self.cid or self.container_name]))[0]
        validate_container(c, self.work, self.cid, self.id)
        if self.cid is None:
            self.cid = c['Id']
        return c

    def start_worker(self):
        with self.lifecycle_lock:
            return self._start_worker()

    def _start_worker(self):
        self.check()
        c = self.inspect()
        if not c['State']['Running']:
            self.command([DOCKER, 'start', self.cid], 'start-worker', 20)
        self.check()
        require(self.inspect()['State']['Running'], 'worker did not start')

    def stop_worker(self):
        with self.lifecycle_lock:
            return self._stop_worker()

    def _stop_worker(self):
        if not self.cid:
            # A cancelled create may have reached Docker. Enroll only its full boundary.
            p = subprocess.run([DOCKER, 'inspect', self.container_name], env=ENV, capture_output=True, timeout=15)
            if p.returncode:
                require(b'No such object:' in p.stderr or b'No such container:' in p.stderr, 'worker absence could not be confirmed')
                return {'present': False, 'running': False}
            c = json.loads(p.stdout)[0]
            validate_container(c, self.work, None, self.id)
            self.cid = c['Id']
        c = self.inspect()
        if c['State']['Running']:
            raw_command([DOCKER, 'stop', '--time', '5', self.cid], 20)
        require(not self.inspect()['State']['Running'], 'worker stop unconfirmed')
        return {'containerId': self.cid, 'running': False}

    def prepare(self):
        baseline = preflight()
        self.check()
        require(not self.work.exists(), 'fresh workspace required')
        self.work.mkdir(mode=0o777)
        self.work.chmod(0o777)
        identity = self.work.lstat()
        self.state['workspaceIdentity'] = {'device': identity.st_dev, 'inode': identity.st_ino}
        self.persist()
        marker = self.work / '.devlab-volume-id'
        marker.write_text(MARKER + '\n')
        marker.chmod(0o444)
        project = self.work / 'project'
        for name, content in SCAFFOLD.items():
            p = project / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            p.chmod(0o444)
        for p in [project, *project.rglob('*')]:
            if p.is_dir():
                p.chmod(0o777)
        command = [DOCKER, 'create', '--platform', 'linux/amd64', '--name', self.container_name, '--label', 'org.openclaw.android-job=' + self.id,
                   '--network', 'none', '--read-only', '--user', '1000:1000', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--memory', '2g', '--memory-swap', '2g',
                   '--cpus', '2', '--pids-limit', '128', '--ulimit', 'nofile=1024:1024',
                   '--ulimit', 'fsize=268435456:268435456', '--log-driver', 'local', '--log-opt', 'max-size=4m', '--log-opt', 'max-file=1', '--log-opt', 'compress=false',
                   '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m', '--tmpfs', '/run:rw,noexec,nosuid,size=16m',
                   '--tmpfs', '/var/tmp:rw,noexec,nosuid,size=16m', '--mount', 'type=bind,src=' + str(self.work) + ',dst=/workspace',
                   '--workdir', '/workspace/project', IMAGE, 'sleep', 'infinity']
        with self.lifecycle_lock:
            self.check()
            text, _ = self.command(command, 'create-worker', 30)
            candidate = text.strip()
            require(re.fullmatch('[a-f0-9]{64}', candidate), 'unexpected container ID')
            created = json.loads(raw_command([DOCKER, 'inspect', candidate]))[0]
            validate_container(created, self.work, candidate, self.id)
            self.cid = candidate
            self.state['containerId'] = self.cid
            self.persist()
        self.start_worker()
        self.command([DOCKER, 'exec', self.cid, '/usr/local/bin/devlab-guard', MARKER], 'volume-guard', 15)
        measured, measure_receipt = self.command([DOCKER, 'exec', self.cid, '/usr/bin/python3', '-B', '-c', MEASURE_SEED], 'measure-offline-cache', 30)
        seed = json.loads(measured)
        require(isinstance(seed, dict) and set(seed) == {'bytes', 'files', 'directories'}, 'invalid offline seed measurement')
        require(type(seed['bytes']) is int and 0 < seed['bytes'] <= 2147442688 and
                all(type(seed[key]) is int and 0 < seed[key] <= 100000 for key in ('files', 'directories')),
                'offline seed measurement is out of bounds')
        free = shutil.disk_usage(VOLUME).free
        reserve = 100 * 1024 * 1024
        self.state['cacheAdmission'] = {'seedBytes': seed['bytes'], 'seedFiles': seed['files'],
                                        'reserveBytes': reserve, 'requiredBytes': seed['bytes'] + reserve,
                                        'freeBytesBeforeCopy': free, 'measurement': measure_receipt}
        self.persist()
        require(free >= seed['bytes'] + reserve, 'measured offline seed plus 100 MiB build reserve exceeds free project capacity; cache was not copied')
        self.command([DOCKER, 'exec', self.cid, '/usr/bin/python3', '-B', '/usr/local/lib/android_setup.py'], 'seed-offline-cache', 90)
        versions, _ = self.command([DOCKER, 'exec', self.cid, '/bin/sh', '-c', '/opt/jdk/bin/java -version && /usr/local/bin/gradle-offline --version && test -f /opt/android-sdk/platforms/android-35/android.jar'], 'actual-toolchain', 30)
        require(shutil.disk_usage(VOLUME).free >= 100 * 1024 * 1024, 'insufficient build reserve after seeding')
        self.stop_worker()
        self.update_state(status='ready', baseline=baseline, workspace=str(self.work), toolchain=versions[-4000:])
        return {'summary': 'A clean Android project and private offline build cache are ready in the restricted Linux worker. The separate emulator will be used only after a verified APK exists.',
                'package': PACKAGE, 'activity': 'MainActivity', 'javaSourceNames': 'MainActivity.java and optional additional simple Java class filenames; package org.openclaw.trial',
                'framework': 'Native Java Android APIs; compile/target SDK 35, min SDK 23. No external dependencies or arbitrary build scripts.',
                'next': 'Read the long-task-runner skill. Send original Java source with write_sources, then build. Use start_test and images/tap bursts to verify behavior; stop when finished.',
                'toolchain': versions[-2500:], 'baseline': baseline}

    def scaffold_ok(self):
        for name, content in SCAFFOLD.items():
            p = safe_regular(self.work / 'project' / name, self.work, 65536)
            require(p.read_text() == content, 'trusted build scaffold changed')

    def sources(self):
        root = self.work / 'project/app/src/main/java/org/openclaw/trial'
        require(root.is_dir() and not root.is_symlink(), 'Java source directory missing')
        result = {}
        for path in root.iterdir():
            require(JAVA_NAME.fullmatch(path.name), 'unexpected source entry')
            safe_regular(path, self.work, 128 * 1024)
            result[path.name] = {'sha256': digest(path.read_bytes()), 'bytes': path.stat().st_size}
        require('MainActivity.java' in result and len(result) <= 16 and sum(x['bytes'] for x in result.values()) <= 512 * 1024, 'bounded MainActivity source required')
        return result

    def write_sources(self, files):
        require(self.state['writes'] < 8, 'source revision budget exhausted')
        if self.emulator is not None:
            cleanup = self.emulator.stop()
            self.event('retire_test_for_repair', 'Stop the previous guest before changing the app; its observations stay in the record.', cleanup)
            require(cleanup.get('status') == 'stopped', 'previous emulator cleanup unconfirmed')
            self.emulator = None
        self.state['writes'] += 1
        self.state['sourceRevision'] += 1
        self.build_receipt = None
        self.state.pop('apk', None)
        self.persist()
        self.scaffold_ok()
        self.start_worker()
        try:
            self.command([DOCKER, 'exec', '-i', self.cid, '/usr/bin/python3', '-c', WRITE_SOURCES], 'write-java', 20, json.dumps(files).encode())
        finally:
            self.stop_worker()
        manifest = self.sources()
        snapshot = self.path / ('source-%d' % self.state['sourceRevision'])
        snapshot.mkdir()
        for name in manifest:
            shutil.copyfile(self.work / 'project/app/src/main/java/org/openclaw/trial' / name, snapshot / name)
        atomic(snapshot / 'manifest.json', manifest)
        self.update_state(status='source-ready', lastWrittenSourceRevision=self.state['sourceRevision'])
        return {'summary': 'The original Java source was written inside the isolated worker. Any earlier APK qualification is now invalid; the next step is a fresh offline build.', 'sources': manifest, 'sourceRevision': self.state['sourceRevision']}

    def build(self):
        require(self.state['builds'] < 3, 'three-build budget exhausted')
        require(self.emulator is None, 'stop the emulator before rebuilding')
        require(not self.state.get('testRequiresRepair') or (self.state['sourceRevision'] > self.state['failedTestSourceRevision'] and self.state.get('lastWrittenSourceRevision') == self.state['sourceRevision']),
                'the failed guest test requires a new source write before another build')
        self.state['builds'] += 1
        self.build_receipt = None
        self.state.pop('apk', None)
        self.state['status'] = 'building'
        self.persist()
        self.scaffold_ok()
        before = self.sources()
        self.start_worker()
        try:
            build_text, build_record = self.command([DOCKER, 'exec', '-w', '/workspace/project', self.cid, '/usr/local/bin/gradle-offline', '--no-build-cache', '--rerun-tasks', ':app:assembleDebug'], 'offline-build', 180)
            _, signature = self.command([DOCKER, 'exec', '-w', '/workspace/project', self.cid, '/opt/android-sdk/build-tools/35.0.0/apksigner', 'verify', '--verbose', 'app/build/outputs/apk/debug/app-debug.apk'], 'apk-signature', 30)
            package_text, package_record = self.command([DOCKER, 'exec', '-w', '/workspace/project', self.cid, '/opt/android-sdk/build-tools/35.0.0/aapt', 'dump', 'badging', 'app/build/outputs/apk/debug/app-debug.apk'], 'apk-package', 30)
            require("package: name='" + PACKAGE + "'" in package_text, 'unexpected APK package')
            require("launchable-activity: name='" + PACKAGE + ".MainActivity'" in package_text, 'unexpected launcher activity')
            require(before == self.sources(), 'source changed during build')
            self.scaffold_ok()
        finally:
            self.stop_worker()
        apk = safe_regular(self.work / 'project/app/build/outputs/apk/debug/app-debug.apk', self.work, 8 * 1024 * 1024)
        require(apk.stat().st_size > 0, 'empty APK')
        data = apk.read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            entries = z.infolist()
            require(len(entries) < 2048 and sum(x.file_size for x in entries) <= 32 * 1024 * 1024, 'APK archive quota')
            require(all(not x.filename.startswith('/') and '..' not in Path(x.filename).parts for x in entries), 'unsafe APK archive names')
            require('AndroidManifest.xml' in z.namelist() and 'classes.dex' in z.namelist(), 'APK content missing')
        target = self.path / ('app-build-%d.apk' % self.state['builds'])
        with target.open('xb') as stream:
            stream.write(data)
        target.chmod(0o400)
        self.build_receipt = {'path': str(target), 'sha256': digest(data), 'bytes': len(data),
                              'sourceRevision': self.state['sourceRevision'], 'sources': before,
                              'build': build_record, 'signature': signature, 'packageCheck': package_record,
                              'image': IMAGE, 'package': PACKAGE, 'builtAt': time.time()}
        atomic(self.path / ('build-%d.json' % self.state['builds']), self.build_receipt)
        self.update_state(status='built', apk=self.build_receipt, testRequiresRepair=False)
        return {'summary': 'The APK built offline and passed signature, package and launcher checks. The worker is stopped. Android launch and gameplay remain unverified until the separate emulator test.', 'apk': self.build_receipt, 'buildOutput': build_text[-5000:]}

    def start_test(self):
        require(not self.state.get('testRequiresRepair'), 'the failed guest test requires write_sources and a new build before start_test')
        require(self.emulator is None and self.build_receipt is not None, 'a current verified APK and no active emulator are required')
        apk = self.build_receipt
        require(apk['sourceRevision'] == self.state['sourceRevision'], 'APK is stale')
        p = safe_regular(Path(apk['path']), self.path, 8 * 1024 * 1024)
        require(digest(p.read_bytes()) == apk['sha256'], 'captured APK changed')
        require(self.sources() == apk['sources'], 'source changed after build')
        from emulator_adapter import EmulatorSession
        directory = self.path / ('emulator-%d' % self.state['builds'])
        directory.mkdir(mode=0o700)
        self.emulator = EmulatorSession(directory, p, apk['sha256'], package=PACKAGE, activity='.MainActivity', deadline=self.deadline)
        self.update_state(status='starting-test', emulatorStatus='starting', testDisplay=None)
        result = self.emulator.start()
        # Only the adapter's measured size may bound later taps; an absent or
        # malformed value leaves the adapter's own check as the only gate.
        display = result.get('display') if isinstance(result, dict) else None
        if not (isinstance(display, list) and len(display) == 2 and all(type(v) is int and 1 <= v <= 4096 for v in display)):
            display = None
        self.update_state(status='testing', emulatorStatus='running', testDisplay=display)
        return self.observation_result(result, display)

    def check_tap_target(self, params):
        """Refuse a tap outside the measured display before the adapter is called."""
        display = self.state.get('testDisplay')
        if display is None:
            return
        width, height = display
        x, y = params['x'], params['y']
        if 0 <= x < width and 0 <= y < height:
            return
        raise InputRejected(
            'Tap (%d, %d) was refused before any input reached the Android guest: it is outside the observed %dx%d display. '
            'Coordinates are actual screen pixels, not a scaled range: use 0 <= x < %d and 0 <= y < %d, such as the center of a '
            'control from the latest uiSummary. The controller did not stop the guest or change the qualified APK, so no source '
            'write or rebuild is needed. Observe again if the current screen is uncertain.' % (x, y, width, height, width, height),
            {'inputDelivered': False, 'guestStoppedByController': False, 'rebuildRequired': False,
             'display': {'width': width, 'height': height}, 'rejectedTap': {'x': x, 'y': y},
             'next': 'Retry with the same jobId and coordinates inside the display, or observe first.'})

    @staticmethod
    def observation_result(result, display=None):
        images = []
        def collect(value):
            if isinstance(value, dict):
                if isinstance(value.get('path'), str) and value['path'].endswith('.png'):
                    images.append(value)
                else:
                    for item in value.values(): collect(item)
            elif isinstance(value, list):
                for item in value: collect(item)
        collect(result)
        summary = 'These are actual Android guest observations and screenshots. Inspect the screens against the requested behavior; successful input or launch alone is not a gameplay pass.'
        response = {'summary': summary, 'observation': result, 'images': images}
        if display is not None:
            response['display'] = {'width': display[0], 'height': display[1]}
            response['summary'] += (' Tap coordinates are actual pixels of this %dx%d display: 0 <= x < %d and 0 <= y < %d.'
                                    ' uiSummary (afterUiSummary for a tap) lists the visible text and controls with their bounds and centers;'
                                    ' it was captured after the screenshot, so observe again if the two disagree.' % (display[0], display[1], display[0], display[1]))
        return response

    def retire_cache(self, worker):
        """Retire this job's derived cache only after confirmed worker cleanup."""
        with self.lifecycle_lock:
            return self._retire_cache(worker)

    def _retire_cache(self, worker):
        result = {'path': str(self.work / '.android-runtime'), 'status': 'refused', 'complete': False,
                  'logicalBytesBefore': None, 'logicalBytesAfter': None,
                  'freeBytesBefore': None, 'freeBytesAfter': None}
        fds = []
        volume_fd = cache_fd = None
        deadline = time.monotonic() + 60
        try:
            require(worker.get('running') is False, 'worker stop must be confirmed before cache retirement')
            identity = self.state.get('workspaceIdentity')
            if worker.get('present') is False and self.cid is None and identity is None:
                result.update(status='not-created', complete=True, logicalBytesBefore=0, logicalBytesAfter=0)
                return result
            require(isinstance(identity, dict) and set(identity) == {'device', 'inode'}, 'retained workspace identity is required')
            require(JOB_NAME.fullmatch(self.id) and self.work == VOLUME / 'workspace/project' / ('android-chat-' + self.id[3:]), 'unexpected job workspace')
            volume = plistlib.loads(raw_command(['/usr/sbin/diskutil', 'info', '-plist', str(VOLUME)]))
            require(volume.get('VolumeUUID') == VOLUME_UUID and volume.get('MountPoint') == str(VOLUME) and volume.get('TotalSize') == 2147442688, 'expected project volume is not mounted for cleanup')
            volume_fd = open_directory_nofollow(VOLUME)
            fds.append(volume_fd)
            device = os.fstat(volume_fd).st_dev
            work_fd = volume_fd
            for name in ('workspace', 'project', self.work.name):
                work_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=work_fd)
                fds.append(work_fd)
                require(os.fstat(work_fd).st_dev == device, 'workspace crosses a filesystem boundary')
            work_stat = os.fstat(work_fd)
            require((work_stat.st_dev, work_stat.st_ino) == (identity['device'], identity['inode']), 'retained workspace identity changed')
            marker_fd = os.open('.devlab-volume-id', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=work_fd)
            with os.fdopen(marker_fd, 'rb') as stream:
                marker_stat = os.fstat(stream.fileno())
                require(stat.S_ISREG(marker_stat.st_mode) and marker_stat.st_nlink == 1 and marker_stat.st_size <= 64, 'invalid workspace marker')
                require(stream.read(65).strip() == MARKER.encode(), 'workspace marker changed')
            def free_bytes():
                value = os.fstatvfs(volume_fd)
                return value.f_bavail * value.f_frsize
            result['freeBytesBefore'] = free_bytes()
            try:
                cache_stat = os.stat('.android-runtime', dir_fd=work_fd, follow_symlinks=False)
            except FileNotFoundError:
                result.update(status='not-present', complete=True, logicalBytesBefore=0, logicalBytesAfter=0, freeBytesAfter=free_bytes())
                return result
            require(self.cid is not None and worker.get('containerId') == self.cid, 'owned worker identity required to retire an existing cache')
            require(not self.inspect()['State']['Running'], 'worker restarted before cache retirement')
            require(stat.S_ISDIR(cache_stat.st_mode) and cache_stat.st_dev == device, 'cache root is not a directory on the project volume')
            cache_fd = os.open('.android-runtime', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=work_fd)
            fds.append(cache_fd)
            opened = os.fstat(cache_fd)
            require((opened.st_dev, opened.st_ino) == (cache_stat.st_dev, cache_stat.st_ino), 'cache root identity changed')
            result['logicalBytesBefore'] = cache_tree(cache_fd, device, deadline)
            current = os.stat('.android-runtime', dir_fd=work_fd, follow_symlinks=False)
            require((current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino), 'cache root replaced before retirement')
            cache_tree(cache_fd, device, deadline, remove=True)
            current = os.stat('.android-runtime', dir_fd=work_fd, follow_symlinks=False)
            require((current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino), 'cache root replaced during retirement')
            os.rmdir('.android-runtime', dir_fd=work_fd)
            result.update(status='removed', complete=True, logicalBytesAfter=0, freeBytesAfter=free_bytes())
        except Exception as error:
            result.update(status='failed', error=str(error))
            if cache_fd is not None:
                try:
                    result['logicalBytesAfter'] = cache_tree(cache_fd, os.fstat(cache_fd).st_dev, time.monotonic() + 5)
                except Exception:
                    pass
            if volume_fd is not None:
                try:
                    value = os.fstatvfs(volume_fd)
                    result['freeBytesAfter'] = value.f_bavail * value.f_frsize
                except OSError:
                    pass
        finally:
            for fd in reversed(fds):
                os.close(fd)
        return result

    def stop(self, status='stopped'):
        self.cancelled.set()
        with self.cleanup_lock:
            if self.state['status'] in TERMINAL and 'cleanup' in self.state:
                return self.state['cleanup']
            errors, outcome = [], {}
            if self.emulator is not None:
                try:
                    outcome['emulator'] = self.emulator.stop()
                    if outcome['emulator'].get('status') != 'stopped':
                        errors.append('emulator cleanup unconfirmed')
                except Exception as error:
                    errors.append('emulator: ' + str(error))
            with self.proc_lock:
                processes = list(self.processes)
            for p in processes:
                if p.poll() is None:
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            try:
                with self.lifecycle_lock:
                    outcome['worker'] = self.stop_worker()
                    outcome['cache'] = self.retire_cache(outcome['worker'])
            except Exception as error:
                errors.append('worker: ' + str(error))
            resources_stopped = not errors
            if 'cache' not in outcome:
                outcome['cache'] = {'status': 'not-attempted', 'complete': False, 'reason': 'Worker cleanup was not confirmed.'}
            if not outcome['cache']['complete']:
                errors.append('cache: ' + outcome['cache'].get('error', outcome['cache'].get('reason', 'cache retirement unconfirmed')))
            outcome.update(errors=errors, stopped=resources_stopped, cleanupComplete=not errors, finishedAt=time.time())
            with self.state_lock:
                if 'emulator' in outcome:
                    self.state['emulatorStatus'] = outcome['emulator'].get('status', 'cleanup-unconfirmed')
                self.state.update(status='cleanup-uncertain' if errors else status, cleanup=outcome)
                self.persist()
            return outcome

    def perform(self, request_id, params):
        action = params['action']
        hashed = digest(json.dumps(params, sort_keys=True).encode())
        require(isinstance(request_id, str) and 1 <= len(request_id) <= 256, 'invalid request identity')
        previous = self.requests.get(request_id)
        if previous:
            require(previous['digest'] == hashed, 'request ID reused for different arguments')
            require(previous.get('result') is not None, 'previous action outcome is uncertain; do not replay')
            return with_report(self.path, previous['result'])
        if action == 'stop':
            cleanup = self.stop()
            complete = cleanup.get('cleanupComplete') is True and cleanup.get('cache', {}).get('complete') is True
            result = {'ok': complete, 'jobId': self.id, 'summary': 'The Android job is stopped, its derived cache is retired, and its evidence is retained.' if complete else 'Android job cleanup is incomplete; inspect the separate worker, emulator, and cache receipts.', 'cleanup': cleanup}
            result = self.receipt(result, include_text=True)
            self.requests[request_id] = {'digest': hashed, 'result': result}
            return result
        if action == 'status':
            with self.state_lock:
                self.persist()
                result = {'ok': True, 'jobId': self.id, 'summary': 'This is the retained state of the session-owned Android job.', 'state': {k:copy.deepcopy(v) for k,v in self.state.items() if k not in ('actor', 'events')}}
                return self.receipt(result, include_text=True)
        require(self.lock.acquire(False), 'another operation is active for this job')
        try:
            self.check()
            require(self.state['actions'] < 40, 'job action budget exhausted')
            self.state['actions'] += 1
            self.requests[request_id] = {'digest': hashed, 'result': None}
            atomic(self.path / ('request-%s.json' % digest(request_id.encode())[:24]), {'requestId': request_id, 'params': params, 'status': 'reserved'})
            if action == 'prepare': result = self.prepare()
            elif action == 'write_sources': result = self.write_sources(params['files'])
            elif action == 'build': result = self.build()
            elif action == 'start_test': result = self.start_test()
            elif action in ('tap', 'observe'):
                require(self.emulator is not None and self.state['status'] == 'testing', 'start the qualified APK test first')
                if action == 'tap':
                    self.check_tap_target(params)
                observation = self.emulator.observe() if action == 'observe' else self.emulator.tap(params['x'], params['y'], params.get('count',1), params.get('intervalMs',300))
                result = self.observation_result(observation, self.state.get('testDisplay'))
            else: raise Refused('unsupported action')
            self.check()
            result.update(ok=True, jobId=self.id)
        except Exception as error:
            result = {'ok': False, 'jobId': self.id, 'summary': str(error)}
            if self.cancelled.is_set() or time.monotonic() >= self.deadline:
                result['cleanup'] = self.stop('failed')
            elif isinstance(error, InputRejected):
                # No guest command was issued, so this is not a guest failure:
                # keep the running test and its qualified APK for a corrected tap.
                result.update(error.details)
            elif action == 'start_test' and self.state.get('testRequiresRepair') and self.emulator is None:
                result['repairAllowed'] = self.state['writes'] < 8 and self.state['builds'] < 3 and self.state['actions'] <= 37
                result['next'] = 'The failed guest is stopped. Write repaired source, build a new APK, then start a new test; the previous test directory is retained.'
            elif action in ('prepare', 'start_test'):
                result['cleanup'] = self.stop('failed')
            elif action in ('tap', 'observe') and self.emulator is not None:
                try:
                    cleanup = self.emulator.stop()
                except Exception as cleanup_error:
                    cleanup = {'status': 'cleanup-unconfirmed', 'errors': [str(cleanup_error)]}
                result['emulatorCleanup'] = cleanup
                with self.state_lock:
                    self.state.update(emulatorStatus=cleanup.get('status', 'cleanup-unconfirmed'), lastEmulatorCleanup=cleanup,
                                      testRequiresRepair=True, failedTestSourceRevision=self.state['sourceRevision'])
                    self.persist()
                if cleanup.get('status') == 'stopped' and not cleanup.get('errors') and not cleanup.get('survivingProcessGroups'):
                    self.emulator = None
                    try:
                        self.update_state(status='built' if self.build_receipt is not None else 'source-ready')
                    except Refused:
                        # A cancellation or deadline may have arrived while the
                        # adapter was producing its stop receipt. Never revive it.
                        result['repairAllowed'] = False
                        result['cleanup'] = self.stop('failed')
                    else:
                        result['repairAllowed'] = self.state['writes'] < 8 and self.state['builds'] < 3 and self.state['actions'] <= 37
                        result['next'] = ('The guest is stopped. Inspect the retained failure, repair with write_sources, build again, then start_test within the remaining budgets.'
                                          if result['repairAllowed'] else 'The guest is stopped and repair budgets are exhausted. Use stop to retire the job cache and retain its evidence.')
                else:
                    result['repairAllowed'] = False
                    result['cleanup'] = self.stop('failed')
            elif action == 'build':
                self.state['status'] = 'built' if self.build_receipt is not None else 'source-ready'
        finally:
            self.lock.release()
        result = self.receipt(result)
        self.requests[request_id] = {'digest': hashed, 'result': result}
        atomic(self.path / ('request-%s.json' % digest(request_id.encode())[:24]), {'requestId': request_id, 'paramsSha256': hashed, 'status': 'returned', 'result': result})
        self.event(action, params.get('reason', ''), result)
        return result

class Controller:
    def __init__(self):
        self.jobs = {}
        self.cancelled_actors = set()
        self.lock = threading.Lock()
        self.last_activity = time.monotonic()

    def dispatch(self, request):
        actor, params = validate_request(request)
        self.last_activity = time.monotonic()
        with self.lock:
            if params['action'] == 'stop':
                self.cancelled_actors.add(actor)
                cancel_actor(actor)
            if params['action'] == 'prepare':
                require(actor not in self.cancelled_actors and not cancellation_path(actor).exists(), 'preparation was cancelled for this session')
            matching = [j for j in self.jobs.values() if j.actor == actor]
            job = self.jobs.get(params.get('jobId')) if params.get('jobId') else (matching[-1] if matching else None)
            if job is None and params['action'] in ('status', 'stop', 'prepare'):
                retained = retained_job(actor, params.get('jobId'))
                if retained is not None:
                    return retained_receipt(retained, params['action'])
            if params.get('jobId'):
                require(job is not None and job.actor == actor, 'job not owned by this session')
            if params['action'] == 'prepare' and job is None:
                require(not any(j.state['status'] not in TERMINAL for j in self.jobs.values()), 'another Android job is active')
                require(not any(j.state['status'] == 'cleanup-uncertain' for j in self.jobs.values()), 'prior cleanup needs operator review')
                job = Job(actor, time_limit_minutes=params.get('timeLimitMinutes', DEFAULT_TIME_LIMIT_MINUTES))
                self.jobs[job.id] = job
            if job is None:
                require(params['action'] in ('status','stop'), 'prepare an Android job first')
                return {'ok': True, 'summary': 'This session has no active Android job.', 'status': 'none'}
        if (params['action'] == 'prepare' and request['requestId'] not in job.requests
                and 'timeLimitMinutes' in params and params['timeLimitMinutes'] * 60 != job.state.get('deadlineSeconds')):
            return job.receipt({'ok': False, 'jobId': job.id,
                               'summary': 'The controller time limit was fixed at this job\u2019s first preparation and cannot be changed. Its original deadline and other limits remain unchanged; use status to inspect them.'})
        if params['action'] == 'prepare' and job.state['status'] != 'preparing' and request['requestId'] not in job.requests:
            if job.state['status'] in TERMINAL:
                return job.receipt({'ok': False, 'jobId': job.id, 'retryAllowed': False,
                        'summary': 'This Android job has ended and cannot be restarted in this session. Repeating prepare will not create another job or reset its limits. Use status to inspect the retained failure and cleanup evidence.',
                        'status': job.state['status']})
            return job.receipt({'ok': True, 'jobId': job.id, 'summary': 'This session already has a retained Android job. Use status; a second preparation was not started.', 'status': job.state['status']})
        return job.perform(request['requestId'], params)

    def watch(self):
        while True:
            time.sleep(.5)
            for job in list(self.jobs.values()):
                if job.state['status'] not in TERMINAL:
                    try:
                        used = evidence_bytes(job.path)
                        if time.monotonic() >= job.deadline or used > 96 * 1024 * 1024:
                            job.stop('expired')
                    except Exception as error:
                        job.event('watchdog_failure', 'The controller could not verify its resource accounting, so it requested cleanup.', {'summary': str(error)})
                        job.stop('failed')

def serve():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    lockfile = (ROOT / 'server.lock').open('a')
    fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    require(not SOCKET.exists(), 'existing control socket requires review; no blind replacement')
    # A trusted marker permits only new sessions after fresh read-only checks.
    # The original uncertain state, old PIDs and session limits remain untouched.
    reconciled = history_admission()
    if reconciled:
        print(json.dumps({'historicalReconciliationChecks': reconciled}), flush=True)
    controller = Controller()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(420)
            raw = self.rfile.readline(MAX_REQUEST + 1)
            try:
                require(len(raw) <= MAX_REQUEST and raw.endswith(b'\n'), 'request byte limit')
                result = controller.dispatch(json.loads(raw))
            except Exception as error:
                result = {'ok': False, 'summary': str(error)}
            data = json.dumps(result).encode() + b'\n'
            if len(data) > MAX_RESPONSE:
                data = b'{"ok":false,"summary":"response size limit"}\n'
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    server = Server(str(SOCKET), Handler)
    os.chmod(SOCKET, 0o600)
    server.timeout = .5
    threading.Thread(target=controller.watch, daemon=True).start()
    def shutdown(signum, frame):
        for job in list(controller.jobs.values()):
            job.stop('stopped')
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        while time.monotonic() - controller.last_activity < 180:
            server.handle_request()
            if any(j.state['status'] not in TERMINAL for j in controller.jobs.values()):
                controller.last_activity = time.monotonic()
    finally:
        for job in list(controller.jobs.values()):
            job.stop('stopped')
        server.server_close()
        SOCKET.unlink(missing_ok=True)

def client():
    raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
    require(len(raw) <= MAX_REQUEST, 'request byte limit')
    request = json.loads(raw)
    actor, params = validate_request(request)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if request['params']['action'] == 'stop':
        cancel_actor(actor)
    if request['params']['action'] == 'prepare':
        require(not cancellation_path(actor).exists(), 'preparation was cancelled for this session')
    # The fixed controller starts only for a preparation request, never model source.
    if not SOCKET.exists():
        if params['action'] in ('status', 'stop', 'prepare'):
            retained = retained_job(actor, params.get('jobId'))
            if retained is not None:
                result = retained_receipt(retained, params['action'])
                print(json.dumps(result));return
            if params['action'] in ('status', 'stop'):
                print(json.dumps({'ok':True,'summary':'No Android job for this session.','status':'none'}));return
        require(request['params']['action'] == 'prepare', 'controller unavailable; no replay or automatic recovery')
        history_admission()  # Give the caller the exact gate failure before spawn.
        log = (ROOT/'server.log').open('ab', buffering=0)
        subprocess.Popen(['/usr/bin/python3','-B',str(ROOT/'bridge.py'),'serve'], env=ENV, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        log.close()
        until = time.monotonic() + 8
        while not SOCKET.exists() and time.monotonic() < until:
            time.sleep(.05)
        require(SOCKET.exists(), 'Android controller did not start; inspect retained server log')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(410)
        sock.connect(str(SOCKET))
        sock.sendall(json.dumps(request).encode() + b'\n')
        result = b''
        while not result.endswith(b'\n'):
            data = sock.recv(65536)
            require(bool(data), 'controller connection ended without receipt')
            result += data
            require(len(result) <= MAX_RESPONSE, 'controller response size limit')
    sys.stdout.buffer.write(result)

if __name__ == '__main__':
    try:
        require(len(sys.argv)==2 and sys.argv[1] in ('client','serve'), 'fixed client or serve entry required')
        serve() if sys.argv[1]=='serve' else client()
    except Exception as error:
        print(json.dumps({'ok':False,'summary':str(error)}))
        raise SystemExit(1)
