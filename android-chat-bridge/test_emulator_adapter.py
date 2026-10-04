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
            return b'<?xml version="1.0"?><hierarchy/>'
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
