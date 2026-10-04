"""Fake-process boundary tests. No Docker, SDK executable, or guest is started."""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

import emulator_adapter as adapter


def png(width=480, height=800):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (adapter.PNG + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * width * 3) * height)) + chunk(b'IEND', b''))


def node(text='', bounds='[0,0][480,800]', cls='android.widget.TextView', clickable='false', package='org.openclaw.trial',
         desc='', children='', **extra):
    attributes = dict(index='0', text=text, **{'resource-id': ''}, **{'class': cls}, package=package,
                      **{'content-desc': desc}, checkable='false', checked='false', clickable=clickable,
                      enabled='true', focusable=clickable, focused='false', scrollable='false',
                      **{'long-clickable': 'false'}, password='false', selected='false', bounds=bounds)
    attributes.update(extra)
    rendered = ' '.join('%s="%s"' % (key, value) for key, value in attributes.items())
    return '<node %s>%s</node>' % (rendered, children) if children else '<node %s />' % rendered


def hierarchy(*children):
    body = "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\">%s</hierarchy>" % ''.join(children)
    return body.encode()


# Shape of an actual uiautomator dump for a native-view counter at 480x800.
COUNTER = hierarchy(node(cls='android.widget.FrameLayout', children=node(
    cls='android.widget.LinearLayout', bounds='[0,24][480,800]', children=''.join((
        node('0', '[0,200][480,264]'),
        node('Increment', '[160,300][320,348]', 'android.widget.Button', 'true'),
        node('Reset', '[160,360][320,408]', 'android.widget.Button', 'true'))))))


class Clock:
    def __init__(self):
        self.value = 100.0

    def now(self):
        return self.value


class Socket:
    occupied = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def bind(self, address):
        if address[1] == self.occupied:
            raise OSError('address already in use')

    def connect_ex(self, address):
        return 0


class Process:
    next_pid = 40000

    def __init__(self):
        Process.next_pid += 1
        self.pid = Process.next_pid
        self.returncode = None

    def poll(self):
        return self.returncode


class Session(adapter.EmulatorSession):
    def __init__(self, *args, clock, **kwargs):
        self.clock = clock
        self.commands = []
        self.launches = []
        self.terminated = []
        self.ui_failure = False
        self.startup_crash = False
        self.launch_failure = False
        self.app_launched = False
        self.block_tap = False
        self.tap_entered = threading.Event()
        self.tap_released = threading.Event()
        self.slow_tap = 0
        self.ui_xml = b'<?xml version="1.0"?><hierarchy/>'
        super().__init__(*args, **kwargs)

    def _spawn(self, command, name, service=False):
        self._ensure()
        self.launches.append(command)
        proc = Process()
        logfile = self.run_dir / (name + '.log') if service else None
        if logfile:
            logfile.write_text('Boot completed in 100 ms' if name == 'emulator' else 'private adb ready')
        item = {'process': proc, 'name': name, 'stream': None, 'log': logfile}
        self._children.append(item)
        if service:
            self._services.append(item)
        return item

    def _terminate(self, item):
        self.terminated.append(item['process'].pid)
        item['process'].returncode = -15
        self.tap_released.set()

    def _sleep(self, seconds):
        self._ensure()
        self.clock.value += seconds
        self._ensure()

    def _assert_listener(self, port, group):
        return {'port': port, 'ownedProcessGroup': group, 'listenerPids': [group]}

    def _adb(self, args, timeout=15, check=True):
        self._ensure()
        self.commands.append(list(args))
        if args[:3] == ['shell', 'getprop', 'sys.boot_completed']:
            return b'1\n'
        if args == ['shell', 'wm', 'size']:
            return b'Physical size: 480x800\n'
        if args[:2] == ['logcat', '-b']:
            return b'FATAL EXCEPTION: main\nProcess: org.openclaw.trial\n' if self.app_launched and self.startup_crash else b''
        if args[0] == 'install':
            return b'Success\n'
        if args[:3] == ['shell', 'am', 'start']:
            self.app_launched = True
            return b'Error: Activity crashed\n' if self.launch_failure else b'Status: ok\n'
        if args[:3] == ['exec-out', 'screencap', '-p']:
            return png()
        if args[:3] == ['shell', 'uiautomator', 'dump']:
            if self.ui_failure:
                raise adapter.EmulatorError('ADB command deadline exceeded')
            return b'UI hierarchy dumped\n'
        if args[:2] == ['exec-out', 'cat']:
            self.clock.value += .25  # A tree dump completes after its screenshot.
            return self.ui_xml
        if args[:2] == ['shell', 'pidof']:
            return b'' if self.startup_crash else b'321\n'
        if args[:3] == ['shell', 'input', 'tap']:
            self.tap_entered.set()
            if self.block_tap:
                self.tap_released.wait(timeout=2)
                self._ensure()
            self.clock.value += self.slow_tap
            return b''
        return b''


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.job = Path(self.directory.name).resolve()
        self.job.chmod(0o700)
        self.apk = self.job / 'app.apk'
        self.apk.write_bytes(b'controller-captured-apk')
        self.digest = hashlib.sha256(self.apk.read_bytes()).hexdigest()
        self.clock = Clock()
        self.patchers = [patch.object(adapter.time, 'monotonic', self.clock.now),
                         patch.object(adapter.socket, 'socket', Socket),
                         patch.object(adapter.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '1 1\n', ''))]
        for patcher in self.patchers:
            patcher.start()
        Socket.occupied = None
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.stop()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.directory.cleanup()

    def session(self, **kwargs):
        session = Session(self.job, self.apk, self.digest, deadline=self.clock.now() + 600,
                          clock=self.clock, **kwargs)
        self.sessions.append(session)
        return session

    def test_start_receipt_uses_fixed_sdk_ports_and_private_paths(self):
        session = self.session()
        receipt = session.start()
        self.assertEqual(receipt['display'], [480, 800])
        self.assertEqual(len(session.launches), 2)
        self.assertIn(str(adapter.SDK / 'emulator/emulator'), session.launches[1])
        self.assertIn('-no-snapshot', session.launches[1])
        self.assertIn('-grpc-use-token', session.launches[1])
        self.assertEqual(session.env['ADB_SERVER_SOCKET'], 'tcp:127.0.0.1:5038')
        path = Path(receipt['initialObservation']['screenshot']['path'])
        self.assertTrue(path.is_relative_to(self.job))
        self.assertEqual(path.read_bytes(), png())
        self.assertNotIn('passed', json.dumps(receipt))

    def test_real_sleep_skips_wait_consumed_by_health_check(self):
        session = self.session()
        ensure = session._ensure
        def slow_health_check():
            ensure()
            self.clock.value += .02
        with patch.object(session, '_ensure', side_effect=slow_health_check) as checked, patch.object(adapter.time, 'sleep') as slept:
            # Invoke the production helper, not Session's fake-clock override.
            adapter.EmulatorSession._sleep(session, .01)
        checked.assert_called_once()
        slept.assert_not_called()

    def test_real_sleep_keeps_waits_positive_and_bounded(self):
        session = self.session()
        durations = []
        def sleep(seconds):
            self.assertGreater(seconds, 0)
            self.assertLessEqual(seconds, .1)
            durations.append(seconds)
            self.clock.value += seconds
        with patch.object(adapter.time, 'sleep', side_effect=sleep):
            adapter.EmulatorSession._sleep(session, .25)
        self.assertAlmostEqual(sum(durations), .25)
        self.assertAlmostEqual(self.clock.value, 100.25)

    def test_real_sleep_still_checks_cancellation_deadline_and_service_exit(self):
        for failure in ('cancelled', 'deadline', 'service'):
            self.clock.value = 100.0
            session = self.session()
            service = Process()
            logfile = self.job / ('sleep-' + failure + '.log')
            logfile.write_text('healthy')
            session._services.append({'process': service, 'name': 'guest', 'log': logfile})
            if failure == 'deadline':
                session.deadline = 100.05
            def sleep(seconds):
                self.clock.value += seconds
                if failure == 'cancelled': session._stopping = True
                if failure == 'service': service.returncode = 1
            expected = {'cancelled': 'emulator stopped', 'deadline': 'deadline exceeded', 'service': 'owned service exited'}[failure]
            with self.subTest(failure=failure), patch.object(adapter.time, 'sleep', side_effect=sleep) as slept:
                with self.assertRaisesRegex(adapter.EmulatorError, expected):
                    adapter.EmulatorSession._sleep(session, .5)
                slept.assert_called_once_with(.1)

    def test_invalid_coordinates_counts_and_bursts_have_no_adb_side_effect(self):
        session = self.session()
        session.start()
        original = len(session.commands)
        bad = [(-1, 1, 1, 300), (480, 1, 1, 300), (1, 800, 1, 300), (True, 1, 1, 300),
               (1.5, 1, 1, 300), (1, 1, 0, 300), (1, 1, 31, 300), (1, 1, True, 300),
               (1, 1, 1, 79), (1, 1, 1, 1501), (1, 1, 30, 1500)]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(adapter.EmulatorError):
                session.tap(*args)
        self.assertEqual(len(session.commands), original)

    def test_tap_burst_records_order_timing_and_intermediate_frames(self):
        session = self.session()
        session.start()
        receipt = session.tap(240, 400, count=6, interval_ms=300)
        stamps = [item['monotonic'] for item in receipt['taps']]
        self.assertEqual(len(stamps), 6)
        self.assertTrue(all(b - a >= .299 for a, b in zip(stamps, stamps[1:])))
        self.assertEqual(len(receipt['intermediateFrames']), 2)
        self.assertTrue(Path(receipt['before']['path']).exists())
        self.assertTrue(Path(receipt['after']['path']).exists())
        self.assertEqual(sum(command[:3] == ['shell', 'input', 'tap'] for command in session.commands), 6)

    def test_canvas_ui_dump_failure_keeps_screenshot_and_crash_receipt(self):
        session = self.session()
        session.ui_failure = True
        receipt = session.start()['initialObservation']
        self.assertEqual(receipt['ui']['status'], 'unavailable')
        self.assertTrue(Path(receipt['screenshot']['path']).exists())
        self.assertTrue(Path(receipt['crashes']['path']).exists())
        self.assertFalse(session._stopping)

    def test_startup_crash_is_retained_without_any_log_clear(self):
        session = self.session()
        session.startup_crash = True
        receipt = session.start()
        self.assertEqual(receipt['beforeLaunchCrashes']['fatalMarkers'], 0)
        self.assertEqual(receipt['initialObservation']['crashes']['fatalMarkers'], 1)
        self.assertEqual(receipt['initialObservation']['packagePids'], '')
        self.assertFalse(any(command[0] == 'logcat' and '-c' in command for command in session.commands))
        crash_index = next(i for i, c in enumerate(session.commands) if c[0] == 'logcat')
        launch_index = next(i for i, c in enumerate(session.commands) if c[:3] == ['shell', 'am', 'start'])
        self.assertLess(crash_index, launch_index)

    def test_failed_activity_launch_retains_crashes_and_stops(self):
        session = self.session()
        session.startup_crash = session.launch_failure = True
        with self.assertRaisesRegex(adapter.EmulatorError, 'launch unconfirmed'):
            session.start()
        captures = [item for item in session._receipt['actions'] if item['action'] == 'startup_failure']
        self.assertEqual(captures[0]['crashes']['fatalMarkers'], 1)
        self.assertEqual(session.stop()['status'], 'stopped')

    def test_changed_apk_rejected_before_process_or_install(self):
        session = self.session()
        self.apk.write_bytes(b'changed')
        with self.assertRaisesRegex(adapter.EmulatorError, 'digest changed'):
            session.start()
        self.assertEqual(session.launches, [])
        self.assertEqual(session.commands, [])

    def test_symlink_apk_rejected_before_process_or_install(self):
        session = self.session()
        target = self.job / 'other.apk'
        target.write_bytes(self.apk.read_bytes())
        self.apk.unlink()
        self.apk.symlink_to(target)
        with self.assertRaisesRegex(adapter.EmulatorError, 'canonical'):
            session.start()
        self.assertEqual(session.launches, [])

    def test_occupied_private_port_does_not_reuse_existing_service(self):
        session = self.session()
        Socket.occupied = adapter.ADB_PORT
        with self.assertRaises(OSError):
            session.start()
        self.assertEqual(session.launches, [])
        self.assertEqual(session.commands, [])

    def test_absolute_deadline_stops_owned_processes(self):
        session = self.session()
        session.start()
        owned = {item['process'].pid for item in session._children}
        self.clock.value = session.deadline + 1
        with self.assertRaisesRegex(adapter.EmulatorError, 'deadline'):
            session.observe()
        self.assertEqual(set(session.terminated), owned)
        self.assertEqual(session.stop()['status'], 'stopped')

    def test_actual_slow_tap_burst_cannot_exceed_fifteen_seconds(self):
        session = self.session()
        session.start()
        session.slow_tap = 6
        with self.assertRaisesRegex(adapter.EmulatorError, 'actual tap burst deadline'):
            session.tap(1, 1, count=5, interval_ms=100)
        self.assertEqual(sum(c[:3] == ['shell', 'input', 'tap'] for c in session.commands), 3)
        self.assertEqual(session.stop()['status'], 'stopped')
        journal = json.loads((session.run_dir / 'session.json').read_text())
        action = next(item for item in journal['actions'] if item['action'] == 'tap')
        self.assertEqual(action['status'], 'interrupted')
        self.assertEqual(len(action['taps']), 3)

    def test_watchdog_stop_interrupts_action_without_waiting_on_action_lock(self):
        session = self.session()
        session.start()
        session.block_tap = True
        errors = []
        def action():
            try:
                session.tap(2, 2)
            except adapter.EmulatorError as error:
                errors.append(str(error))
        thread = threading.Thread(target=action)
        thread.start()
        self.assertTrue(session.tap_entered.wait(timeout=2))
        receipt = session.stop()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(receipt['status'], 'stopped')
        self.assertEqual(errors, ['emulator stopped'])
        journal = json.loads((session.run_dir / 'session.json').read_text())
        action = next(item for item in journal['actions'] if item['action'] == 'tap')
        self.assertEqual(action['status'], 'interrupted')
        self.assertEqual(action['taps'][0]['status'], 'dispatched')

    def test_granted_deadline_extension_keeps_guest_running_past_old_deadline(self):
        session = self.session()
        session.start()
        old = session.deadline
        self.assertEqual(session.extend_deadline(old + 900), old + 900)
        self.clock.value = old + 1
        self.assertEqual(session.observe('extended')['action'], 'observe')
        journal = json.loads((session.run_dir / 'session.json').read_text())
        self.assertEqual(journal['deadlineExtensions'][0]['deadline'], old + 900)
        self.clock.value = old + 901
        with self.assertRaisesRegex(adapter.EmulatorError, 'deadline'):
            session.observe('expired')
        self.assertEqual(session.stop()['status'], 'stopped')

    def test_deadline_cannot_move_earlier_revive_or_extend_after_expiry(self):
        session = self.session()
        session.start()
        old = session.deadline
        for value in (old, old - 1, True, float('inf'), float('nan'), '700'):
            with self.subTest(value=value), self.assertRaises(adapter.EmulatorError):
                session.extend_deadline(value)
        self.assertEqual(session.deadline, old)
        self.clock.value = old
        with self.assertRaisesRegex(adapter.EmulatorError, 'before it expires'):
            session.extend_deadline(old + 60)
        self.clock.value = 100.0
        session.stop()
        with self.assertRaisesRegex(adapter.EmulatorError, 'emulator stopped'):
            session.extend_deadline(old + 60)
        self.assertEqual(session.deadline, old)

    def test_stop_is_idempotent_and_never_uses_disk_pid_as_authority(self):
        session = self.session()
        session.start()
        (session.run_dir / 'forged-process.json').write_text('{"pid":1}')
        first = session.stop()
        calls = list(session.terminated)
        self.assertEqual(first, session.stop())
        self.assertEqual(calls, session.terminated)
        self.assertNotIn(1, session.terminated)

    def test_surviving_owned_group_keeps_cleanup_unconfirmed(self):
        session = self.session()
        session.start()
        pid = session._children[0]['process'].pid
        survivor = str(pid) + ' 99999'
        started = self.clock.now()
        def advance(seconds):
            self.assertGreater(seconds, 0)
            self.assertLessEqual(seconds, .1)
            self.clock.value += seconds
        with patch.object(adapter.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, survivor + '\n', '')) as observed, patch.object(adapter.time, 'sleep', side_effect=advance):
            receipt = session.stop()
            self.assertEqual(receipt['status'], 'cleanup-unconfirmed')
            self.assertEqual(receipt['survivingProcessGroups'], [survivor])
            self.assertGreater(observed.call_count, 1)
            self.assertLessEqual(observed.call_count, adapter.STOP_GROUP_MAX_OBSERVATIONS)
            self.assertLessEqual(self.clock.now() - started, adapter.STOP_GROUP_GRACE_SECONDS + .000001)
            calls = observed.call_count
            self.assertIs(session.stop(), receipt)
            self.assertEqual(observed.call_count, calls, 'cached cleanup must not adopt a later process group')
        self.assertNotIn(99999, session.terminated)

    def test_transient_owned_descendant_disappears_during_bounded_grace(self):
        session = self.session()
        session.start()
        owned = {item['process'].pid for item in session._children}
        survivor = str(session._children[0]['process'].pid) + ' 99999'
        results = [subprocess.CompletedProcess([], 0, survivor + '\n777 778\n', ''),
                   subprocess.CompletedProcess([], 0, '777 778\n', '')]
        def advance(seconds):
            self.clock.value += seconds
        with patch.object(adapter.subprocess, 'run', side_effect=results) as observed, patch.object(adapter.time, 'sleep', side_effect=advance):
            receipt = session.stop()
        self.assertEqual(receipt['status'], 'stopped')
        self.assertEqual(receipt['errors'], [])
        self.assertEqual(receipt['survivingProcessGroups'], [])
        self.assertEqual(observed.call_count, 2)
        first, second = receipt['groupObservations']
        self.assertEqual(first['survivingProcessGroups'], [survivor])
        self.assertEqual(second['survivingProcessGroups'], [])
        self.assertEqual([first['attempt'], second['attempt']], [1, 2])
        self.assertLess(first['monotonic'], second['monotonic'])
        self.assertTrue(all('atMs' in item and 'finishedAtMs' in item for item in (first, second)))
        self.assertEqual(set(session.terminated), owned)
        self.assertNotIn(99999, session.terminated)

    def test_group_observation_failure_preserves_survivor_and_cannot_confirm_cleanup(self):
        session = self.session()
        session.start()
        survivor = str(session._children[0]['process'].pid) + ' 99999'
        results = [subprocess.CompletedProcess([], 0, survivor + '\n', ''),
                   subprocess.TimeoutExpired(['ps'], .1)]
        def advance(seconds):
            self.clock.value += seconds
        with patch.object(adapter.subprocess, 'run', side_effect=results) as observed, patch.object(adapter.time, 'sleep', side_effect=advance):
            receipt = session.stop()
        self.assertEqual(receipt['status'], 'cleanup-unconfirmed')
        self.assertEqual(receipt['survivingProcessGroups'], [survivor])
        self.assertEqual(observed.call_count, 2)
        self.assertEqual(len(receipt['errors']), 1)
        self.assertIn('process-group observation', receipt['errors'][0])
        self.assertEqual(receipt['groupObservations'][0]['survivingProcessGroups'], [survivor])
        self.assertIn('error', receipt['groupObservations'][1])
        self.assertTrue(all(0 < call.kwargs['timeout'] <= adapter.STOP_GROUP_GRACE_SECONDS for call in observed.call_args_list))

    def test_no_restart_after_stop_or_failed_start(self):
        session = self.session()
        session.start()
        session.stop()
        with self.assertRaisesRegex(adapter.EmulatorError, 'only once'):
            session.start()

    def test_invalid_constructor_package_activity_or_deadline(self):
        for kwargs in ({'package': 'org.example; id'}, {'activity': '.MainActivity;id'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(adapter.EmulatorError):
                self.session(**kwargs)
        with self.assertRaises(adapter.EmulatorError):
            Session(self.job, self.apk, self.digest, deadline=99, clock=self.clock)

    def test_untrusted_label_cannot_create_path(self):
        session = self.session()
        session.start()
        with self.assertRaisesRegex(adapter.EmulatorError, 'invalid observation label'):
            session.observe('../../outside')
        self.assertFalse((self.job / 'outside.png').exists())

    def test_artifact_budget_rejects_additional_capture(self):
        session = self.session()
        session.start()
        session._artifact_bytes = 64 * 1024 * 1024
        with self.assertRaisesRegex(adapter.EmulatorError, 'session artifact quota'):
            session.observe()
        self.assertEqual(session.stop()['status'], 'stopped')

    def test_observations_never_reuse_guest_xml_path(self):
        session = self.session()
        session.start()
        session.observe('second')
        paths = [c[3] for c in session.commands if c[:3] == ['shell', 'uiautomator', 'dump']]
        self.assertEqual(len(paths), 2)
        self.assertEqual(len(set(paths)), 2)

    def test_private_adb_runner_bounds_output_and_reaps_its_handle(self):
        session = self.session()
        session.start()
        readfd, writefd = os.pipe()
        os.write(writefd, b'x' * 64)
        os.close(writefd)
        proc = Process()
        proc.stdout = os.fdopen(readfd, 'rb')
        proc.wait = lambda timeout: 0
        item = {'process': proc, 'name': 'adb-call', 'stream': None, 'log': None}
        session._children.append(item)
        with patch.object(session, '_spawn', return_value=item), patch.object(adapter, 'MAX_OUTPUT', 32):
            with self.assertRaisesRegex(adapter.EmulatorError, 'ADB output quota'):
                adapter.EmulatorSession._adb(session, ['shell', 'wm', 'size'])
        self.assertTrue(proc.stdout.closed)
        self.assertIn(proc.pid, session.terminated)
        self.assertNotIn(item, session._children)

    def test_private_adb_runner_deadline_reaps_only_its_handle(self):
        session = self.session()
        session.start()
        readfd, writefd = os.pipe()
        proc = Process()
        proc.stdout = os.fdopen(readfd, 'rb')
        proc.wait = lambda timeout: 0
        item = {'process': proc, 'name': 'adb-call', 'stream': None, 'log': None}
        session._children.append(item)
        clock = self.clock
        class NoOutput:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def register(self, *args): pass
            def get_map(self): return {1: 1}
            def select(self, timeout):
                clock.value += 2
                return []
        try:
            with patch.object(session, '_spawn', return_value=item), patch.object(adapter.selectors, 'DefaultSelector', NoOutput):
                with self.assertRaisesRegex(adapter.EmulatorError, 'ADB command deadline'):
                    adapter.EmulatorSession._adb(session, ['shell', 'wm', 'size'], timeout=1)
        finally:
            os.close(writefd)
        self.assertTrue(proc.stdout.closed)
        self.assertIn(proc.pid, session.terminated)
        self.assertNotIn(item, session._children)
        self.assertTrue(all(service['process'].poll() is None for service in session._services))

    def test_observation_summarizes_visible_text_controls_and_actual_pixel_centers(self):
        session = self.session()
        session.ui_xml = COUNTER
        launch = session.start()['initialObservation']
        summary = launch['uiSummary']
        self.assertEqual(summary['status'], 'summarized')
        self.assertEqual(summary['display'], [480, 800])
        self.assertTrue(summary['appPackagePresent'])
        self.assertEqual(summary['packages'], ['org.openclaw.trial'])
        self.assertEqual(summary['nodeCount'], 5)
        by_text = {item.get('text'): item for item in summary['elements']}
        self.assertEqual(set(by_text), {'0', 'Increment', 'Reset'})
        self.assertEqual(by_text['Increment']['center'], [240, 324])
        self.assertEqual(by_text['Reset']['center'], [240, 384])
        self.assertEqual(by_text['Increment']['bounds'], [160, 300, 320, 348])
        self.assertTrue(by_text['Increment']['clickable'])
        self.assertNotIn('clickable', by_text['0'])
        self.assertTrue(all(item['centerOnDisplay'] for item in summary['elements']))
        self.assertNotIn('package', by_text['0'], 'app-owned elements omit the repeated package name')
        # Separate captures are timed separately and never presented as one instant.
        self.assertEqual(summary['capturedAfterScreenshotMs'], 250)
        self.assertGreater(launch['ui']['monotonic'], launch['screenshot']['monotonic'])
        self.assertIn('dumpStartedAtMs', launch['ui'])
        self.assertEqual(Path(launch['ui']['path']).read_bytes(), COUNTER)
        self.assertNotIn('passed', json.dumps(summary))

    def test_tap_receipt_includes_tree_captured_after_its_after_frame(self):
        session = self.session()
        session.start()
        session.ui_xml = hierarchy(node('1', '[0,200][480,264]'))
        receipt = session.tap(240, 324)
        self.assertEqual(receipt['status'], 'returned')
        self.assertEqual(receipt['afterUiSummary']['elements'][0]['text'], '1')
        self.assertTrue(Path(receipt['afterUi']['path']).name.endswith('tap-after.xml'))
        self.assertGreater(receipt['afterUi']['monotonic'], receipt['after']['monotonic'])
        dumps = [c for c in session.commands if c[:3] == ['shell', 'uiautomator', 'dump']]
        self.assertEqual(len(dumps), 2)
        self.assertEqual(len({c[3] for c in dumps}), 2)

    def test_unavailable_tree_after_tap_keeps_guest_and_tap_receipt(self):
        session = self.session()
        session.start()
        session.ui_failure = True
        receipt = session.tap(240, 324)
        self.assertEqual(receipt['status'], 'returned')
        self.assertEqual(receipt['afterUi']['status'], 'unavailable')
        self.assertEqual(receipt['afterUiSummary']['status'], 'unavailable')
        self.assertTrue(Path(receipt['after']['path']).exists())
        self.assertFalse(session._stopping)

    def test_unsafe_or_malformed_trees_are_retained_but_not_summarized(self):
        deep = b'<hierarchy rotation="0">' + b'<node>' * (adapter.UI_DEPTH_LIMIT + 1) + b'</node>' * (adapter.UI_DEPTH_LIMIT + 1) + b'</hierarchy>'
        cases = {
            'entity': b'<?xml version="1.0"?><!DOCTYPE h [<!ENTITY a "aaaa">]><hierarchy>&a;</hierarchy>',
            'malformed': b'<?xml version="1.0"?><hierarchy><node text="0"></hierarchy>',
            'depth': deep,
            'nodes': b'<hierarchy>' + b'<node />' * (adapter.UI_NODE_LIMIT + 1) + b'</hierarchy>',
            'root': b'<?xml version="1.0"?><other><node text="0" /></other>',
            'element': b'<hierarchy><script /></hierarchy>',
        }
        for name, raw in cases.items():
            session = self.session()
            session.start()
            session.ui_xml = raw
            receipt = session.observe('case')
            with self.subTest(case=name):
                self.assertEqual(receipt['uiSummary']['status'], 'unavailable')
                self.assertNotIn('elements', receipt['uiSummary'])
                self.assertEqual(Path(receipt['ui']['path']).read_bytes(), raw)
                self.assertFalse(session._stopping)
            session.stop()

    def test_summary_bounds_text_and_reports_omitted_or_offscreen_controls(self):
        many = [node('Item %d' % i, '[0,%d][480,%d]' % (i, i + 1)) for i in range(adapter.UI_ELEMENT_LIMIT + 5)]
        raw = hierarchy(
            node('A&#9;B‮&#10;' + 'x' * 400, '[0,0][480,40]'),
            node('', '[0,0][0,0]', clickable='true'),
            node('', '[600,900][700,1000]', 'android.widget.Button', 'true', desc='Offscreen'),
            node('Clock', '[0,0][100,24]', package='com.android.systemui'),
            node('', '[0,0][480,800]', cls='bad class;name'),
            *many)
        summary = adapter.summarize_ui(raw, (480, 800), 'org.openclaw.trial')
        first = summary['elements'][0]
        self.assertTrue(first['text'].startswith('A B '))
        self.assertEqual(len(first['text']), adapter.UI_TEXT_LIMIT)
        self.assertTrue(all(ch.isprintable() for item in summary['elements'] for ch in item.get('text', '')))
        offscreen = summary['elements'][1]
        self.assertEqual(offscreen['contentDescription'], 'Offscreen')
        self.assertFalse(offscreen['centerOnDisplay'])
        self.assertEqual(summary['elements'][2]['package'], 'com.android.systemui')
        self.assertEqual(len(summary['elements']), adapter.UI_ELEMENT_LIMIT)
        self.assertEqual(summary['elementsOmitted'], 8)
        self.assertNotIn([0, 0, 0, 0], [item['bounds'] for item in summary['elements']])
        self.assertTrue(summary['appPackagePresent'])
        self.assertLess(len(json.dumps(summary)), 32 * 1024)

    def test_app_package_presence_is_not_limited_by_package_listing(self):
        others = [node('x', package='com.example.p%d' % i) for i in range(10)]
        summary = adapter.summarize_ui(hierarchy(*others, node('0')), (480, 800), 'org.openclaw.trial')
        self.assertEqual(len(summary['packages']), 8)
        self.assertNotIn('org.openclaw.trial', summary['packages'])
        self.assertTrue(summary['appPackagePresent'])
        absent = adapter.summarize_ui(hierarchy(*others), (480, 800), 'org.openclaw.trial')
        self.assertFalse(absent['appPackagePresent'])

    def test_oversized_tree_is_refused_before_parsing(self):
        with patch.object(adapter.ElementTree, 'XMLPullParser', side_effect=AssertionError('parsed')):
            with self.assertRaisesRegex(adapter.EmulatorError, 'byte limit'):
                adapter.summarize_ui(b' ' * (adapter.UI_TREE_LIMIT + 1), (480, 800), 'org.openclaw.trial')

    def test_listener_inventory_rejects_an_unrelated_process_group(self):
        session = self.session()
        session.start()
        outputs = [subprocess.CompletedProcess([], 0, 'p123\n', ''), subprocess.CompletedProcess([], 0, '999\n', '')]
        with patch.object(adapter.subprocess, 'run', side_effect=outputs):
            with self.assertRaisesRegex(adapter.EmulatorError, 'another process group'):
                adapter.EmulatorSession._assert_listener(session, adapter.ADB_PORT, 456)

    def test_listener_inventory_accepts_only_owned_process_group(self):
        session = self.session()
        session.start()
        outputs = [subprocess.CompletedProcess([], 0, 'p123\np124\n', ''),
                   subprocess.CompletedProcess([], 0, '456\n', ''), subprocess.CompletedProcess([], 0, '456\n', '')]
        with patch.object(adapter.subprocess, 'run', side_effect=outputs):
            result = adapter.EmulatorSession._assert_listener(session, adapter.ADB_PORT, 456)
        self.assertEqual(result['listenerPids'], [123, 124])

    def test_listener_race_stops_own_service_without_sending_adb(self):
        session = self.session()
        with patch.object(session, '_assert_listener', side_effect=adapter.EmulatorError('private port belongs to another process group')):
            with self.assertRaises(adapter.EmulatorError):
                session.start()
        self.assertEqual(len(session.launches), 1)
        self.assertEqual(session.commands, [])
        self.assertEqual(len(session.terminated), 1)


if __name__ == '__main__':
    unittest.main()
