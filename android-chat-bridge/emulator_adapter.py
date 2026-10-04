"""Bounded official Android guest adapter. Receipts record observations, not gameplay success."""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import subprocess
import threading
import time
import uuid
from xml.etree import ElementTree

SDK = Path('/CONFIGURE/android-sdk')
PORT, ADB_PORT, GRPC_PORT = 5580, 5038, 5582
MAX_OUTPUT = 8 * 1024 * 1024
PNG = b'\x89PNG\r\n\x1a\n'
STOP_GROUP_GRACE_SECONDS = 2.0
STOP_GROUP_MAX_OBSERVATIONS = 21
UI_TREE_LIMIT = 2 * 1024 * 1024
UI_NODE_LIMIT = 4000
UI_DEPTH_LIMIT = 96
UI_ELEMENT_LIMIT = 60
UI_TEXT_LIMIT = 160
# One guest session may receive every controller action (120) as a 5-file tap burst;
# hitting this cap stops the guest, so it must stay above that, not below it.
SESSION_ARTIFACTS = 640
SESSION_ARTIFACT_BYTES = 128 * 1024 * 1024
# 'android' owns system dialogs such as "isn't responding"; label it, not the app.
SYSTEM_PACKAGE = 'android'
PACKAGE_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+')
BOUNDS = re.compile(r'\[(-?[0-9]{1,6}),(-?[0-9]{1,6})\]\[(-?[0-9]{1,6}),(-?[0-9]{1,6})\]')


class EmulatorError(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise EmulatorError(message)


def ui_text(value):
    return ' '.join(''.join(c if c.isprintable() else ' ' for c in value).split())[:UI_TEXT_LIMIT]


def summarize_ui(raw, size, package):
    """List visible text and controls from untrusted guest view-tree XML.

    Bounds and centers are the dump's own display pixels. This is observation
    data for the model to compare with the request, never a behavioral verdict.
    """
    require(isinstance(raw, bytes) and len(raw) <= UI_TREE_LIMIT, 'view tree exceeds the summary byte limit')
    # uiautomator emits no declarations; refusing them excludes entity expansion.
    require(b'<!' not in raw, 'view tree declarations refused')
    width, height = size
    parser = ElementTree.XMLPullParser(events=('start', 'end'))
    elements, packages = [], []
    nodes = depth = omitted = 0
    rotation = None
    seen_root = app_present = False
    for offset in range(0, len(raw), 16384):
        parser.feed(raw[offset:offset + 16384])
        for event, node in parser.read_events():
            if event == 'end':
                depth -= 1
                continue
            depth += 1
            require(depth <= UI_DEPTH_LIMIT, 'view tree depth limit')
            if not seen_root:
                require(node.tag == 'hierarchy', 'view tree root is not a hierarchy')
                seen_root = True
                value = node.get('rotation', '')
                rotation = int(value) if value in ('0', '1', '2', '3') else None
                continue
            require(node.tag == 'node', 'unexpected view tree element')
            nodes += 1
            require(nodes <= UI_NODE_LIMIT, 'view tree node limit')
            owner = node.get('package', '')
            owner = owner if len(owner) <= 120 and (owner == SYSTEM_PACKAGE or PACKAGE_NAME.fullmatch(owner)) else ''
            app_present = app_present or owner == package
            if owner and owner not in packages and len(packages) < 8:
                packages.append(owner)
            text, description = ui_text(node.get('text', '')), ui_text(node.get('content-desc', ''))
            clickable = node.get('clickable') == 'true'
            match = BOUNDS.fullmatch(node.get('bounds', ''))
            if not (text or description or clickable) or not match:
                continue
            left, top, right, bottom = map(int, match.groups())
            if right <= left or bottom <= top:
                continue  # Zero-area views are not visible tap targets.
            if len(elements) == UI_ELEMENT_LIMIT:
                omitted += 1
                continue
            center = [(left + right) // 2, (top + bottom) // 2]
            item = {'bounds': [left, top, right, bottom], 'center': center,
                    'centerOnDisplay': 0 <= center[0] < width and 0 <= center[1] < height}
            kind = node.get('class', '')
            if len(kind) <= 120 and re.fullmatch(r'[A-Za-z0-9_.$]+', kind):
                item['class'] = kind
            if text:
                item['text'] = text
            if description:
                item['contentDescription'] = description
            identifier = node.get('resource-id', '')
            if len(identifier) <= 120 and re.fullmatch(r'[A-Za-z0-9_.:/]+', identifier):
                item['resourceId'] = identifier
            if clickable:
                item['clickable'] = True
            if node.get('enabled') == 'false':
                item['enabled'] = False
            if node.get('checked') == 'true':
                item['checked'] = True
            if owner and owner != package:
                item['package'] = owner
            elements.append(item)
    parser.close()
    require(seen_root, 'view tree root is missing')
    return {'status': 'summarized', 'display': [width, height], 'rotation': rotation,
            'appPackagePresent': app_present, 'packages': packages, 'nodeCount': nodes,
            'elements': elements, 'elementsOmitted': omitted,
            'note': 'Untrusted guest view-tree data. Bounds and centers are actual display pixels. '
                    'Text and controls listed here are what the guest reported at this capture; compare them with the request yourself.'}


def fingerprint(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(data)
    return result.hexdigest()


class EmulatorSession:
    """One cold guest with controller-owned paths and an absolute monotonic deadline.

    The caller must keep this object alive and invoke stop at the deadline even
    when no model action arrives. No method accepts ADB commands or device IDs.
    stop is safe from that watchdog thread while another method is in flight.
    """
    def __init__(self, job_dir, apk, expected_sha256, package='org.openclaw.trial',
                 activity='.MainActivity', deadline=None):
        self.job_dir, self.apk = Path(job_dir), Path(apk)
        require(self.job_dir.is_absolute() and self.job_dir.resolve() == self.job_dir,
                'job directory must be canonical')
        meta = self.job_dir.stat()
        require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == os.getuid() and not meta.st_mode & 0o022,
                'job directory ownership or permissions')
        require(isinstance(expected_sha256, str) and re.fullmatch('[a-f0-9]{64}', expected_sha256),
                'invalid APK digest')
        require(isinstance(package, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', package),
                'invalid package')
        require(isinstance(activity, str) and re.fullmatch(r'\.?[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*', activity),
                'invalid activity')
        require(type(deadline) in (int, float) and time.monotonic() < deadline < float('inf'),
                'invalid or expired deadline')
        self.expected_sha256, self.package, self.activity, self.deadline = expected_sha256, package, activity, deadline
        self.run_dir = None
        self.env = None
        self.size = None
        self._state_lock = threading.RLock()
        self._action_lock = threading.Lock()
        self._stop_lock = threading.Lock()
        self._stopping = False
        self._started = False
        self._attempted = False
        self._children = []
        self._services = []
        self._sequence = 0
        self._artifact_bytes = 0
        self._stop_receipt = None
        self._receipt = {'kind': 'android-emulator-observations', 'apkSha256': expected_sha256,
                         'package': package, 'activity': activity, 'actions': [], 'status': 'prepared'}

    def _stamp(self):
        return {'atMs': int(time.time() * 1000), 'monotonic': time.monotonic()}

    def _ensure(self):
        require(not self._stopping, 'emulator stopped')
        require(time.monotonic() < self.deadline, 'emulator deadline exceeded')
        for item in list(self._services):
            require(item['process'].poll() is None, 'owned service exited: ' + item['name'])
            require(item['log'].stat().st_size <= MAX_OUTPUT, 'service log quota exceeded')

    def _record(self, value):
        with self._state_lock:
            self._receipt['actions'].append(value)
            self._flush()
        return value

    def _flush(self):
        with self._state_lock:
            if self.run_dir:
                target = self.run_dir / 'session.json'
                temporary = self.run_dir / 'session.json.new'
                temporary.write_text(json.dumps(self._receipt, indent=2) + '\n')
                temporary.replace(target)

    def _artifact(self, suffix, data):
        require(len(data) <= MAX_OUTPUT, 'artifact quota exceeded')
        require(self._sequence < SESSION_ARTIFACTS and self._artifact_bytes + len(data) <= SESSION_ARTIFACT_BYTES,
                'session artifact quota exceeded')
        self._sequence += 1
        self._artifact_bytes += len(data)
        path = self.run_dir / ('%04d-%s' % (self._sequence, suffix))
        with path.open('xb') as stream:
            stream.write(data)
        return {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def _spawn(self, command, name, service=False):
        with self._state_lock:
            self._ensure()
            logfile = self.run_dir / (name + '.log') if service else None
            stream = logfile.open('xb') if service else subprocess.PIPE
            try:
                proc = subprocess.Popen(command, env=self.env, stdin=subprocess.DEVNULL,
                                        stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            except BaseException:
                if service:
                    stream.close()
                raise
            item = {'process': proc, 'name': name, 'stream': stream if service else None, 'log': logfile}
            self._children.append(item)
            if service:
                self._services.append(item)
            return item

    @staticmethod
    def _terminate(item):
        proc = item['process']
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=3)
        else:
            proc.wait(timeout=3)

    def _adb(self, args, timeout=15, check=True):
        """Private fixed-command runner. Bounded reads, including stderr."""
        self._ensure()
        item = self._spawn([str(SDK / 'platform-tools/adb'), '-P', str(ADB_PORT),
                            '-s', '127.0.0.1:' + str(PORT + 1), *args], 'adb-call')
        proc = item['process']
        output = bytearray()
        until = min(self.deadline, time.monotonic() + timeout)
        try:
            with selectors.DefaultSelector() as selected:
                selected.register(proc.stdout, selectors.EVENT_READ)
                while selected.get_map():
                    self._ensure()
                    require(time.monotonic() < until, 'ADB command deadline exceeded')
                    for key, _ in selected.select(.1):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            selected.unregister(key.fileobj)
                        else:
                            output.extend(chunk)
                            require(len(output) <= MAX_OUTPUT, 'ADB output quota exceeded')
            code = proc.wait(timeout=max(.01, until - time.monotonic()))
            if check:
                require(code == 0, 'ADB command failed: ' + bytes(output[:2000]).decode('utf-8', 'replace'))
            return bytes(output)
        finally:
            self._terminate(item)
            proc.stdout.close()
            with self._state_lock:
                if item in self._children:
                    self._children.remove(item)

    def _sleep(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self._ensure()
            remaining = until - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(.1, remaining))

    def _assert_listener(self, port, group):
        """Read-only ownership check closes the preflight-bind/startup race."""
        self._ensure()
        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-iTCP:' + str(port), '-sTCP:LISTEN', '-Fp'],
                                capture_output=True, text=True, timeout=5, check=True)
        require(len(result.stdout) <= 8192, 'listener inventory quota')
        pids = sorted(set(re.findall(r'^p([0-9]+)$', result.stdout, re.MULTILINE)))
        require(0 < len(pids) <= 8, 'private listener ownership unavailable')
        owners = []
        for pid in pids:
            output = subprocess.run(['/bin/ps', '-o', 'pgid=', '-p', pid], capture_output=True,
                                    text=True, timeout=5, check=True).stdout.strip()
            require(output.isdigit() and int(output) == group, 'private port belongs to another process group')
            owners.append(int(pid))
        return {'port': port, 'ownedProcessGroup': group, 'listenerPids': owners}

    def _capture(self, label):
        raw = self._adb(['exec-out', 'screencap', '-p'])
        require(raw.startswith(PNG) and len(raw) >= 24 and raw[12:16] == b'IHDR', 'invalid PNG screenshot')
        width, height = int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')
        require((width, height) == self.size, 'screenshot size differs from observed guest size')
        return dict(self._artifact(label + '.png', raw), width=width, height=height, **self._stamp())

    def _crashes(self, label):
        raw = self._adb(['logcat', '-b', 'crash', '-d', '-v', 'threadtime'])
        text = raw.decode('utf-8', 'replace')
        return dict(self._artifact(label + '-crashes.log', raw),
                    fatalMarkers=len(re.findall(r'FATAL EXCEPTION|Fatal signal', text)),
                    scope='entire disposable guest crash buffer; not a gameplay verdict', **self._stamp())

    def _ui(self, label, screenshot):
        """Dump the view tree after its screenshot; an unavailable tree is retained, not fatal."""
        started = self._stamp()
        try:
            # A unique guest path prevents a nominally successful dump command
            # from returning an earlier observation's XML after an idle failure.
            guest_xml = '/sdcard/openclaw-window-%04d.xml' % self._sequence
            self._adb(['shell', 'uiautomator', 'dump', guest_xml], timeout=5)
            raw = self._adb(['exec-out', 'cat', guest_xml], timeout=5)
            require(raw.lstrip().startswith(b'<?xml') or raw.lstrip().startswith(b'<hierarchy'), 'invalid UI XML')
            ui = dict(self._artifact(label + '.xml', raw), dumpStartedAtMs=started['atMs'], **self._stamp())
        except (EmulatorError, subprocess.TimeoutExpired) as error:
            ui = {'status': 'unavailable', 'reason': str(error)[:1000], 'dumpStartedAtMs': started['atMs'], **self._stamp()}
            summary = {'status': 'unavailable', 'reason': 'No view tree was captured; use the screenshot or observe again.'}
        else:
            try:
                summary = summarize_ui(raw, self.size, self.package)
            except Exception as error:  # Advisory text only; never fail the guest action.
                summary = {'status': 'unavailable', 'reason': 'The view tree could not be summarized: ' + str(error)[:300]}
        # The tree and its screenshot are separate captures, never one instant.
        summary['capturedAfterScreenshotMs'] = int(round((ui['monotonic'] - screenshot['monotonic']) * 1000))
        self._ensure()
        return ui, summary

    def _observe(self, label):
        require(isinstance(label, str) and re.fullmatch('[a-zA-Z0-9_-]{1,40}', label), 'invalid observation label')
        require(self._started, 'test not started')
        result = {'action': 'observe', 'label': label, **self._stamp()}
        # Capture first: Canvas animation can keep UI Automator from becoming idle.
        result['screenshot'] = self._capture(label)
        result['ui'], result['uiSummary'] = self._ui(label, result['screenshot'])
        result['packagePids'] = self._adb(['shell', 'pidof', self.package], check=False).decode('utf-8', 'replace').strip()
        require(not result['packagePids'] or re.fullmatch(r'[0-9]+(?: [0-9]+)*', result['packagePids']), 'invalid package PID observation')
        result['crashes'] = self._crashes(label)
        return self._record(result)

    def _configure(self):
        self.run_dir = self.job_dir / ('emulator-' + uuid.uuid4().hex)
        self.run_dir.mkdir(mode=0o700)
        for part in ('avd', 'home', 'tmp', 'android-user', 'emulator-home'):
            (self.run_dir / part).mkdir(mode=0o700)
        avd = self.run_dir / 'avd/offline35.avd'
        avd.mkdir(mode=0o700)
        (self.run_dir / 'avd/offline35.ini').write_text('avd.ini.encoding=UTF-8\npath=' + str(avd) + '\ntarget=android-35\n')
        (avd / 'config.ini').write_text('''AvdId=offline35
avd.ini.encoding=UTF-8
abi.type=arm64-v8a
tag.id=default
image.sysdir.1=system-images/android-35/default/arm64-v8a/
hw.cpu.arch=arm64
hw.cpu.ncore=2
hw.ramSize=2048
hw.keyboard=yes
hw.gpu.enabled=yes
hw.gpu.mode=software
hw.lcd.width=480
hw.lcd.height=800
hw.lcd.density=160
disk.dataPartition.size=2G
vm.heapSize=256
fastboot.forceColdBoot=yes
showDeviceFrame=no
''')
        profiles = {
            'emulator': '(version 1)(allow default)(deny network*)(allow network-inbound (local tcp "localhost:5580") (local tcp "localhost:5581") (local tcp "localhost:5582"))(allow network-outbound (remote tcp "localhost:5038"))',
            'adb': '(version 1)(allow default)(deny network*)(allow network-inbound (local tcp "localhost:5038"))(allow network-outbound (remote tcp "localhost:5581"))'}
        for name, text in profiles.items():
            (self.run_dir / (name + '.sb')).write_text(text + '\n')
        self.env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(self.run_dir / 'home'),
                    'TMPDIR': str(self.run_dir / 'tmp') + '/', 'LANG': 'en_US.UTF-8',
                    'ANDROID_SDK_ROOT': str(SDK), 'ANDROID_HOME': str(SDK),
                    'ANDROID_AVD_HOME': str(self.run_dir / 'avd'), 'ANDROID_USER_HOME': str(self.run_dir / 'android-user'),
                    'ANDROID_EMULATOR_HOME': str(self.run_dir / 'emulator-home'), 'ADB_MDNS_AUTO_CONNECT': '0',
                    'ANDROID_ADB_SERVER_PORT': str(ADB_PORT), 'ADB_SERVER_SOCKET': 'tcp:127.0.0.1:' + str(ADB_PORT)}
        self._receipt.update(runDirectory=str(self.run_dir), networkProfiles=profiles)

    def start(self):
        with self._action_lock:
            require(not self._attempted, 'session may start only once')
            self._attempted = True
            try:
                self._ensure()
                require(self.apk.is_absolute() and self.apk.resolve() == self.apk, 'APK path must be canonical')
                meta = self.apk.lstat()
                require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and 0 < meta.st_size <= MAX_OUTPUT,
                        'APK must be a bounded regular file')
                require(fingerprint(self.apk) == self.expected_sha256, 'APK digest changed')
                for port in (PORT, PORT + 1, ADB_PORT, GRPC_PORT):
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', port))
                self._configure()
                adb = str(SDK / 'platform-tools/adb')
                adb_process = self._spawn(['/usr/bin/sandbox-exec', '-f', str(self.run_dir / 'adb.sb'),
                             adb, '-L', 'tcp:' + str(ADB_PORT), 'server', 'nodaemon'], 'adb', service=True)
                until = min(self.deadline, time.monotonic() + 10)
                while True:
                    self._ensure()
                    with socket.socket() as sock:
                        if sock.connect_ex(('127.0.0.1', ADB_PORT)) == 0:
                            break
                    require(time.monotonic() < until, 'private ADB server startup deadline')
                    self._sleep(.1)
                adb_listener = self._assert_listener(ADB_PORT, adb_process['process'].pid)
                emulator_process = self._spawn(['/usr/bin/sandbox-exec', '-f', str(self.run_dir / 'emulator.sb'),
                             str(SDK / 'emulator/emulator'), '-avd', 'offline35', '-no-window', '-no-audio',
                             '-no-boot-anim', '-no-snapshot', '-no-metrics', '-memory', '2048', '-cores', '2',
                             '-port', str(PORT), '-grpc', str(GRPC_PORT), '-grpc-use-token', '-accel', 'on', '-gpu', 'software'],
                            'emulator', service=True)
                until = min(self.deadline, time.monotonic() + 180)
                while 'Boot completed in ' not in (self.run_dir / 'emulator.log').read_text(errors='replace'):
                    require(time.monotonic() < until, 'guest boot deadline')
                    self._sleep(.5)
                emulator_listener = self._assert_listener(PORT + 1, emulator_process['process'].pid)
                self._adb(['connect', '127.0.0.1:' + str(PORT + 1)], timeout=30)
                while self._adb(['shell', 'getprop', 'sys.boot_completed'], check=False).strip() != b'1':
                    require(time.monotonic() < until, 'guest boot property deadline')
                    self._sleep(.5)
                self._adb(['shell', 'svc', 'wifi', 'disable'])
                self._adb(['shell', 'svc', 'data', 'disable'])
                size = self._adb(['shell', 'wm', 'size']).decode('ascii', 'strict')
                matches = re.findall(r'(?:Physical|Override) size: ([0-9]+)x([0-9]+)', size)
                require(bool(matches), 'guest display size unavailable')
                self.size = tuple(int(x) for x in matches[-1])
                require(all(1 <= x <= 4096 for x in self.size), 'guest display size outside bounds')
                result = {'action': 'start_test', 'display': list(self.size),
                          'listeners': [adb_listener, emulator_listener], 'beforeLaunchCrashes': self._crashes('before-launch'),
                          'guestRoutes': self._adb(['shell', 'ip', 'route']).decode('utf-8', 'replace'), **self._stamp()}
                self._record({'action': 'before_launch', 'crashes': result['beforeLaunchCrashes'], **self._stamp()})
                self._record(result)
                # Recheck immediately before handing the private captured artifact to ADB.
                require(fingerprint(self.apk) == self.expected_sha256, 'APK digest changed before install')
                result['install'] = self._adb(['install', str(self.apk)], timeout=30).decode('utf-8', 'replace')
                self._flush()
                require('Success' in result['install'], 'APK installation unconfirmed')
                self._adb(['shell', 'input', 'keyevent', '82'])
                result['launch'] = self._adb(['shell', 'am', 'start', '-W', '-n', self.package + '/' + self.activity], timeout=30).decode('utf-8', 'replace')
                self._flush()
                require('Status: ok' in result['launch'], 'activity launch unconfirmed')
                self._started = True
                self._receipt['status'] = 'observing'
                result['initialObservation'] = self._observe('launch')
                self._flush()
                return result
            except BaseException as error:
                self._receipt['error'] = str(error)[:2000]
                if self.run_dir and len(self._services) == 2 and not self._stopping:
                    try:
                        self._record({'action': 'startup_failure', 'crashes': self._crashes('startup-failure'), **self._stamp()})
                    except Exception as capture_error:
                        self._receipt['failureCaptureError'] = str(capture_error)[:1000]
                self.stop()
                raise

    def observe(self, label='frame'):
        with self._action_lock:
            try:
                self._ensure()
                return self._observe(label)
            except BaseException:
                self.stop()
                raise

    def tap(self, x, y, count=1, interval_ms=300):
        # Validate before any action; booleans are not coordinates or counts.
        require(self._started and self.size is not None, 'test not started')
        require(type(x) is int and type(y) is int and 0 <= x < self.size[0] and 0 <= y < self.size[1],
                'tap outside observed display')
        require(type(count) is int and 1 <= count <= 30, 'tap count outside bounds')
        require(type(interval_ms) is int and 80 <= interval_ms <= 1500 and (count - 1) * interval_ms <= 15000,
                'tap interval or burst outside bounds')
        with self._action_lock:
            try:
                self._ensure()
                result = {'action': 'tap', 'x': x, 'y': y, 'count': count, 'intervalMs': interval_ms,
                          'before': self._capture('tap-before'), 'taps': [], 'intermediateFrames': [], 'status': 'running'}
                self._record(result)
                started = time.monotonic()
                previous_tap = None
                for index in range(count):
                    target = started + index * interval_ms / 1000
                    if previous_tap is not None:
                        target = max(target, previous_tap + interval_ms / 1000)
                    self._sleep(max(0, target - time.monotonic()))
                    require(time.monotonic() - started <= 15, 'actual tap burst deadline')
                    actual = self._stamp()
                    tap_record = dict(index=index, scheduledMonotonic=target, status='dispatched', **actual)
                    result['taps'].append(tap_record)
                    self._flush()
                    self._adb(['shell', 'input', 'tap', str(x), str(y)], timeout=3)
                    tap_record.update(status='returned', returnedAtMs=int(time.time() * 1000))
                    self._flush()
                    require(time.monotonic() - started <= 15, 'actual tap burst deadline')
                    previous_tap = actual['monotonic']
                    if count >= 4 and index in {count // 3, 2 * count // 3}:
                        result['intermediateFrames'].append(self._capture('tap-intermediate'))
                result['after'] = self._capture('tap-after')
                result['afterUi'], result['afterUiSummary'] = self._ui('tap-after', result['after'])
                result['status'] = 'returned'
                self._flush()
                return result
            except BaseException as error:
                if 'result' in locals():
                    result.update(status='interrupted', error=str(error)[:1000])
                    self._flush()
                self.stop()
                raise

    def extend_deadline(self, deadline):
        """Accept a controller-granted later deadline; it can never move earlier or revive a stop."""
        with self._state_lock:
            require(not self._stopping, 'emulator stopped')
            require(type(deadline) in (int, float) and time.monotonic() < self.deadline < deadline < float('inf'),
                    'emulator deadline may only move later before it expires')
            self.deadline = deadline
            self._receipt.setdefault('deadlineExtensions', []).append({'deadline': deadline, **self._stamp()})
            self._flush()
            return deadline

    def stop(self):
        """Stop only Popen handles created by this instance; idempotent and watchdog-safe."""
        with self._stop_lock:
            if self._stop_receipt is not None:
                return self._stop_receipt
            with self._state_lock:
                self._stopping = True
                children = list(reversed(self._children))
            result = {'action': 'stop', 'ownedProcesses': [], 'errors': [], **self._stamp()}
            for item in children:
                proc = item['process']
                entry = {'name': item['name'], 'pid': proc.pid}
                try:
                    self._terminate(item)
                    entry['exitCode'] = proc.returncode
                    if item['stream']:
                        item['stream'].close()
                    if item['log']:
                        entry.update(log=str(item['log']), logBytes=item['log'].stat().st_size,
                                     logSha256=fingerprint(item['log']))
                except Exception as error:
                    result['errors'].append(item['name'] + ': ' + str(error)[:1000])
                result['ownedProcesses'].append(entry)
            # A reaped leader is not proof that all of its descendants exited.
            groups = {item['process'].pid for item in children}
            result['survivingProcessGroups'] = []
            result['groupObservations'] = []
            if groups:
                grace_until = time.monotonic() + STOP_GROUP_GRACE_SECONDS
                for attempt in range(STOP_GROUP_MAX_OBSERVATIONS):
                    observation = {'attempt': attempt + 1, **self._stamp()}
                    result['groupObservations'].append(observation)
                    remaining = grace_until - time.monotonic()
                    if remaining <= 0:
                        observation['error'] = 'process-group observation grace expired before recheck'
                        result['errors'].append(observation['error'])
                        break
                    try:
                        listing = subprocess.run(['/bin/ps', '-axo', 'pgid=,pid='], capture_output=True,
                                                 text=True, timeout=remaining, check=True).stdout
                        survivors = [line.strip() for line in listing.splitlines()
                            if line.strip() and int(line.split()[0]) in groups]
                        observation['survivingProcessGroups'] = survivors
                        result['survivingProcessGroups'] = survivors
                    except Exception as error:
                        observation['error'] = 'process-group observation: ' + str(error)[:1000]
                        result['errors'].append(observation['error'])
                        break
                    finally:
                        observation['finishedAtMs'] = int(time.time() * 1000)
                    if not survivors:
                        break
                    remaining = grace_until - time.monotonic()
                    if remaining <= 0 or attempt + 1 == STOP_GROUP_MAX_OBSERVATIONS:
                        break
                    time.sleep(min(.1, remaining))
            result['status'] = 'stopped' if not result['errors'] and not result['survivingProcessGroups'] else 'cleanup-unconfirmed'
            self._receipt['status'] = result['status']
            self._stop_receipt = self._record(result)
            return self._stop_receipt
