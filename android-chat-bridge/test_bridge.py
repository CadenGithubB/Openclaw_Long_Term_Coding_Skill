"""Boundary tests. All processes, networking, Docker and emulator operations are faked.

These tests exercise outcomes, including refusal before side effects; they never run
the Java, Gradle or Python source supplied to the worker.
"""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch
import zipfile

spec = importlib.util.spec_from_file_location("android_bridge_under_test", Path(__file__).with_name("bridge.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
ACTOR = "agent:main:chat-a|session-a"
OTHER = "agent:main:chat-b|session-b"


def container_fixture(work, job_id="am-" + "a" * 32):
    return {
        "Id": "c" * 64, "Image": bridge.IMAGE,
        "Config": {"User": "1000:1000", "Labels": {"org.openclaw.android-job": job_id}},
        "State": {"Running": False},
        "HostConfig": {
            "NetworkMode": "none", "ReadonlyRootfs": True, "Privileged": False,
            "CapDrop": ["ALL"], "CapAdd": None, "Memory": 2147483648,
            "MemorySwap": 2147483648, "NanoCpus": 2000000000, "PidsLimit": 128,
            "PidMode": "", "IpcMode": "private", "Devices": [],
            "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=128m", "/run": "rw,noexec,nosuid,size=16m", "/var/tmp": "rw,noexec,nosuid,size=16m"},
            "Ulimits": [{"Name": "nofile", "Soft": 1024, "Hard": 1024}, {"Name": "fsize", "Soft": 268435456, "Hard": 268435456}],
            "UTSMode": "", "CgroupnsMode": "private", "DeviceRequests": [], "VolumesFrom": [],
            "LogConfig": {"Type": "local", "Config": {"max-size": "4m", "max-file": "1", "compress": "false"}},
        },
        "Mounts": [{"Type": "bind", "Source": str(work), "Destination": "/workspace", "RW": True, "Propagation": "rprivate"}],
    }


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="controller-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        for name, value in (("ROOT", self.base / "controller"), ("VOLUME", self.base / "volume")):
            p = patch.object(bridge, name, value)
            p.start()
            self.addCleanup(p.stop)
        (bridge.VOLUME / "workspace/project").mkdir(parents=True)
        for owner, name in ((bridge.subprocess, "run"), (bridge.subprocess, "Popen"), (bridge.urllib.request, "urlopen")):
            p = patch.object(owner, name, side_effect=AssertionError("unexpected external operation"))
            p.start()
            self.addCleanup(p.stop)

    def job(self):
        job = bridge.Job(ACTOR)
        job.stop_worker = Mock(return_value={"running": False})
        job.retire_cache = Mock(return_value={"status": "not-created", "complete": True})
        job.start_worker = Mock()
        job.command = Mock(side_effect=AssertionError("unexpected command"))
        return job

    def sources(self, job):
        for name, content in bridge.SCAFFOLD.items():
            p = job.work / "project" / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        root = job.work / "project/app/src/main/java/org/openclaw/trial"
        root.mkdir(parents=True, exist_ok=True)
        (root / "MainActivity.java").write_text("package org.openclaw.trial; public class MainActivity {}\n")
        job.state.update(status="source-ready", sourceRevision=1)
        return root

    def qualify(self, job):
        self.sources(job)
        apk = job.path / "app-build-1.apk"
        apk.write_bytes(b"retained-qualified-apk")
        job.state.update(builds=1, sourceRevision=1, status="built")
        job.build_receipt = {"path": str(apk), "sha256": bridge.digest(apk.read_bytes()), "sourceRevision": 1, "sources": job.sources()}
        return apk


class ParameterTests(unittest.TestCase):
    def test_minimal_actions_are_accepted(self):
        for action in ("prepare", "status", "stop", "build", "start_test", "observe"):
            with self.subTest(action=action):
                self.assertEqual(bridge.validate_params({"action": action}), {"action": action})

    def test_host_paths_commands_and_arbitrary_options_are_refused(self):
        for field, value in (("command", "touch /tmp/bad"), ("path", "/etc/passwd"), ("workspace", "../../outside"), ("image", "untrusted"), ("shell", "/bin/zsh"), ("url", "http://example.com"), ("timeout", 999999)):
            with self.subTest(field=field), self.assertRaises(bridge.Refused):
                bridge.validate_params({"action": "build", field: value})

    def test_source_names_cannot_select_paths_or_scripts(self):
        for name in ("../MainActivity.java", "/tmp/MainActivity.java", "a/MainActivity.java", "build.gradle", "A.java\n", "A.java;touch x", "a.java", "A.java/../B.java", "A" * 65 + ".java"):
            with self.subTest(name=name), self.assertRaises(bridge.Refused):
                bridge.validate_params({"action": "write_sources", "files": [{"name": name, "content": "data"}]})

    def test_source_contents_are_opaque_data(self):
        content = 'package org.openclaw.trial; // $(touch /tmp/x) `id` " ;\n'
        params = {"action": "write_sources", "files": [{"name": "MainActivity.java", "content": content}]}
        self.assertEqual(bridge.validate_params(params)["files"][0]["content"], content)

    def test_source_batch_limits_and_unique_names(self):
        cases = [[], [{"name": "A.java", "content": "a"}] * 2,
                 [{"name": "A.java", "content": "\0"}], [{"name": "A.java", "content": ""}],
                 [{"name": "A.java", "content": "x" * (128 * 1024 + 1)}],
                 [{"name": "A.java", "content": "é" * (64 * 1024 + 1)}],
                 [{"name": "A%d.java" % n, "content": "x" * (128 * 1024)} for n in range(5)],
                 [{"name": "A%d.java" % n, "content": "x"} for n in range(17)],
                 [{"name": "A.java", "content": "x", "path": "/tmp"}]]
        for files in cases:
            with self.subTest(count=len(files)), self.assertRaises(bridge.Refused):
                bridge.validate_params({"action": "write_sources", "files": files})

    def test_tap_has_integer_and_total_time_limits(self):
        for values in ({"x": True}, {"y": -1}, {"x": 8193}, {"y": 1.5}, {"count": 0}, {"count": True}, {"count": 31}, {"intervalMs": 79}, {"intervalMs": 1501}, {"count": 12, "intervalMs": 1500}):
            params = {"action": "tap", "x": 100, "y": 100, **values}
            with self.subTest(values=values), self.assertRaises(bridge.Refused):
                bridge.validate_params(params)

    def test_job_ids_have_no_path_syntax(self):
        for value in ("../state", "am-" + "a" * 31, "am-" + "a" * 32 + "/x", "/tmp/x", 1):
            with self.subTest(value=value), self.assertRaises(bridge.Refused):
                bridge.validate_params({"action": "status", "jobId": value})
        with self.assertRaises(bridge.Refused):
            bridge.validate_params({"action": "prepare", "jobId": "am-" + "a" * 32})


class ConfinementTests(unittest.TestCase):
    def setUp(self):
        self.work = Path("/approved/project")
        self.c = container_fixture(self.work)

    def test_expected_worker_accepted(self):
        self.assertIs(bridge.validate_container(self.c, self.work, "c" * 64, "am-" + "a" * 32), self.c)

    def test_image_user_identity_and_label_must_match(self):
        for target, key, value in ((self.c, "Image", "sha256:other"), (self.c, "Id", "d" * 64), (self.c["Config"], "User", "0:0"), (self.c["Config"]["Labels"], "org.openclaw.android-job", "am-" + "b" * 32)):
            old = target[key]
            target[key] = value
            with self.subTest(key=key), self.assertRaises(bridge.Refused):
                bridge.validate_container(self.c, self.work, "c" * 64, "am-" + "a" * 32)
            target[key] = old

    def test_network_root_capabilities_namespaces_and_resources(self):
        changes = {"NetworkMode": "host", "ReadonlyRootfs": False, "Privileged": True, "CapDrop": [], "CapAdd": ["SYS_ADMIN"], "Memory": 0, "MemorySwap": -1, "NanoCpus": 0, "PidsLimit": 0, "PidMode": "host", "IpcMode": "host", "Devices": [{"PathOnHost": "/dev/test"}], "SecurityOpt": []}
        for key, value in changes.items():
            c = copy.deepcopy(self.c)
            c["HostConfig"][key] = value
            with self.subTest(key=key), self.assertRaises(bridge.Refused):
                bridge.validate_container(c, self.work)

    def test_extra_or_misdirected_mounts_are_refused(self):
        variants = [[], self.c["Mounts"] * 2]
        for key, value in (("Source", "/CONFIGURE/service-home"), ("Destination", "/"), ("RW", False), ("Type", "volume")):
            mounts = copy.deepcopy(self.c["Mounts"])
            mounts[0][key] = value
            variants.append(mounts)
        for mounts in variants:
            c = copy.deepcopy(self.c)
            c["Mounts"] = mounts
            with self.subTest(mounts=mounts), self.assertRaises(bridge.Refused):
                bridge.validate_container(c, self.work)

    def test_tmpfs_cannot_gain_exec_suid_or_unbounded_size(self):
        for value in ("rw,exec,nosuid,size=128m", "rw,noexec,suid,size=128m", "rw,noexec,nosuid", "rw,noexec,nosuid,size=2g"):
            c = copy.deepcopy(self.c)
            c["HostConfig"]["Tmpfs"]["/tmp"] = value
            with self.subTest(value=value), self.assertRaises(bridge.Refused):
                bridge.validate_container(c, self.work)

    def test_file_and_descriptor_limits_must_be_enforced(self):
        for limits in ([], [{"Name": "nofile", "Soft": -1, "Hard": -1}, {"Name": "fsize", "Soft": -1, "Hard": -1}]):
            c = copy.deepcopy(self.c)
            c["HostConfig"]["Ulimits"] = limits
            with self.subTest(limits=limits), self.assertRaises(bridge.Refused):
                bridge.validate_container(c, self.work)

    def test_additional_unconfined_security_options_are_refused(self):
        for value in ("seccomp=unconfined", "apparmor=unconfined", "label=disable"):
            c = copy.deepcopy(self.c)
            c["HostConfig"]["SecurityOpt"].append(value)
            with self.subTest(value=value), self.assertRaises(bridge.Refused):
                bridge.validate_container(c, self.work)

    def test_host_uts_and_device_requests_are_refused(self):
        for key, value in (("UTSMode", "host"), ("DeviceRequests", [{"Driver": "nvidia", "Count": -1, "Capabilities": [["gpu"]]}]), ("VolumesFrom", ["other:rw"])):
            c = copy.deepcopy(self.c)
            c["HostConfig"][key] = value
            with self.subTest(key=key), self.assertRaises(bridge.Refused):
                bridge.validate_container(c, self.work)

    def test_single_log_file_requires_compression_disabled(self):
        for value in (None, "true"):
            c = copy.deepcopy(self.c)
            if value is None:
                del c["HostConfig"]["LogConfig"]["Config"]["compress"]
            else:
                c["HostConfig"]["LogConfig"]["Config"]["compress"] = value
            with self.subTest(compress=value), self.assertRaises(bridge.Refused):
                bridge.validate_container(c, self.work)


class ControllerTests(SandboxCase):
    def request(self, actor=ACTOR, action="status", rid="r1", **params):
        return {"actor": actor, "requestId": rid, "params": {"action": action, **params}}

    def test_cross_session_job_id_cannot_read_stop_or_use_job(self):
        controller = bridge.Controller()
        job = self.job()
        controller.jobs[job.id] = job
        for action in ("status", "stop", "build", "observe"):
            with self.subTest(action=action), self.assertRaises(bridge.Refused):
                controller.dispatch(self.request(actor=OTHER, action=action, jobId=job.id))
        self.assertFalse(job.cancelled.is_set())
        job.command.assert_not_called()

    def test_implicit_selection_does_not_return_another_sessions_job(self):
        controller = bridge.Controller()
        job = self.job()
        controller.jobs[job.id] = job
        result = controller.dispatch(self.request(actor=OTHER))
        self.assertEqual(result["status"], "none")
        self.assertNotIn("jobId", result)

    def test_other_active_or_uncertain_job_prevents_preparation(self):
        for status in ("ready", "testing", "preparing", "cleanup-uncertain"):
            controller = bridge.Controller()
            job = self.job()
            job.state["status"] = status
            controller.jobs[job.id] = job
            with self.subTest(status=status), self.assertRaises(bridge.Refused):
                controller.dispatch(self.request(actor=OTHER, action="prepare"))
            self.assertEqual(len(controller.jobs), 1)

    def test_invalid_identity_is_rejected_before_job_creation(self):
        for actor in ("agent:devlab:a|b", "agent:main:missing-separator", "untrusted", None):
            controller = bridge.Controller()
            with self.subTest(actor=actor), self.assertRaises(bridge.Refused):
                controller.dispatch(self.request(actor=actor, action="prepare"))
            self.assertEqual(controller.jobs, {})

    def test_invalid_request_id_is_rejected_before_job_creation(self):
        for rid in (None, "", "x" * 257, 42):
            controller = bridge.Controller()
            with self.subTest(rid=rid), self.assertRaises(bridge.Refused):
                controller.dispatch(self.request(action="prepare", rid=rid))
            self.assertEqual(controller.jobs, {}, "invalid request created a retained job")

    def test_changed_prepare_request_does_not_create_second_job(self):
        controller = bridge.Controller()
        job = self.job()
        job.state["status"] = "ready"
        job.prepare = Mock(side_effect=AssertionError("preparation replay"))
        controller.jobs[job.id] = job
        result = controller.dispatch(self.request(action="prepare", rid="new-id"))
        self.assertEqual(result["jobId"], job.id)
        self.assertTrue(result["ok"])
        self.assertEqual(len(controller.jobs), 1)
        job.prepare.assert_not_called()

    def test_terminal_duplicate_prepare_reports_failure_without_resetting_limits(self):
        for status in ("stopped", "failed", "expired", "cleanup-uncertain"):
            controller = bridge.Controller()
            job = self.job()
            job.state.update(status=status, builds=3, writes=8, actions=40)
            job.prepare = Mock(side_effect=AssertionError("terminal job preparation replay"))
            controller.jobs[job.id] = job
            before = copy.deepcopy(job.state)
            result = controller.dispatch(self.request(action="prepare", rid="new-after-terminal"))
            with self.subTest(status=status):
                self.assertFalse(result["ok"])
                self.assertIs(result["retryAllowed"], False)
                self.assertEqual(result["jobId"], job.id)
                self.assertEqual(result["status"], status)
                self.assertIn("cannot be restarted in this session", result["summary"])
                self.assertEqual(job.state, before)
                self.assertEqual(len(controller.jobs), 1)
                job.prepare.assert_not_called()
                job.command.assert_not_called()

    def test_actor_only_stop_cancels_delayed_prepare(self):
        controller = bridge.Controller()
        result = controller.dispatch(self.request(action="stop"))
        self.assertTrue(result["ok"])
        with self.assertRaises(bridge.Refused):
            controller.dispatch(self.request(action="prepare", rid="delayed-prepare"))
        self.assertEqual(controller.jobs, {})

    def test_actor_cancellation_survives_controller_restart(self):
        controller = bridge.Controller()
        controller.dispatch(self.request(action="stop"))
        replacement = bridge.Controller()
        with self.assertRaises(bridge.Refused):
            replacement.dispatch(self.request(action="prepare", rid="late-after-restart"))
        self.assertEqual(replacement.jobs, {})

    def test_client_stop_before_socket_exists_cancels_later_preparation(self):
        request = self.request(action="stop", rid="abort-before-controller-start")
        stdin = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))
        stdout = io.StringIO()
        with patch.object(bridge, "SOCKET", bridge.ROOT / "absent.sock"), patch.object(bridge.sys, "stdin", stdin), patch.object(bridge.sys, "stdout", stdout):
            bridge.client()
        self.assertTrue(json.loads(stdout.getvalue())["ok"])
        controller = bridge.Controller()
        with self.assertRaises(bridge.Refused):
            controller.dispatch(self.request(action="prepare", rid="late-after-client-abort"))
        self.assertEqual(controller.jobs, {})

    def test_artifact_quota_excludes_disposable_emulator_disks(self):
        controller = bridge.Controller()
        job = self.job()
        job.state["status"] = "ready"
        job.stop = Mock()
        controller.jobs[job.id] = job
        disks = job.path / "emulator-1/avd"
        disks.mkdir(parents=True)
        with (disks / "userdata.img").open("wb") as f:
            f.truncate(2 * 1024 * 1024 * 1024)
        with patch.object(bridge.time, "sleep", side_effect=[None, StopIteration]), self.assertRaises(StopIteration):
            controller.watch()
        job.stop.assert_not_called()

    def test_artifact_quota_still_counts_retained_build_output(self):
        controller = bridge.Controller()
        job = self.job()
        job.state["status"] = "ready"
        job.stop = Mock()
        controller.jobs[job.id] = job
        with (job.path / "build.log").open("wb") as f:
            f.truncate(97 * 1024 * 1024)
        with patch.object(bridge.time, "sleep", side_effect=[None, StopIteration]), self.assertRaises(StopIteration):
            controller.watch()
        job.stop.assert_called_once_with("expired")


class LifecycleTests(SandboxCase):
    def test_same_request_returns_receipt_without_replaying_action(self):
        job = self.job()
        job.prepare = Mock(return_value={"summary": "ready"})
        params = {"action": "prepare", "reason": "original"}
        first = job.perform("request-1", params)
        second = job.perform("request-1", dict(params))
        self.assertEqual(first, second)
        self.assertEqual(job.prepare.call_count, 1)
        self.assertEqual(job.state["actions"], 1)

    def test_request_identity_cannot_be_reused_with_changed_arguments(self):
        job = self.job()
        job.prepare = Mock(return_value={"summary": "ready"})
        job.perform("request-1", {"action": "prepare"})
        with self.assertRaises(bridge.Refused):
            job.perform("request-1", {"action": "stop"})
        self.assertFalse(job.cancelled.is_set())

    def test_uncertain_request_is_not_replayed(self):
        job = self.job()
        params = {"action": "build"}
        job.requests["request-1"] = {"digest": bridge.digest(json.dumps(params, sort_keys=True).encode()), "result": None}
        job.build = Mock(side_effect=AssertionError("build replayed"))
        with self.assertRaises(bridge.Refused):
            job.perform("request-1", params)
        job.build.assert_not_called()

    def test_failed_action_receipt_is_not_replayed(self):
        job = self.job()
        job.build = Mock(side_effect=bridge.Refused("build failed"))
        params = {"action": "build"}
        first = job.perform("request-1", params)
        self.assertFalse(first["ok"])
        self.assertEqual(first, job.perform("request-1", params))
        self.assertEqual(job.build.call_count, 1)

    def test_status_is_readable_after_stop_but_build_is_refused(self):
        job = self.job()
        job.build = Mock(side_effect=AssertionError("build after stop"))
        job.stop()
        self.assertEqual(job.perform("status", {"action": "status"})["state"]["status"], "stopped")
        result = job.perform("build", {"action": "build"})
        self.assertFalse(result["ok"])
        job.build.assert_not_called()
        self.assertNotIn("actor", job.perform("status-2", {"action": "status"})["state"])

    def test_expiry_prevents_action_and_requests_cleanup(self):
        job = self.job()
        job.deadline = time.monotonic() - 1
        job.build = Mock(side_effect=AssertionError("expired build"))
        result = job.perform("build", {"action": "build"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["cleanup"]["stopped"])
        job.build.assert_not_called()

    def test_action_budget_prevents_worker_or_emulator_use(self):
        job = self.job()
        job.state["actions"] = 40
        job.build = Mock(side_effect=AssertionError("over-budget build"))
        result = job.perform("build", {"action": "build"})
        self.assertFalse(result["ok"])
        self.assertEqual(job.state["actions"], 40)
        job.build.assert_not_called()

    def test_build_and_write_budgets_fail_before_external_operations(self):
        job = self.job()
        job.state.update(builds=3, writes=8)
        with self.assertRaises(bridge.Refused):
            job.build()
        with self.assertRaises(bridge.Refused):
            job.write_sources([{"name": "MainActivity.java", "content": "x"}])
        job.command.assert_not_called()
        job.start_worker.assert_not_called()

    def test_tap_and_observe_require_running_qualified_test(self):
        job = self.job()
        for action in ("observe", "tap"):
            params = {"action": action, "x": 100, "y": 100} if action == "tap" else {"action": action}
            result = job.perform(action, params)
            self.assertFalse(result["ok"])
        job.command.assert_not_called()

    def test_cleanup_failure_is_retained_as_uncertain(self):
        job = self.job()
        job.stop_worker.side_effect = bridge.Refused("stop not confirmed")
        result = job.stop()
        self.assertFalse(result["stopped"])
        self.assertEqual(job.state["status"], "cleanup-uncertain")
        self.assertIn("stop not confirmed", result["errors"][0])

    def test_unconfirmed_emulator_cleanup_returns_failed_stop_receipt(self):
        job = self.job()
        job.emulator = Mock()
        job.emulator.stop.return_value = {"status": "cleanup-unconfirmed", "reason": "listener still present"}
        result = job.perform("stop", {"action": "stop"})
        self.assertFalse(result["ok"])
        self.assertFalse(result["cleanup"]["stopped"])
        self.assertEqual(job.state["status"], "cleanup-uncertain")
        self.assertEqual(result["cleanup"]["emulator"]["status"], "cleanup-unconfirmed")

    def test_stop_waits_for_inflight_start_and_prevents_running_worker_after_cleanup(self):
        job = self.job()
        del job.start_worker
        del job.stop_worker
        job.cid = "c" * 64
        running = {"value": False}
        entered, release = threading.Event(), threading.Event()
        errors = []
        job.inspect = Mock(side_effect=lambda: {"State": {"Running": running["value"]}})
        def command(argv, label, seconds):
            self.assertEqual(label, "start-worker")
            entered.set()
            if not release.wait(2):
                raise AssertionError("test failed to release fake start")
            running["value"] = True
            return "", {}
        job.command.side_effect = command
        def start():
            try:
                job.start_worker()
            except bridge.Refused as error:
                errors.append(str(error))
        def raw(argv, timeout):
            self.assertEqual(argv[1:4], ["stop", "--time", "5"])
            running["value"] = False
            return b"stopped"
        a = threading.Thread(target=start, daemon=True)
        b = threading.Thread(target=job.stop, daemon=True)
        with patch.object(bridge, "raw_command", side_effect=raw):
            a.start()
            self.assertTrue(entered.wait(2))
            b.start()
            self.assertTrue(job.cancelled.wait(2))
            self.assertTrue(b.is_alive(), "cleanup completed before inflight start was resolved")
            release.set()
            a.join(2)
            b.join(2)
        self.assertFalse(a.is_alive())
        self.assertFalse(b.is_alive())
        self.assertFalse(running["value"])
        self.assertEqual(job.state["status"], "stopped")
        self.assertTrue(job.state["cleanup"]["stopped"])
        self.assertIn("job cancelled", errors)

    def test_png_observations_are_exposed_for_launch_and_tap_results(self):
        first = {"path": "/retained/emulator-1/launch.png", "sha256": "a" * 64, "bytes": 123, "width": 1080, "height": 1920}
        second = {"path": "/retained/emulator-1/tap.png", "sha256": "b" * 64, "bytes": 124, "width": 1080, "height": 1920}
        observation = {"action": "tap", "before": {"screenshot": first}, "after": [{"screenshot": second, "ui": {"path": "/retained/tap.xml"}}], "input": {"exitCode": 0}}
        result = bridge.Job.observation_result(observation)
        self.assertEqual(result["images"], [first, second])
        self.assertEqual(result["observation"], observation)
        self.assertNotIn("passed", result)

    def test_observe_returns_actual_adapter_png_receipt(self):
        job = self.job()
        job.state["status"] = "testing"
        job.emulator = Mock()
        screenshot = {"path": "/retained/observed.png", "sha256": "a" * 64, "bytes": 99}
        job.emulator.observe.return_value = {"screenshot": screenshot, "crashes": {"fatalMarkers": 0}}
        result = job.perform("observe", {"action": "observe"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["images"], [screenshot])
        self.assertEqual(job.emulator.observe.call_count, 1)


class CommandCaptureTests(SandboxCase):
    CID = "2d11d60d783f76f52da3c0ac7dda6875c0e18a54feb84d2564cc681f65bceb27"
    WARNING = b"WARNING: The requested image's platform (linux/amd64) does not match the detected host platform (linux/arm64/v8)\n"

    def popen_output(self, stdout_bytes, stderr_bytes, returncode=0):
        def spawn(args, **kwargs):
            self.assertNotEqual(kwargs["stderr"], bridge.subprocess.STDOUT)
            self.assertIsNot(kwargs["stdout"], kwargs["stderr"])
            kwargs["stdout"].write(stdout_bytes)
            kwargs["stdout"].flush()
            kwargs["stderr"].write(stderr_bytes)
            kwargs["stderr"].flush()
            process = Mock()
            process.pid = 987654321
            process.returncode = returncode
            process.poll.return_value = returncode
            return process
        return spawn

    def test_machine_output_excludes_stderr_and_retains_both_stream_receipts(self):
        job = self.job()
        stdout = (self.CID + "\n").encode()
        with patch.object(bridge.subprocess, "Popen", side_effect=self.popen_output(stdout, self.WARNING)):
            text, receipt = bridge.Job.command(job, [bridge.DOCKER, "create"], "create-worker", 20)
        self.assertEqual(text, self.CID + "\n")
        self.assertEqual(Path(receipt["log"]).read_bytes(), stdout)
        self.assertEqual(Path(receipt["stderrLog"]).read_bytes(), self.WARNING)
        self.assertEqual(receipt["logSha256"], bridge.digest(stdout))
        self.assertEqual(receipt["stderrSha256"], bridge.digest(self.WARNING))
        self.assertEqual(receipt["stdoutBytes"], len(stdout))
        self.assertEqual(receipt["stderrBytes"], len(self.WARNING))
        self.assertEqual(job.processes, set())

    def test_quota_counts_stdout_and_stderr_together_and_retains_failure_receipt(self):
        job = self.job()
        stdout, stderr = b"a" * (2 * 1024 * 1024), b"b" * (2 * 1024 * 1024 + 1)
        with patch.object(bridge.subprocess, "Popen", side_effect=self.popen_output(stdout, stderr)), self.assertRaisesRegex(bridge.Refused, "command log quota"):
            bridge.Job.command(job, ["fixed-command"], "quota", 20)
        receipt = json.loads((job.path / "000-quota.json").read_text())
        self.assertEqual(receipt["stdoutBytes"] + receipt["stderrBytes"], 4 * 1024 * 1024 + 1)
        self.assertIn("command log quota", receipt["error"])
        self.assertEqual(receipt["stderrSha256"], bridge.digest(stderr))
        self.assertEqual(job.processes, set())

    def test_nonzero_exit_retains_stderr_and_surfaces_diagnostic(self):
        job = self.job()
        with patch.object(bridge.subprocess, "Popen", side_effect=self.popen_output(b"some stdout", b"actual failure diagnostic", 1)), self.assertRaisesRegex(bridge.Refused, "actual failure diagnostic"):
            bridge.Job.command(job, ["fixed-command"], "failure", 20)
        receipt = json.loads((job.path / "000-failure.json").read_text())
        self.assertEqual(receipt["exitCode"], 1)
        self.assertEqual(Path(receipt["stderrLog"]).read_bytes(), b"actual failure diagnostic")

    def test_prepare_accepts_clean_stdout_despite_platform_warning_on_stderr(self):
        job = self.job()
        container = container_fixture(job.work, job.id)
        container["Id"] = self.CID
        def command(argv, label, seconds):
            if label == "create-worker":
                self.assertEqual(argv[argv.index("--platform") + 1], "linux/amd64")
                log_options = [argv[n + 1] for n, arg in enumerate(argv[:-1]) if arg == "--log-opt"]
                self.assertEqual(set(log_options), {"max-size=4m", "max-file=1", "compress=false"})
                return bridge.Job.command(job, argv, label, seconds)
            if label == 'measure-offline-cache':
                return json.dumps({'bytes': 4096, 'files': 1, 'directories': 1}), {}
            return "offline toolchain verified", {}
        job.command.side_effect = command
        with patch.object(bridge, "preflight", return_value={"fake": "preflight"}), patch.object(bridge, "raw_command", return_value=json.dumps([container]).encode()) as inspect, patch.object(bridge.subprocess, "Popen", side_effect=self.popen_output((self.CID + "\n").encode(), self.WARNING)):
            result = job.prepare()
        self.assertEqual(job.cid, self.CID)
        self.assertEqual(job.state["containerId"], self.CID)
        self.assertEqual(job.state["status"], "ready")
        self.assertIn("toolchain", result)
        inspect.assert_called_once_with([bridge.DOCKER, "inspect", self.CID])
        self.assertEqual((job.path / "000-create-worker.stderr.log").read_bytes(), self.WARNING)

    def test_invalid_create_stdout_never_poisoned_cid_and_cleanup_reconciles_exact_name(self):
        job = self.job()
        del job.stop_worker
        container = container_fixture(job.work, job.id)
        container["Id"] = self.CID
        job.command.return_value = (self.WARNING.decode() + self.CID + "\n", {})
        job.command.side_effect = None
        stdout = json.dumps([container]).encode()
        def discover(argv, **kwargs):
            self.assertEqual(argv, [bridge.DOCKER, "inspect", job.container_name])
            self.assertIsNone(job.cid, "unvalidated command output poisoned cleanup identity")
            return types.SimpleNamespace(returncode=0, stdout=stdout, stderr=b"")
        with patch.object(bridge, "preflight", return_value={}), patch.object(bridge.subprocess, "run", side_effect=discover), patch.object(bridge, "raw_command", return_value=stdout):
            result = job.perform("prepare-bad-stdout", {"action": "prepare"})
        self.assertFalse(result["ok"])
        self.assertIn("unexpected container ID", result["summary"])
        self.assertTrue(result["cleanup"]["stopped"])
        self.assertEqual(job.cid, self.CID)
        self.assertEqual(result["cleanup"]["worker"]["containerId"], self.CID)
        self.assertEqual(job.state["status"], "failed")
        job.start_worker.assert_not_called()

    def test_create_inspection_rejects_wrong_image_or_label_before_enrollment(self):
        for changed in ("image", "label"):
            job = self.job()
            container = container_fixture(job.work, job.id)
            container["Id"] = self.CID
            if changed == "image":
                container["Image"] = "sha256:wrong-image"
            else:
                container["Config"]["Labels"]["org.openclaw.android-job"] = "am-" + "f" * 32
            job.command.side_effect = None
            job.command.return_value = (self.CID + "\n", {})
            with self.subTest(changed=changed), patch.object(bridge, "preflight", return_value={}), patch.object(bridge, "raw_command", return_value=json.dumps([container]).encode()), self.assertRaises(bridge.Refused):
                job.prepare()
            self.assertIsNone(job.cid)
            self.assertNotIn("containerId", job.state)
            job.start_worker.assert_not_called()

    def test_failed_create_reconciles_owned_created_container_without_replay(self):
        job = self.job()
        del job.stop_worker
        container = container_fixture(job.work, job.id)
        container["Id"] = self.CID
        container["State"]["Running"] = True
        job.command.side_effect = bridge.Refused("Docker connection ended after create")
        def raw(argv, timeout=20):
            if argv[1] == "stop":
                self.assertEqual(argv, [bridge.DOCKER, "stop", "--time", "5", self.CID])
                container["State"]["Running"] = False
                return b"stopped"
            self.assertEqual(argv, [bridge.DOCKER, "inspect", self.CID])
            return json.dumps([container]).encode()
        response = types.SimpleNamespace(returncode=0, stdout=json.dumps([container]).encode(), stderr=b"")
        with patch.object(bridge, "preflight", return_value={}), patch.object(bridge.subprocess, "run", return_value=response) as discover, patch.object(bridge, "raw_command", side_effect=raw):
            result = job.perform("failed-create", {"action": "prepare"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["cleanup"]["stopped"])
        self.assertFalse(container["State"]["Running"])
        self.assertEqual(job.command.call_count, 1)
        discover.assert_called_once_with([bridge.DOCKER, "inspect", job.container_name], env=bridge.ENV, capture_output=True, timeout=15)


class SourceArtifactTests(SandboxCase):
    def test_retained_file_parent_traversal_is_refused(self):
        job = self.job()
        outside = job.path.parent / "outside.apk"
        outside.write_bytes(b"outside retained job")
        with self.assertRaises(bridge.Refused):
            bridge.safe_regular(job.path / ".." / "outside.apk", job.path, 1024)

    def test_retained_file_hardlink_is_refused(self):
        job = self.job()
        outside = self.base / "outside.apk"
        outside.write_bytes(b"hardlinked artifact")
        link = job.path / "inside.apk"
        os.link(outside, link)
        with self.assertRaises(bridge.Refused):
            bridge.safe_regular(link, job.path, 1024)

    def test_scaffold_changes_refuse_before_worker_starts(self):
        job = self.job()
        self.sources(job)
        (job.work / "project/app/build.gradle").write_text("untrusted build script")
        with self.assertRaises(bridge.Refused):
            job.build()
        job.start_worker.assert_not_called()
        job.command.assert_not_called()

    def test_scaffold_symlink_refused_even_when_content_is_correct(self):
        job = self.job()
        self.sources(job)
        target = self.base / "outside.gradle"
        target.write_text(bridge.SCAFFOLD["build.gradle"])
        path = job.work / "project/build.gradle"
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(bridge.Refused):
            job.scaffold_ok()

    def test_source_manifest_refuses_symlinks_unexpected_names_and_missing_main(self):
        for mode in ("symlink", "unexpected", "missing", "subdirectory"):
            job = self.job()
            root = self.sources(job)
            if mode == "symlink":
                (root / "Extra.java").symlink_to(root / "MainActivity.java")
            elif mode == "unexpected":
                (root / "build.gradle").write_text("x")
            elif mode == "missing":
                (root / "MainActivity.java").unlink()
            else:
                (root / "Extra.java").mkdir()
            with self.subTest(mode=mode), self.assertRaises(bridge.Refused):
                job.sources()

    def test_source_parent_symlink_is_refused(self):
        job = self.job()
        root = self.sources(job)
        moved = self.base / "outside-source"
        root.rename(moved)
        root.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(bridge.Refused):
            job.sources()

    def test_failed_source_write_invalidates_previous_apk(self):
        job = self.job()
        self.qualify(job)
        job.state["apk"] = job.build_receipt
        job.command.side_effect = bridge.Refused("write failed")
        with self.assertRaises(bridge.Refused):
            job.write_sources([{"name": "MainActivity.java", "content": "x"}])
        self.assertIsNone(job.build_receipt)
        self.assertNotIn("apk", job.state)
        self.assertEqual(job.state["sourceRevision"], 2)
        job.stop_worker.assert_called_once()

    def test_source_content_is_sent_on_stdin_not_in_command_arguments(self):
        job = self.job()
        self.sources(job)
        code = 'package org.openclaw.trial; // $(touch /tmp/pwned) `id`; "\n'
        job.command.side_effect = bridge.Refused("fake write complete enough to inspect")
        with self.assertRaises(bridge.Refused):
            job.write_sources([{"name": "MainActivity.java", "content": code}])
        call = job.command.call_args
        self.assertNotIn(code, call.args[0])
        self.assertEqual(json.loads(call.args[3]), [{"name": "MainActivity.java", "content": code}])
        self.assertEqual(call.args[0][-3:-1], ["/usr/bin/python3", "-c"])

    def test_stale_modified_or_relocated_apk_does_not_start_emulator(self):
        fake = Mock()
        module = types.SimpleNamespace(EmulatorSession=fake)
        for mode in ("revision", "bytes", "source", "outside", "symlink"):
            job = self.job()
            apk = self.qualify(job)
            if mode == "revision":
                job.state["sourceRevision"] += 1
            elif mode == "bytes":
                apk.write_bytes(b"changed APK")
            elif mode == "source":
                (job.work / "project/app/src/main/java/org/openclaw/trial/MainActivity.java").write_text("changed source")
            elif mode == "outside":
                outside = self.base / "outside.apk"
                outside.write_bytes(apk.read_bytes())
                job.build_receipt["path"] = str(outside)
            else:
                outside = self.base / "outside.apk"
                outside.write_bytes(apk.read_bytes())
                apk.unlink()
                apk.symlink_to(outside)
            with self.subTest(mode=mode), patch.dict(sys.modules, {"emulator_adapter": module}), self.assertRaises(bridge.Refused):
                job.start_test()
        fake.assert_not_called()

    def test_verified_artifact_starts_only_fixed_package_and_activity(self):
        job = self.job()
        apk = self.qualify(job)
        instance = Mock()
        instance.start.return_value = {"summary": "fake guest booted"}
        factory = Mock(return_value=instance)
        with patch.dict(sys.modules, {"emulator_adapter": types.SimpleNamespace(EmulatorSession=factory)}):
            result = job.start_test()
        self.assertEqual(result["observation"]["summary"], "fake guest booted")
        self.assertEqual(factory.call_args.args[1], apk)
        self.assertEqual(factory.call_args.kwargs["package"], "org.openclaw.trial")
        self.assertEqual(factory.call_args.kwargs["activity"], ".MainActivity")
        self.assertEqual(job.state["status"], "testing")

    def test_build_failure_cannot_retain_old_qualification(self):
        job = self.job()
        self.qualify(job)
        job.state["apk"] = job.build_receipt
        job.command.side_effect = bridge.Refused("compiler error")
        with self.assertRaises(bridge.Refused):
            job.build()
        self.assertIsNone(job.build_receipt)
        self.assertNotIn("apk", job.state)
        job.stop_worker.assert_called_once()

    def test_running_emulator_prevents_build_mutation(self):
        job = self.job()
        self.qualify(job)
        qualified = copy.deepcopy(job.build_receipt)
        job.emulator = Mock()
        with self.assertRaises(bridge.Refused):
            job.build()
        self.assertEqual(job.build_receipt, qualified)
        self.assertEqual(job.state["sourceRevision"], 1)
        job.start_worker.assert_not_called()

    def test_repair_stops_old_guest_and_retains_cleanup_before_writing(self):
        job = self.job()
        self.qualify(job)
        guest = Mock()
        receipt = {"status": "stopped", "guest": "old-build", "observationsRetained": True}
        guest.stop.return_value = receipt
        job.emulator = guest
        job.command.side_effect = bridge.Refused("fake source write reached")
        with self.assertRaisesRegex(bridge.Refused, "fake source write reached"):
            job.write_sources([{"name": "MainActivity.java", "content": "repair"}])
        guest.stop.assert_called_once()
        self.assertIsNone(job.emulator)
        self.assertIsNone(job.build_receipt)
        self.assertEqual(job.state["sourceRevision"], 2)
        event = next(e for e in job.state["events"] if e["action"] == "retire_test_for_repair")
        self.assertEqual(event["result"], receipt)
        job.start_worker.assert_called_once()

    def test_repair_refuses_unconfirmed_guest_cleanup_before_mutating_source(self):
        job = self.job()
        self.qualify(job)
        job.emulator = Mock()
        receipt = {"status": "cleanup-unconfirmed", "reason": "owned process remains"}
        job.emulator.stop.return_value = receipt
        result = job.perform("repair", {"action": "write_sources", "files": [{"name": "MainActivity.java", "content": "repair"}]})
        self.assertFalse(result["ok"])
        self.assertEqual(job.state["sourceRevision"], 1)
        self.assertEqual(job.state["writes"], 0)
        job.command.assert_not_called()
        job.start_worker.assert_not_called()
        self.assertEqual(next(e for e in job.state["events"] if e["action"] == "retire_test_for_repair")["result"], receipt)

    def test_source_change_during_build_refuses_qualification_and_stops_worker(self):
        job = self.job()
        root = self.sources(job)
        def fake_command(argv, label, seconds):
            if label == "apk-package":
                (root / "MainActivity.java").write_text("changed during compile")
                return ("package: name='org.openclaw.trial'\nlaunchable-activity: name='org.openclaw.trial.MainActivity'", {})
            return ("ok", {})
        job.command.side_effect = fake_command
        with self.assertRaises(bridge.Refused):
            job.build()
        self.assertIsNone(job.build_receipt)
        job.stop_worker.assert_called_once()

    def test_unsafe_or_incomplete_apk_archive_cannot_be_qualified(self):
        for entries in ({"AndroidManifest.xml": b"x"}, {"classes.dex": b"x"}, {"AndroidManifest.xml": b"x", "classes.dex": b"x", "../escape": b"x"}, {"AndroidManifest.xml": b"x", "classes.dex": b"x", "/absolute": b"x"}):
            job = self.job()
            self.sources(job)
            path = job.work / "project/app/build/outputs/apk/debug/app-debug.apk"
            path.parent.mkdir(parents=True)
            with zipfile.ZipFile(path, "w") as z:
                for name, data in entries.items():
                    z.writestr(name, data)
            job.command.side_effect = [("build ok", {}), ("signature ok", {}), ("package: name='org.openclaw.trial'\nlaunchable-activity: name='org.openclaw.trial.MainActivity'", {})]
            with self.subTest(entries=list(entries)), self.assertRaises(bridge.Refused):
                job.build()
            self.assertIsNone(job.build_receipt)
            job.stop_worker.assert_called_once()

    def test_build_checks_package_and_manifest_before_qualifying(self):
        job = self.job()
        self.sources(job)
        job.command.side_effect = [("build ok", {}), ("signature ok", {}), ("package: name='wrong.package'", {})]
        with self.assertRaises(bridge.Refused):
            job.build()
        self.assertIsNone(job.build_receipt)
        job.stop_worker.assert_called_once()

    def test_build_captures_apk_and_source_digest_only_after_checks(self):
        job = self.job()
        self.sources(job)
        path = job.work / "project/app/build/outputs/apk/debug/app-debug.apk"
        path.parent.mkdir(parents=True)
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("AndroidManifest.xml", b"manifest fixture")
            z.writestr("classes.dex", b"dex fixture")
        job.command.side_effect = [("build ok", {"exitCode": 0}), ("signature ok", {"exitCode": 0}), ("package: name='org.openclaw.trial'\nlaunchable-activity: name='org.openclaw.trial.MainActivity'", {"exitCode": 0})]
        result = job.build()
        receipt = result["apk"]
        self.assertEqual(receipt["sha256"], bridge.digest(path.read_bytes()))
        self.assertEqual(Path(receipt["path"]).read_bytes(), path.read_bytes())
        self.assertEqual(receipt["sources"], job.sources())
        self.assertEqual(receipt["sourceRevision"], 1)
        self.assertEqual(job.state["status"], "built")
        job.stop_worker.assert_called_once()
        first_argv = job.command.call_args_list[0].args[0]
        self.assertIn("/usr/local/bin/gradle-offline", first_argv)
        self.assertIn("--rerun-tasks", first_argv)
        self.assertIn("--no-build-cache", first_argv)


class SeedAdmissionTests(SandboxCase):
    def prepare_fixture(self, measurement, free_values):
        job = self.job()
        cid = 'c' * 64
        container = container_fixture(job.work, job.id)
        container['Id'] = cid
        labels = []
        def command(argv, label, seconds):
            labels.append(label)
            if label == 'create-worker':
                return cid + '\n', {}
            if label == 'measure-offline-cache':
                self.assertEqual(argv, [bridge.DOCKER, 'exec', cid, '/usr/bin/python3', '-B', '-c', bridge.MEASURE_SEED])
                self.assertEqual(seconds, 30)
                return measurement, {'exitCode': 0, 'log': 'measured-seed.log'}
            return 'offline tools', {}
        job.command.side_effect = command
        context = [patch.object(bridge, 'preflight', return_value={}),
                   patch.object(bridge, 'raw_command', return_value=json.dumps([container]).encode()),
                   patch.object(bridge.shutil, 'disk_usage', side_effect=[types.SimpleNamespace(free=n) for n in free_values])]
        for entry in context:
            entry.start()
            self.addCleanup(entry.stop)
        return job, labels

    def test_space_passing_early_floor_but_short_of_measured_seed_and_reserve_never_copies(self):
        seed = 500 * 1024 * 1024
        job, labels = self.prepare_fixture(json.dumps({'bytes': seed, 'files': 100, 'directories': 10}), [550 * 1024 * 1024])
        result = job.perform('prepare-short-space', {'action': 'prepare'})
        self.assertFalse(result['ok'])
        self.assertIn('cache was not copied', result['summary'])
        self.assertIn('measure-offline-cache', labels)
        self.assertNotIn('seed-offline-cache', labels)
        self.assertFalse((job.work / '.android-runtime').exists())
        self.assertEqual(job.state['cacheAdmission']['requiredBytes'], 600 * 1024 * 1024)
        self.assertEqual(job.state['cacheAdmission']['freeBytesBeforeCopy'], 550 * 1024 * 1024)
        self.assertTrue(result['cleanup']['stopped'])

    def test_seed_exactly_fitting_existing_reserve_is_admitted_then_reserve_rechecked(self):
        job, labels = self.prepare_fixture(json.dumps({'bytes': 500 * 1024 * 1024, 'files': 100, 'directories': 10}), [600 * 1024 * 1024, 100 * 1024 * 1024])
        result = job.prepare()
        self.assertEqual(job.state['status'], 'ready')
        self.assertLess(labels.index('volume-guard'), labels.index('measure-offline-cache'))
        self.assertLess(labels.index('measure-offline-cache'), labels.index('seed-offline-cache'))
        self.assertEqual(job.state['cacheAdmission']['reserveBytes'], 100 * 1024 * 1024)
        self.assertIn('toolchain', result)

    def test_invalid_measurement_fails_closed_without_seed_copy(self):
        values = ['not JSON', json.dumps({'bytes': 1}), json.dumps({'bytes': True, 'files': 1, 'directories': 1}),
                  json.dumps({'bytes': -1, 'files': 1, 'directories': 1}), json.dumps({'bytes': 3 * 1024**3, 'files': 1, 'directories': 1}),
                  json.dumps({'bytes': 1, 'files': 0, 'directories': 1}), json.dumps({'bytes': 1, 'files': 1, 'directories': 1, 'extra': 0})]
        for measured in values:
            job, labels = self.prepare_fixture(measured, [])
            result = job.perform('prepare-invalid-measurement', {'action': 'prepare'})
            with self.subTest(measured=measured):
                self.assertFalse(result['ok'])
                self.assertNotIn('seed-offline-cache', labels)
                self.assertFalse((job.work / '.android-runtime').exists())


class AdapterFailureStateTests(SandboxCase):
    def make_testing_job(self, action='tap', cleanup=None):
        job = self.job()
        self.qualify(job)
        job.state['status'] = 'testing'
        guest = Mock()
        getattr(guest, action).side_effect = bridge.Refused('actual adapter observation failed')
        guest.stop.return_value = cleanup if cleanup is not None else {'status': 'stopped', 'errors': [], 'survivingProcessGroups': []}
        job.emulator = guest
        return job, guest

    def test_tap_and_observe_failure_record_stopped_guest_without_discarding_source_or_apk(self):
        for action in ('tap', 'observe'):
            job, guest = self.make_testing_job(action)
            before = copy.deepcopy(job.build_receipt)
            params = {'action': action, 'x': 10, 'y': 10} if action == 'tap' else {'action': action}
            result = job.perform('failed-' + action, params)
            with self.subTest(action=action):
                self.assertFalse(result['ok'])
                self.assertEqual(result['status'], 'built')
                self.assertEqual(job.state['status'], 'built')
                self.assertEqual(job.state['emulatorStatus'], 'stopped')
                self.assertEqual(job.state['lastEmulatorCleanup'], result['emulatorCleanup'])
                self.assertTrue(result['repairAllowed'])
                self.assertIsNone(job.emulator)
                self.assertEqual(job.build_receipt, before)
                self.assertTrue(Path(before['path']).exists())
                self.assertEqual(job.sources(), before['sources'])
                self.assertFalse(job.cancelled.is_set())
                self.assertEqual(job.state['builds'], 1)
                self.assertEqual(job.state['actions'], 1)
                self.assertEqual(result, job.perform('failed-' + action, params))
                self.assertEqual(getattr(guest, action).call_count, 1)
                guest.stop.assert_called_once()

    def test_confirmed_failure_allows_existing_bounded_source_repair(self):
        job, guest = self.make_testing_job('observe')
        job.perform('observe-failed', {'action': 'observe'})
        source = 'package org.openclaw.trial; public class MainActivity { /* repair */ }'
        def write(argv, label, seconds, data):
            self.assertEqual(label, 'write-java')
            (job.work / 'project/app/src/main/java/org/openclaw/trial/MainActivity.java').write_text(json.loads(data)[0]['content'])
            return 'wrote source', {}
        job.command.side_effect = write
        result = job.perform('bounded-repair', {'action': 'write_sources', 'files': [{'name': 'MainActivity.java', 'content': source}]})
        self.assertTrue(result['ok'])
        self.assertEqual(job.state['status'], 'source-ready')
        self.assertEqual((job.state['writes'], job.state['builds'], job.state['actions']), (1, 1, 2))
        self.assertIsNone(job.build_receipt)
        guest.stop.assert_called_once()

    def test_unconfirmed_adapter_shutdown_ends_job_without_allowing_repair(self):
        receipt = {'status': 'cleanup-unconfirmed', 'errors': ['process survived'], 'survivingProcessGroups': ['123 456']}
        job, guest = self.make_testing_job('observe', receipt)
        result = job.perform('observe-uncertain', {'action': 'observe'})
        self.assertFalse(result['ok'])
        self.assertFalse(result['repairAllowed'])
        self.assertEqual(result['status'], 'cleanup-uncertain')
        self.assertEqual(job.state['emulatorStatus'], 'cleanup-unconfirmed')
        self.assertEqual(result['emulatorCleanup'], receipt)
        self.assertFalse(result['cleanup']['stopped'])
        self.assertTrue(job.cancelled.is_set())
        retry = job.perform('repair-refused', {'action': 'write_sources', 'files': [{'name': 'MainActivity.java', 'content': 'repair'}]})
        self.assertFalse(retry['ok'])
        self.assertEqual(job.state['writes'], 0)
        job.command.assert_not_called()

    def test_adapter_stop_exception_is_retained_as_uncertain(self):
        job, guest = self.make_testing_job('tap')
        guest.stop.side_effect = RuntimeError('cleanup receipt unavailable')
        result = job.perform('tap-no-cleanup', {'action': 'tap', 'x': 10, 'y': 10})
        self.assertFalse(result['ok'])
        self.assertFalse(result['repairAllowed'])
        self.assertEqual(result['status'], 'cleanup-uncertain')
        self.assertIn('cleanup receipt unavailable', result['emulatorCleanup']['errors'])

    def test_failed_observation_does_not_reset_exhausted_repair_budgets(self):
        job, guest = self.make_testing_job('observe')
        job.state.update(writes=8, builds=3, actions=39)
        result = job.perform('observe-at-limit', {'action': 'observe'})
        self.assertFalse(result['repairAllowed'])
        self.assertEqual((job.state['writes'], job.state['builds'], job.state['actions']), (8, 3, 40))
        self.assertEqual(job.state['status'], 'built')
        self.assertIn('budgets are exhausted', result['next'])

    def test_cancellation_during_adapter_shutdown_does_not_revive_repairable_state(self):
        job, guest = self.make_testing_job('observe')
        def stop_guest():
            job.cancelled.set()
            return {'status': 'stopped', 'errors': [], 'survivingProcessGroups': []}
        guest.stop.side_effect = stop_guest
        result = job.perform('observe-cancel-race', {'action': 'observe'})
        self.assertFalse(result['ok'])
        self.assertFalse(result['repairAllowed'])
        self.assertIn(result['status'], bridge.TERMINAL)
        self.assertNotEqual(job.state['status'], 'built')
        self.assertTrue(result['cleanup']['stopped'])

    def test_failed_test_cannot_restart_or_rebuild_same_source_and_old_frames_remain(self):
        job, guest = self.make_testing_job('observe')
        old = job.path / 'emulator-1'
        old.mkdir()
        (old / 'before.png').write_bytes(b'original frame')
        job.perform('old-observe-failed', {'action': 'observe'})
        result = job.perform('same-build-restart', {'action': 'start_test'})
        self.assertFalse(result['ok'])
        self.assertTrue(result['repairAllowed'])
        self.assertIn('write_sources', result['summary'])
        self.assertFalse(job.cancelled.is_set())
        self.assertEqual(job.state['status'], 'built')
        rebuild = job.perform('same-source-rebuild', {'action': 'build'})
        self.assertFalse(rebuild['ok'])
        self.assertIn('new source write', rebuild['summary'])
        self.assertEqual(job.state['builds'], 1)
        self.assertEqual((old / 'before.png').read_bytes(), b'original frame')
        job.command.assert_not_called()

    def test_failed_source_write_does_not_satisfy_failed_guest_repair_gate(self):
        job, guest = self.make_testing_job('observe')
        job.perform('guest-failed', {'action': 'observe'})
        job.command.side_effect = bridge.Refused('source write failed before confirmation')
        write = job.perform('failed-write', {'action': 'write_sources', 'files': [{'name': 'MainActivity.java', 'content': 'repair'}]})
        self.assertFalse(write['ok'])
        self.assertGreater(job.state['sourceRevision'], job.state['failedTestSourceRevision'])
        count = job.command.call_count
        build = job.perform('build-after-failed-write', {'action': 'build'})
        self.assertFalse(build['ok'])
        self.assertIn('new source write', build['summary'])
        self.assertEqual(job.command.call_count, count)
        self.assertEqual(job.state['builds'], 1)
        self.assertTrue(job.state['testRequiresRepair'])

    def test_source_repair_new_build_and_test_refresh_status_and_preserve_old_test(self):
        job, guest = self.make_testing_job('observe')
        old = job.path / 'emulator-1'
        old.mkdir()
        (old / 'before.png').write_bytes(b'original frame')
        job.perform('old-observe-failed', {'action': 'observe'})
        def write(argv, label, seconds, data):
            (job.work / 'project/app/src/main/java/org/openclaw/trial/MainActivity.java').write_text(json.loads(data)[0]['content'])
            return 'wrote repaired source', {}
        job.command.side_effect = write
        source = 'package org.openclaw.trial; public class MainActivity { /* repaired */ }'
        repair = job.perform('write-repair', {'action': 'write_sources', 'files': [{'name': 'MainActivity.java', 'content': source}]})
        self.assertTrue(repair['ok'])
        apk = job.work / 'project/app/build/outputs/apk/debug/app-debug.apk'
        apk.parent.mkdir(parents=True)
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'manifest fixture')
            archive.writestr('classes.dex', b'dex fixture')
        job.command.side_effect = [('build success', {'exitCode': 0}), ('signature success', {'exitCode': 0}),
                                   ("package: name='org.openclaw.trial'\nlaunchable-activity: name='org.openclaw.trial.MainActivity'", {'exitCode': 0})]
        built = job.perform('build-repair', {'action': 'build'})
        self.assertTrue(built['ok'])
        self.assertFalse(job.state['testRequiresRepair'])
        self.assertEqual(job.state['builds'], 2)
        new_guest = Mock()
        new_guest.start.return_value = {'summary': 'new guest launched'}
        new_guest.stop.return_value = {'status': 'stopped', 'errors': [], 'survivingProcessGroups': []}
        factory = Mock(return_value=new_guest)
        with patch.dict(sys.modules, {'emulator_adapter': types.SimpleNamespace(EmulatorSession=factory)}):
            started = job.perform('test-repair', {'action': 'start_test'})
        self.assertTrue(started['ok'])
        self.assertEqual(job.state['status'], 'testing')
        self.assertEqual(job.state['emulatorStatus'], 'running')
        self.assertEqual(factory.call_args.args[0], job.path / 'emulator-2')
        self.assertEqual((old / 'before.png').read_bytes(), b'original frame')
        final = job.perform('final-stop', {'action': 'stop'})
        self.assertTrue(final['ok'])
        self.assertEqual(job.state['emulatorStatus'], 'stopped')
        self.assertEqual((job.state['writes'], job.state['builds'], job.state['actions']), (1, 2, 4))


class TapTargetRejectionTests(SandboxCase):
    """The recorded trial lost a build to a tap rejected before any guest input."""

    def running_job(self, display=(480, 800)):
        job = self.job()
        self.qualify(job)
        job.state['apk'] = dict(job.build_receipt, build={'exitCode': 0}, signature={'exitCode': 0}, packageCheck={'exitCode': 0})
        guest = Mock()
        guest.start.return_value = {'action': 'start_test', 'display': list(display) if display else None,
                                    'initialObservation': {'screenshot': {'path': str(job.path / 'emulator-1/0001-launch.png')}}}
        guest.tap.return_value = {'action': 'tap', 'status': 'returned'}
        guest.stop.return_value = {'status': 'stopped', 'errors': [], 'survivingProcessGroups': []}
        with patch.dict(sys.modules, {'emulator_adapter': types.SimpleNamespace(EmulatorSession=Mock(return_value=guest))}):
            started = job.perform('start', {'action': 'start_test'})
        self.assertTrue(started['ok'])
        return job, guest, started

    def test_start_test_records_measured_display_for_tap_bounds(self):
        job, guest, started = self.running_job()
        self.assertEqual(job.state['testDisplay'], [480, 800])
        self.assertEqual(started['display'], {'width': 480, 'height': 800})
        self.assertIn('actual pixels of this 480x800 display', started['summary'])

    def test_out_of_display_tap_is_refused_without_stopping_guest_or_requiring_rebuild(self):
        job, guest, _ = self.running_job()
        before = copy.deepcopy(job.build_receipt)
        result = job.perform('tap-outside', {'action': 'tap', 'x': 4096, 'y': 6000})
        self.assertFalse(result['ok'])
        self.assertIs(result['inputDelivered'], False)
        self.assertIs(result['guestStoppedByController'], False)
        self.assertIs(result['rebuildRequired'], False)
        self.assertEqual(result['display'], {'width': 480, 'height': 800})
        self.assertEqual(result['rejectedTap'], {'x': 4096, 'y': 6000})
        self.assertIn('0 <= x < 480 and 0 <= y < 800', result['summary'])
        self.assertIn('no source write or rebuild is needed', result['summary'])
        guest.tap.assert_not_called()
        guest.stop.assert_not_called()
        self.assertIs(job.emulator, guest)
        self.assertEqual(result['status'], 'testing')
        self.assertEqual((job.state['status'], job.state['emulatorStatus']), ('testing', 'running'))
        self.assertNotIn('testRequiresRepair', job.state)
        self.assertNotIn('cleanup', result)
        self.assertNotIn('emulatorCleanup', result)
        self.assertEqual(job.build_receipt, before)
        self.assertEqual(result['progress']['qualifiedApkSourceRevision'], 1)
        self.assertFalse(job.cancelled.is_set())
        self.assertEqual((job.state['builds'], job.state['writes'], job.state['actions']), (1, 0, 2))
        self.assertEqual(result, job.perform('tap-outside', {'action': 'tap', 'x': 4096, 'y': 6000}))
        self.assertEqual(job.state['actions'], 2)
        corrected = job.perform('tap-inside', {'action': 'tap', 'x': 240, 'y': 400})
        self.assertTrue(corrected['ok'])
        guest.tap.assert_called_once_with(240, 400, 1, 300)
        self.assertEqual(corrected['display'], {'width': 480, 'height': 800})
        self.assertEqual(job.state['status'], 'testing')

    def test_display_edges_are_exclusive_upper_bounds(self):
        for x, y, accepted in ((479, 799, True), (0, 0, True), (480, 0, False), (0, 800, False), (480, 800, False)):
            job, guest, _ = self.running_job()
            result = job.perform('edge-%d-%d' % (x, y), {'action': 'tap', 'x': x, 'y': y})
            with self.subTest(x=x, y=y):
                self.assertEqual(result['ok'], accepted)
                self.assertEqual(guest.tap.call_count, 1 if accepted else 0)
                guest.stop.assert_not_called()

    def test_cancellation_racing_a_rejected_tap_still_stops_the_job(self):
        job, guest, _ = self.running_job()
        original = job.check_tap_target
        def racing(params):
            job.cancelled.set()  # Arrives after the action's own job check passed.
            original(params)
        job.check_tap_target = racing
        result = job.perform('tap-after-cancel', {'action': 'tap', 'x': 9, 'y': 9000})
        self.assertFalse(result['ok'])
        self.assertIn('cleanup', result)
        guest.stop.assert_called_once()
        guest.tap.assert_not_called()
        self.assertIn(job.state['status'], bridge.TERMINAL)

    def test_unknown_display_leaves_adapter_failure_path_unchanged(self):
        job, guest, started = self.running_job(display=None)
        self.assertIsNone(job.state['testDisplay'])
        self.assertNotIn('display', started)
        guest.tap.side_effect = bridge.Refused('tap outside observed display')
        result = job.perform('tap-unknown-display', {'action': 'tap', 'x': 4096, 'y': 6000})
        self.assertFalse(result['ok'])
        self.assertNotIn('inputDelivered', result)
        guest.tap.assert_called_once()
        guest.stop.assert_called_once()
        self.assertTrue(job.state['testRequiresRepair'])

    def test_malformed_adapter_display_is_not_trusted(self):
        for display in ([480], [0, 800], [480, 5000], [True, 800], ['480', '800'], (480, 800)):
            job = self.job()
            self.qualify(job)
            guest = Mock()
            guest.start.return_value = {'display': display}
            with patch.dict(sys.modules, {'emulator_adapter': types.SimpleNamespace(EmulatorSession=Mock(return_value=guest))}):
                started = job.perform('start', {'action': 'start_test'})
            with self.subTest(display=display):
                self.assertTrue(started['ok'])
                self.assertIsNone(job.state['testDisplay'])


class CacheRetirementTests(SandboxCase):
    def cached_job(self):
        job = self.job()
        del job.retire_cache
        self.sources(job)
        job.cid = 'c' * 64
        job.state['containerId'] = job.cid
        value = job.work.stat()
        job.state['workspaceIdentity'] = {'device': value.st_dev, 'inode': value.st_ino}
        (job.work / '.devlab-volume-id').write_text(bridge.MARKER + '\n')
        cache = job.work / '.android-runtime'
        (cache / 'gradle').mkdir(parents=True)
        (cache / 'gradle/dependency.bin').write_bytes(b'c' * 4096)
        (cache / 'ready.json').write_bytes(b'ready')
        (job.work / 'project/keep.apk').write_bytes(b'project apk')
        (job.path / 'keep.log').write_bytes(b'build log')
        (job.path / 'keep.apk').write_bytes(b'retained apk')
        job.stop_worker.return_value = {'containerId': job.cid, 'running': False}
        return job, cache

    def command_response(self, job, container=None):
        container = container_fixture(job.work, job.id) if container is None else container
        container['Id'] = job.cid
        def command(argv, timeout=20):
            if argv[0] == '/usr/sbin/diskutil':
                self.assertEqual(argv, ['/usr/sbin/diskutil', 'info', '-plist', str(bridge.VOLUME)])
                return bridge.plistlib.dumps({'VolumeUUID': bridge.VOLUME_UUID, 'MountPoint': str(bridge.VOLUME), 'TotalSize': 2147442688})
            self.assertEqual(argv, [bridge.DOCKER, 'inspect', job.cid])
            return json.dumps([container]).encode()
        return command

    def test_success_deletes_only_derived_cache_and_retains_artifacts_and_limits(self):
        job, cache = self.cached_job()
        job.state.update(builds=3, writes=8, actions=40)
        source = job.work / 'project/app/src/main/java/org/openclaw/trial/MainActivity.java'
        before = source.read_bytes()
        with patch.object(bridge, 'raw_command', side_effect=self.command_response(job)):
            result = job.perform('final-stop', {'action': 'stop'})
        self.assertTrue(result['ok'])
        self.assertTrue(result['cleanup']['stopped'])
        self.assertTrue(result['cleanup']['cleanupComplete'])
        receipt = result['cleanup']['cache']
        self.assertEqual(receipt['status'], 'removed')
        self.assertEqual(receipt['logicalBytesBefore'], 4101)
        self.assertEqual(receipt['logicalBytesAfter'], 0)
        self.assertIsInstance(receipt['freeBytesBefore'], int)
        self.assertIsInstance(receipt['freeBytesAfter'], int)
        self.assertFalse(cache.exists())
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual((job.work / 'project/keep.apk').read_bytes(), b'project apk')
        self.assertEqual((job.path / 'keep.apk').read_bytes(), b'retained apk')
        self.assertEqual((job.path / 'keep.log').read_bytes(), b'build log')
        self.assertTrue((job.path / 'state.json').is_file())
        self.assertTrue((job.path / 'report.md').is_file())
        self.assertEqual((job.state['builds'], job.state['writes'], job.state['actions']), (3, 8, 40))
        self.assertIn('Source files, APKs, logs', result['reportText'])

    def test_worker_stop_failure_keeps_cache_and_refuses_complete_cleanup(self):
        job, cache = self.cached_job()
        job.stop_worker.side_effect = bridge.Refused('worker may still be running')
        result = job.perform('failed-stop', {'action': 'stop'})
        self.assertFalse(result['ok'])
        self.assertFalse(result['cleanup']['stopped'])
        self.assertFalse(result['cleanup']['cleanupComplete'])
        self.assertEqual(result['cleanup']['cache']['status'], 'not-attempted')
        self.assertEqual((cache / 'gradle/dependency.bin').stat().st_size, 4096)

    def test_symlinked_volume_workspace_or_job_parent_is_refused(self):
        for component in ('volume', 'workspace', 'job'):
            job, cache = self.cached_job()
            target = {'volume': bridge.VOLUME, 'workspace': bridge.VOLUME / 'workspace', 'job': job.work}[component]
            moved = target.with_name(target.name + '-moved')
            target.rename(moved)
            target.symlink_to(moved, target_is_directory=True)
            try:
                with patch.object(bridge, 'raw_command', side_effect=self.command_response(job)):
                    result = job.retire_cache({'containerId': job.cid, 'running': False})
                with self.subTest(component=component):
                    self.assertFalse(result['complete'])
                    self.assertTrue((cache / 'gradle/dependency.bin').exists())
            finally:
                target.unlink()
                moved.rename(target)

    def test_cache_root_symlink_is_refused_without_touching_target(self):
        job, cache = self.cached_job()
        outside = self.base / 'external-cache'
        cache.rename(outside)
        cache.symlink_to(outside, target_is_directory=True)
        with patch.object(bridge, 'raw_command', side_effect=self.command_response(job)):
            result = job.retire_cache({'containerId': job.cid, 'running': False})
        self.assertFalse(result['complete'])
        self.assertTrue(cache.is_symlink())
        self.assertEqual((outside / 'gradle/dependency.bin').stat().st_size, 4096)

    def test_inner_symlink_is_unlinked_without_following_target(self):
        job, cache = self.cached_job()
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'important.txt').write_text('preserve me')
        (cache / 'linked').symlink_to(outside, target_is_directory=True)
        with patch.object(bridge, 'raw_command', side_effect=self.command_response(job)):
            result = job.retire_cache({'containerId': job.cid, 'running': False})
        self.assertTrue(result['complete'])
        self.assertFalse(cache.exists())
        self.assertEqual((outside / 'important.txt').read_text(), 'preserve me')

    def test_changed_workspace_identity_marker_or_container_boundary_refuses_cache(self):
        for changed in ('inode', 'marker', 'image', 'label', 'running'):
            job, cache = self.cached_job()
            container = container_fixture(job.work, job.id)
            if changed == 'inode':
                job.state['workspaceIdentity']['inode'] += 1
            elif changed == 'marker':
                (job.work / '.devlab-volume-id').write_text('different marker')
            elif changed == 'image':
                container['Image'] = 'sha256:foreign'
            elif changed == 'label':
                container['Config']['Labels']['org.openclaw.android-job'] = 'am-' + 'f' * 32
            else:
                container['State']['Running'] = True
            with patch.object(bridge, 'raw_command', side_effect=self.command_response(job, container)):
                result = job.retire_cache({'containerId': job.cid, 'running': False})
            with self.subTest(changed=changed):
                self.assertFalse(result['complete'])
                self.assertTrue((cache / 'gradle/dependency.bin').exists())

    def test_missing_workspace_identity_is_not_invented_for_old_jobs(self):
        job, cache = self.cached_job()
        del job.state['workspaceIdentity']
        result = job.retire_cache({'containerId': job.cid, 'running': False})
        self.assertFalse(result['complete'])
        self.assertIn('identity', result['error'])
        self.assertTrue(cache.exists())
        self.assertNotIn('workspaceIdentity', job.state)

    def test_cache_retirement_failure_does_not_claim_process_shutdown_failed_or_cleanup_done(self):
        job, cache = self.cached_job()
        real_tree = bridge.cache_tree
        def tree(fd, device, deadline, remove=False, budget=None, depth=0):
            if remove:
                raise PermissionError('cache deletion denied')
            return real_tree(fd, device, deadline, remove, budget, depth)
        with patch.object(bridge, 'raw_command', side_effect=self.command_response(job)), patch.object(bridge, 'cache_tree', side_effect=tree):
            result = job.perform('cache-failed-stop', {'action': 'stop'})
        self.assertFalse(result['ok'])
        self.assertTrue(result['cleanup']['stopped'])
        self.assertFalse(result['cleanup']['cleanupComplete'])
        self.assertFalse(result['cleanup']['worker']['running'])
        self.assertEqual(result['cleanup']['cache']['logicalBytesBefore'], 4101)
        self.assertEqual(result['cleanup']['cache']['logicalBytesAfter'], 4101)
        self.assertEqual(job.state['status'], 'cleanup-uncertain')
        self.assertIn('storage failure separate from the process shutdown', result['reportText'])
        self.assertTrue(cache.exists())

    def test_no_cache_is_idempotent_and_repeated_stop_does_not_retry_cleanup(self):
        job, cache = self.cached_job()
        with patch.object(bridge, 'raw_command', side_effect=self.command_response(job)) as commands:
            first = job.stop()
            count = commands.call_count
            second = job.stop()
            self.assertEqual(commands.call_count, count)
            absent = job.retire_cache({'containerId': job.cid, 'running': False})
        self.assertEqual(first, second)
        self.assertTrue(absent['complete'])
        self.assertEqual(absent['status'], 'not-present')
        self.assertEqual(absent['logicalBytesBefore'], 0)
        self.assertFalse(cache.exists())

    def test_legacy_cleanup_receipt_cannot_claim_cache_was_retired(self):
        job, cache = self.cached_job()
        old = {'stopped': True, 'errors': [], 'worker': {'containerId': job.cid, 'running': False}}
        job.state.update(status='stopped', cleanup=old)
        result = job.perform('legacy-stop', {'action': 'stop'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['cleanup'], old)
        self.assertTrue(cache.exists())
        self.assertNotIn('cleanupComplete', result['cleanup'])
        job.stop_worker.assert_not_called()

    def test_prepare_failure_before_workspace_or_worker_needs_no_cache_deletion(self):
        job = self.job()
        del job.retire_cache
        job.stop_worker.return_value = {'present': False, 'running': False}
        result = job.stop('failed')
        self.assertTrue(result['stopped'])
        self.assertTrue(result['cleanupComplete'])
        self.assertEqual(result['cache']['status'], 'not-created')

    def test_partial_cache_seed_failure_retires_partial_cache_and_preserves_scaffold(self):
        job = self.job()
        del job.retire_cache
        cid = 'c' * 64
        job.stop_worker.return_value = {'containerId': cid, 'running': False}
        def command(argv, label, seconds):
            if label == 'create-worker':
                return cid + '\n', {}
            if label == 'measure-offline-cache':
                return json.dumps({'bytes': 4096, 'files': 1, 'directories': 1}), {}
            if label == 'seed-offline-cache':
                (job.work / '.android-runtime').mkdir()
                (job.work / '.android-runtime/partial.bin').write_bytes(b'partial derived cache')
                raise bridge.Refused('cache seed failed')
            return 'guard passed', {}
        job.command.side_effect = command
        container = container_fixture(job.work, job.id)
        container['Id'] = cid
        response = self.command_response(job, container)
        container['Id'] = cid
        def raw(argv, timeout=20):
            if argv[0] == bridge.DOCKER:
                self.assertEqual(argv, [bridge.DOCKER, 'inspect', cid])
                return json.dumps([container]).encode()
            return response(argv, timeout)
        with patch.object(bridge, 'preflight', return_value={}), patch.object(bridge, 'raw_command', side_effect=raw):
            result = job.perform('partial-prepare', {'action': 'prepare'})
        self.assertFalse(result['ok'])
        self.assertTrue(result['cleanup']['cleanupComplete'])
        self.assertEqual(result['cleanup']['cache']['status'], 'removed')
        self.assertFalse((job.work / '.android-runtime').exists())
        self.assertEqual((job.work / 'project/build.gradle').read_text(), bridge.SCAFFOLD['build.gradle'])
        self.assertEqual(job.state['status'], 'failed')

    def test_descriptor_walk_refuses_cross_device_directory(self):
        job, cache = self.cached_job()
        fd = os.open(cache, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        real_stat = os.stat
        def changed_stat(path, *args, **kwargs):
            value = real_stat(path, *args, **kwargs)
            if path == 'gradle':
                values = list(value)
                values[2] += 1
                return os.stat_result(values)
            return value
        try:
            with patch.object(bridge.os, 'stat', side_effect=changed_stat), self.assertRaisesRegex(bridge.Refused, 'filesystem boundary'):
                bridge.cache_tree(fd, os.fstat(fd).st_dev, time.monotonic() + 5, remove=True)
        finally:
            os.close(fd)
        self.assertTrue((cache / 'gradle/dependency.bin').exists())


class ReportAccessTests(SandboxCase):
    def test_mutation_receipt_exposes_only_fixed_owned_report_path(self):
        job = self.job()
        job.prepare = Mock(return_value={"summary": "prepared"})
        result = job.perform("prepare-with-report", {"action": "prepare"})
        self.assertEqual(result["reportPath"], str(job.path / "report.md"))
        self.assertNotIn("reportText", result)
        self.assertEqual(result, job.perform("prepare-with-report", {"action": "prepare"}))
        self.assertEqual(job.prepare.call_count, 1)
        receipt = json.loads(next(job.path.glob("request-*.json")).read_text())
        self.assertEqual(receipt["result"]["reportPath"], result["reportPath"])

    def test_status_reads_current_rendered_prose_without_exposing_raw_events(self):
        job = self.job()
        job.state["status"] = "built"
        job.state["events"] = [{"action": "build", "reason": "I checked the build.", "result": {"ok": False, "summary": "Build did not finish.", "unrelatedRawField": "RAW-PRIVATE-SENTINEL"}}]
        result = job.perform("status-with-report", {"action": "status"})
        data = (job.path / "report.md").read_bytes()
        self.assertEqual(result["reportText"], data.decode())
        self.assertEqual(result["reportSha256"], bridge.digest(data))
        self.assertEqual(result["reportStatus"], "available")
        self.assertIn("I checked the build.", result["reportText"])
        self.assertNotIn("RAW-PRIVATE-SENTINEL", result["reportText"])
        self.assertNotIn("actor", result["state"])
        self.assertNotIn("events", result["state"])
        self.assertNotIn(ACTOR, result["reportText"])

    def test_stop_report_reflects_completed_cleanup_and_duplicate_is_same_receipt(self):
        job = self.job()
        result = job.perform("stop-with-report", {"action": "stop"})
        self.assertTrue(result["ok"])
        self.assertIn("The run has stopped.", result["reportText"])
        self.assertIn("resources stopped.", result["reportText"])
        self.assertEqual(result, job.perform("stop-with-report", {"action": "stop"}))
        job.stop_worker.assert_called_once()

    def test_failed_cleanup_report_preserves_failure(self):
        job = self.job()
        job.stop_worker.side_effect = bridge.Refused("worker still present")
        result = job.perform("stop-failure-with-report", {"action": "stop"})
        self.assertFalse(result["ok"])
        self.assertFalse(result["cleanup"]["stopped"])
        self.assertIn("Cleanup remains uncertain", result["reportText"])
        self.assertIn("worker still present", result["reportText"])

    def test_missing_oversized_linked_or_invalid_utf8_report_is_unavailable(self):
        for mode in ("missing", "large", "symlink", "hardlink", "utf8"):
            job = self.job()
            report = job.path / "report.md"
            report.unlink()
            if mode == "large":
                report.write_bytes(b"x" * (bridge.MAX_REPORT_BYTES + 1))
            elif mode in ("symlink", "hardlink"):
                outside = self.base / (job.id + "-outside-report.md")
                outside.write_text("outside report content must not leak")
                if mode == "symlink":
                    report.symlink_to(outside)
                else:
                    os.link(outside, report)
            elif mode == "utf8":
                report.write_bytes(b"\xff")
            original = {"ok": False, "cleanup": {"stopped": False}}
            result = bridge.with_report(job.path, original, include_text=True)
            with self.subTest(mode=mode):
                self.assertEqual(result["reportStatus"], "unavailable")
                self.assertNotIn("reportText", result)
                self.assertFalse(result["ok"])
                self.assertFalse(result["cleanup"]["stopped"])
                self.assertNotIn("reportPath", original)

    def offline_client(self, actor, action="status", job_id=None):
        params = {"action": action}
        if job_id is not None:
            params["jobId"] = job_id
        request = {"actor": actor, "requestId": "offline-report", "params": params}
        stdout = io.StringIO()
        with patch.object(bridge, "SOCKET", bridge.ROOT / "absent.sock"), patch.object(bridge.sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), patch.object(bridge.sys, "stdout", stdout):
            bridge.client()
        return json.loads(stdout.getvalue())

    def test_daemon_absent_status_and_stop_read_owned_report_without_restart(self):
        job = self.job()
        job.stop()
        for action in ("status", "stop"):
            result = self.offline_client(ACTOR, action, job.id)
            with self.subTest(action=action):
                self.assertTrue(result["ok"])
                self.assertEqual(result["jobId"], job.id)
                self.assertEqual(result["reportPath"], str(job.path / "report.md"))
                self.assertIn("The run has stopped.", result["reportText"])
                self.assertNotIn("events", result["state"])
                self.assertNotIn("actor", result["state"])

    def test_daemon_absent_report_does_not_cross_session_ownership(self):
        job = self.job()
        job.stop()
        with self.assertRaisesRegex(bridge.Refused, 'not owned'):
            self.offline_client(OTHER, job_id=job.id)
        result = self.offline_client(OTHER)
        self.assertEqual(result['status'], 'none')
        self.assertNotIn('reportText', result)

    def test_daemon_absent_uncertain_cleanup_report_is_accessible_with_failure(self):
        job = self.job()
        job.stop_worker.side_effect = bridge.Refused("owned worker not stopped")
        job.stop()
        result = self.offline_client(ACTOR, job_id=job.id)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reportStatus"], "available")
        self.assertIn("Cleanup remains uncertain", result["reportText"])
        self.assertNotIn("events", result["state"])

    def test_terminal_prepare_refusal_still_links_its_report(self):
        controller = bridge.Controller()
        job = self.job()
        job.state["status"] = "failed"
        controller.jobs[job.id] = job
        result = controller.dispatch({"actor": ACTOR, "requestId": "new-prepare", "params": {"action": "prepare"}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["reportPath"], str(job.path / "report.md"))
        self.assertNotIn("reportText", result)


class RetainedServiceTests(SandboxCase):
    def request(self, actor=ACTOR, action='status', **params):
        return {'actor': actor, 'requestId': 'retained-read', 'params': {'action': action, **params}}

    def offline(self, request):
        output = io.StringIO()
        with patch.object(bridge, 'SOCKET', bridge.ROOT / 'absent.sock'), patch.object(bridge.sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), patch.object(bridge.sys, 'stdout', output):
            bridge.client()
        return json.loads(output.getvalue())

    def stored(self):
        job = self.job()
        job.state.update(actions=39, builds=3, writes=8, sourceRevision=8)
        job.stop('failed')
        return job

    def active(self, controller, actor=OTHER):
        job = self.job()
        job.actor = actor
        job.state.update(actor=actor, status='ready')
        job.persist()
        controller.jobs[job.id] = job
        return job

    def files(self, job):
        return {p.relative_to(job.path): p.read_bytes() for p in job.path.rglob('*') if p.is_file()}

    def test_prior_daemon_report_access_while_other_actor_runs_preserves_both_jobs(self):
        old = self.stored()
        controller = bridge.Controller()
        active = self.active(controller)
        before, active_before = self.files(old), copy.deepcopy(active.state)
        with patch.object(bridge, 'Job', side_effect=AssertionError('retained Job adopted')):
            for action in ('status', 'stop'):
                for explicit in (False, True):
                    request = self.request(action=action, **({'jobId': old.id} if explicit else {}))
                    result = controller.dispatch(request)
                    with self.subTest(action=action, explicit=explicit):
                        self.assertTrue(result['ok'])
                        self.assertTrue(result['retained'])
                        self.assertEqual(result['jobId'], old.id)
                        self.assertEqual(result['reportText'], before[Path('report.md')].decode())
                        self.assertNotIn('actor', result['state'])
                        self.assertNotIn('events', result['state'])
                        self.assertEqual(result['state']['actions'], 39)
                        self.assertEqual(self.files(old), before)
                        self.assertEqual(active.state, active_before)
                        self.assertEqual(controller.jobs, {active.id: active})
        self.assertFalse(active.cancelled.is_set())
        active.stop_worker.assert_not_called()
        active.command.assert_not_called()

    def test_current_job_takes_precedence_and_performs_its_normal_actions(self):
        old = self.stored()
        controller = bridge.Controller()
        active = self.active(controller, ACTOR)
        expected = {'ok': True, 'current-job': active.id}
        active.perform = Mock(return_value=expected)
        for action in ('status', 'stop', 'build'):
            with patch.object(bridge, 'retained_job', side_effect=AssertionError('current job bypassed')):
                self.assertEqual(controller.dispatch(self.request(action=action)), expected)
        self.assertEqual(active.perform.call_count, 3)
        self.assertEqual(set(controller.jobs), {active.id})
        self.assertTrue(old.path.exists())

    def test_foreign_and_unknown_explicit_jobs_refused_without_report_access(self):
        old = self.stored()
        controller = bridge.Controller()
        active = self.active(controller)
        with patch.object(bridge, 'with_report', side_effect=AssertionError('unauthorized report read')):
            for job_id in (old.id, 'am-' + 'f' * 32):
                for action in ('status', 'stop'):
                    request = self.request(actor=OTHER, action=action, jobId=job_id)
                    with self.subTest(action=action, job_id=job_id), self.assertRaisesRegex(bridge.Refused, 'not owned'):
                        controller.dispatch(request)
        self.assertFalse(active.cancelled.is_set())

    def test_explicit_job_lookup_does_not_read_other_jobs(self):
        old = self.stored()
        unrelated = bridge.ROOT / 'jobs' / ('am-' + 'f' * 32)
        unrelated.mkdir()
        (unrelated / 'state.json').write_text('invalid JSON must not be consulted')
        controller = bridge.Controller()
        self.assertTrue(controller.dispatch(self.request(jobId=old.id))['ok'])
        self.assertTrue(self.offline(self.request(jobId=old.id))['ok'])

    def test_terminal_read_matches_online_and_offline_cleanup_truth(self):
        old = self.stored()
        original = copy.deepcopy(old.state)
        cases = ('complete', 'legacy', 'missing-cache', 'cache-unfinished', 'cleanup-unfinished', 'resources-running', 'errors', 'uncertain-status',
                 'missing-worker', 'running-worker', 'uncertain-emulator', 'emulator-errors', 'emulator-survivors')
        for mode in cases:
            old.state = copy.deepcopy(original)
            cleanup = old.state['cleanup']
            if mode == 'legacy':
                cleanup.pop('cleanupComplete')
                cleanup.pop('cache')
            elif mode == 'missing-cache': cleanup.pop('cache')
            elif mode == 'cache-unfinished': cleanup['cache']['complete'] = False
            elif mode == 'cleanup-unfinished': cleanup['cleanupComplete'] = False
            elif mode == 'resources-running': cleanup['stopped'] = False
            elif mode == 'errors': cleanup['errors'] = ['storage operation failed']
            elif mode == 'uncertain-status': old.state['status'] = 'cleanup-uncertain'
            elif mode == 'missing-worker': cleanup.pop('worker')
            elif mode == 'running-worker': cleanup['worker']['running'] = True
            elif mode == 'uncertain-emulator': cleanup['emulator'] = {'status': 'cleanup-unconfirmed'}
            elif mode == 'emulator-errors': cleanup['emulator'] = {'status': 'stopped', 'errors': ['owned process failed to stop']}
            elif mode == 'emulator-survivors': cleanup['emulator'] = {'status': 'stopped', 'survivingProcessGroups': [123]}
            old.persist()
            for action in ('status', 'stop'):
                request = self.request(action=action, jobId=old.id)
                online = bridge.Controller().dispatch(request)
                offline = self.offline(request)
                with self.subTest(mode=mode, action=action):
                    self.assertEqual(online, offline)
                    self.assertEqual(online['ok'], mode == 'complete')
                    self.assertEqual(online['reportStatus'], 'available')
                    self.assertEqual(online['state']['cleanup'], cleanup)
                    if action == 'stop': self.assertEqual(online['cleanup'], cleanup)
                    if mode != 'complete': self.assertIn('do not confirm full cleanup', online['summary'])

    def test_unfinished_retained_job_refuses_read_and_prepare_without_adoption(self):
        old = self.stored()
        for status in ('preparing', 'ready', 'built', 'testing'):
            old.state['status'] = status
            old.persist()
            before = self.files(old)
            for action in ('status', 'stop', 'prepare'):
                params = {} if action == 'prepare' else {'jobId': old.id}
                request = self.request(action=action, **params)
                with self.subTest(status=status, action=action), patch.object(bridge, 'Job', side_effect=AssertionError('orphan adopted')):
                    # stop legitimately leaves a cancellation tombstone; use a
                    # separate actor-independent lookup to check unfinished data.
                    if action == 'prepare':
                        bridge.cancellation_path(ACTOR).unlink(missing_ok=True)
                    with self.assertRaisesRegex(bridge.Refused, 'unfinished'):
                        bridge.Controller().dispatch(request)
                    if action == 'prepare':
                        bridge.cancellation_path(ACTOR).unlink(missing_ok=True)
                    with self.assertRaisesRegex(bridge.Refused, 'unfinished'):
                        self.offline(request)
                    self.assertEqual(self.files(old), before)

    def test_retained_mutations_are_never_replayed(self):
        old = self.stored()
        before = self.files(old)
        controller = bridge.Controller()
        with patch.object(bridge, 'Job', side_effect=AssertionError('old job adopted')):
            for action in ('build', 'start_test', 'observe'):
                with self.subTest(action=action), self.assertRaises(bridge.Refused):
                    controller.dispatch(self.request(action=action, jobId=old.id))
        self.assertEqual(self.files(old), before)
        self.assertEqual(controller.jobs, {})

    def test_repeated_prepare_after_daemon_restart_cannot_reset_session_limits(self):
        old = self.stored()
        self.assertFalse(bridge.cancellation_path(ACTOR).exists())
        before = self.files(old)
        controller = bridge.Controller()
        with patch.object(bridge, 'Job', side_effect=AssertionError('budgets reset')):
            online = controller.dispatch(self.request(action='prepare'))
            offline = self.offline(self.request(action='prepare'))
        self.assertEqual(online, offline)
        self.assertFalse(online['ok'])
        self.assertFalse(online['retryAllowed'])
        self.assertEqual(online['jobId'], old.id)
        self.assertEqual(self.files(old), before)
        self.assertEqual(controller.jobs, {})

    def test_implicit_selection_uses_creation_time_and_stable_job_tiebreak(self):
        jobs = [self.stored(), self.stored(), self.stored()]
        for job, created in zip(jobs, (30, 10, 30)):
            job.state['createdAt'] = created
            job.persist()
        expected = max((jobs[0], jobs[2]), key=lambda job: job.id)
        controller = bridge.Controller()
        self.assertEqual(controller.dispatch(self.request())['jobId'], expected.id)
        self.assertEqual(self.offline(self.request())['jobId'], expected.id)

    def test_invalid_request_refused_before_retained_read_or_cancellation(self):
        old = self.stored()
        invalid = [self.request(action='stop', jobId=old.id) for _ in range(4)]
        invalid[0]['requestId'] = '../bad'
        invalid[1]['unexpected'] = 'value'
        invalid[2]['params']['path'] = '/untrusted'
        invalid[3]['actor'] = 'untrusted'
        with patch.object(bridge, 'retained_job', side_effect=AssertionError('invalid read')), patch.object(bridge, 'cancel_actor', side_effect=AssertionError('invalid cancellation')):
            for request in invalid:
                with self.subTest(request=request):
                    with self.assertRaises(bridge.Refused): bridge.Controller().dispatch(request)
                    with self.assertRaises(bridge.Refused): self.offline(request)

    def test_mismatched_job_identity_and_corrupt_state_fail_closed(self):
        old = self.stored()
        state_path = old.path / 'state.json'
        original = state_path.read_bytes()
        invalid = [b'{', b'[]']
        for changed in ({'jobId': 'am-' + 'f' * 32}, {'createdAt': True}, {'createdAt': float('inf')}, {'createdAt': -1}):
            value = json.loads(original)
            value.update(changed)
            invalid.append(json.dumps(value).encode())
        with patch.object(bridge, 'with_report', side_effect=AssertionError('invalid state exposed report')):
            for data in invalid:
                state_path.write_bytes(data)
                with self.subTest(data=data[:100]):
                    with self.assertRaises((bridge.Refused, ValueError)):
                        bridge.Controller().dispatch(self.request(jobId=old.id))

    def test_linked_state_and_job_directory_fail_closed(self):
        for mode in ('state-symlink', 'state-hardlink', 'directory-symlink'):
            old = self.stored()
            target = self.base / (old.id + '-outside')
            path = old.path / 'state.json'
            if mode == 'directory-symlink':
                old.path.rename(target)
                old.path.symlink_to(target, target_is_directory=True)
            else:
                path.rename(target)
                if mode == 'state-symlink': path.symlink_to(target)
                else: os.link(target, path)
            with self.subTest(mode=mode), patch.object(bridge, 'with_report', side_effect=AssertionError('linked report read')):
                with self.assertRaises(bridge.Refused):
                    bridge.Controller().dispatch(self.request(jobId=old.id))

    def test_missing_retained_report_does_not_invent_or_rewrite_evidence(self):
        old = self.stored()
        (old.path / 'report.md').unlink()
        before = self.files(old)
        result = bridge.Controller().dispatch(self.request(jobId=old.id))
        self.assertTrue(result['ok'])
        self.assertEqual(result['reportStatus'], 'unavailable')
        self.assertNotIn('reportText', result)
        self.assertEqual(self.files(old), before)


class ReconciliationAdmissionTests(SandboxCase):
    def setUp(self):
        super().setUp()
        p = patch.object(bridge, 'OPERATOR_UID', os.getuid())
        p.start()
        self.addCleanup(p.stop)
        self.invocations = []
        self.failure = None
        self.mutate = None
        self.process_listing = None
        self.containers = {}
        p = patch.object(bridge.subprocess, 'run', side_effect=self.inventory)
        p.start()
        self.addCleanup(p.stop)
        p = patch.object(bridge.os, 'killpg', side_effect=AssertionError('old PID signalled'))
        p.start()
        self.addCleanup(p.stop)

    def uncertain(self):
        job = self.job()
        self.sources(job)
        value = job.work.stat()
        cid = bridge.digest(job.id.encode())
        job.state.update(status='cleanup-uncertain', actor=ACTOR, containerId=cid, workspace=str(job.work),
                         workspaceIdentity={'device': value.st_dev, 'inode': value.st_ino},
                         builds=3, writes=8, actions=40, emulatorStatus='cleanup-unconfirmed',
                         cleanup={'stopped': False, 'cleanupComplete': False, 'errors': ['emulator cleanup unconfirmed'],
                                  'worker': {'containerId': cid, 'running': False}, 'cache': {'complete': True},
                                  'emulator': {'status': 'cleanup-unconfirmed', 'ownedProcesses': [{'pid': 34522}, {'pid': 34518}],
                                               'survivingProcessGroups': ['34522 35282']}})
        (job.work / '.devlab-volume-id').write_text(bridge.MARKER + '\n')
        job.persist()
        c = container_fixture(job.work, job.id)
        c['Id'] = cid
        self.containers[cid] = c
        return job

    def marker(self, job):
        state = job.state
        marker = {'schemaVersion': 1, 'purpose': bridge.RECONCILIATION_PURPOSE, 'operatorUid': bridge.OPERATOR_UID,
                  'jobId': job.id, 'stateSha256': bridge.digest((job.path / 'state.json').read_bytes()),
                  'actorSha256': bridge.digest(state['actor'].encode()), 'containerId': state['containerId'],
                  'workspaceIdentity': state['workspaceIdentity'], 'operatorProofSha256': 'e' * 64,
                  'recordedAt': time.time() - 1, 'resourcesConfirmedStopped': True}
        path = bridge.ROOT / 'operator-reconciliations' / (job.id + '.json')
        path.parent.mkdir(mode=0o700, exist_ok=True)
        bridge.atomic(path, marker)
        return path

    def inventory(self, argv, **kwargs):
        self.invocations.append(argv)
        self.assertEqual(kwargs['env'], bridge.ENV)
        self.assertGreater(kwargs['timeout'], 0)
        self.assertLessEqual(kwargs['timeout'], 3)
        output, code, error = b'', 0, b''
        if self.failure == 'timeout':
            raise bridge.subprocess.TimeoutExpired(argv, 1)
        if argv[0] == '/usr/sbin/diskutil':
            output = bridge.plistlib.dumps({'VolumeUUID': 'wrong' if self.failure == 'volume' else bridge.VOLUME_UUID,
                                           'MountPoint': str(bridge.VOLUME), 'TotalSize': 2147442688})
        elif argv == ['/bin/ps', '-axo', 'uid=,pid=,pgid=,command=']:
            if getattr(self, 'process_listing', None) is not None: output = self.process_listing
            elif self.failure == 'pid': output = b'502 34522 1 original-leader\n'
            elif self.failure == 'group': output = b'502 99999 34518 descendant\n'
            elif self.failure == 'survivor': output = b'502 35282 1 prior-survivor\n'
            elif self.failure == 'sdk': output = ('502 999 999 ' + str(bridge.ROOT.parent / 'offline-toolchains/android-emulator-v1/sdk/emulator/qemu') + '\n').encode()
            elif self.failure == 'job-process': output = ('502 999 999 reader ' + str(bridge.ROOT / 'jobs') + '/job/file\n').encode()
            elif self.failure == 'process-format': output = b'not a process inventory\n'
        elif argv[0] == '/usr/sbin/lsof':
            self.assertEqual(argv, ['/usr/sbin/lsof', '-nP', '-iTCP:5038', '-iTCP:5580', '-iTCP:5581', '-iTCP:5582', '-sTCP:LISTEN'])
            code = 1
            if self.failure == 'ports': code, output = 0, b'LISTEN\n'
            elif self.failure == 'port-error': error = b'observation denied'
        elif argv[:2] == [bridge.DOCKER, 'ps']:
            self.assertEqual(argv, [bridge.DOCKER, 'ps', '--filter', 'label=org.openclaw.android-job', '--format', '{{.ID}}'])
            if self.failure == 'labelled-worker': output = b'another-controller-worker\n'
            elif self.failure == 'docker-error': code, error = 1, b'docker unavailable'
        elif argv[:2] == [bridge.DOCKER, 'inspect']:
            self.assertEqual(len(argv), 3)
            output = json.dumps([self.containers[argv[2]]]).encode()
            if self.mutate:
                self.mutate()
        else:
            raise AssertionError('Not an authorized read-only inventory: ' + repr(argv))
        return bridge.subprocess.CompletedProcess(argv, code, output, error)

    def test_reconciled_history_allows_new_actor_but_preserves_original_failure_and_limits(self):
        old = self.uncertain()
        self.marker(old)
        before = {p.name: p.read_bytes() for p in old.path.iterdir() if p.is_file()}
        checked = bridge.history_admission()
        self.assertEqual(len(checked), 1)
        self.assertTrue(checked[0]['resourcesConfirmedStopped'])
        self.assertEqual(checked[0]['originalStatus'], 'cleanup-uncertain')
        controller = bridge.Controller()
        with patch.object(bridge.Job, 'prepare', return_value={'summary': 'fake new preparation'}):
            fresh = controller.dispatch({'actor': OTHER, 'requestId': 'new-actor', 'params': {'action': 'prepare'}})
        self.assertTrue(fresh['ok'])
        self.assertNotEqual(fresh['jobId'], old.id)
        self.assertNotIn(old.id, controller.jobs)
        self.assertEqual(before, {p.name: p.read_bytes() for p in old.path.iterdir() if p.is_file()})
        refusal = controller.dispatch({'actor': ACTOR, 'requestId': 'old-actor', 'params': {'action': 'prepare'}})
        self.assertFalse(refusal['ok'])
        self.assertFalse(refusal['retryAllowed'])
        for action in ('status', 'stop'):
            receipt = controller.dispatch({'actor': ACTOR, 'requestId': action, 'params': {'action': action, 'jobId': old.id}})
            self.assertFalse(receipt['ok'])
            self.assertFalse(receipt['state']['cleanup']['cleanupComplete'])
            self.assertEqual(receipt['state']['actions'], 40)
        self.assertEqual(before, {p.name: p.read_bytes() for p in old.path.iterdir() if p.is_file()})

    def test_missing_marker_blocks_before_observing_or_creating_jobs(self):
        old = self.uncertain()
        with patch.object(bridge, 'Job', side_effect=AssertionError('history adopted')), self.assertRaisesRegex(bridge.Refused, old.id + ' requires operator reconciliation'):
            bridge.history_admission()
        self.assertEqual(self.invocations, [])

    def test_nonterminal_state_is_never_admitted_even_with_matching_marker(self):
        old = self.uncertain()
        for status in ('preparing', 'ready', 'built', 'testing'):
            old.state['status'] = status
            old.persist()
            self.marker(old)
            with self.subTest(status=status), self.assertRaisesRegex(bridge.Refused, 'unfinished'):
                bridge.history_admission()
        self.assertEqual(self.invocations, [])

    def test_every_uncertain_record_requires_its_own_marker(self):
        first, second = self.uncertain(), self.uncertain()
        self.marker(first)
        with self.assertRaisesRegex(bridge.Refused, second.id + ' requires operator reconciliation'):
            bridge.history_admission()

    def test_stale_state_marker_and_mismatched_actor_worker_or_workspace_fail_closed(self):
        old = self.uncertain()
        path = self.marker(old)
        original = json.loads(path.read_text())
        for change in ({'stateSha256': 'a' * 64}, {'actorSha256': 'a' * 64}, {'containerId': 'a' * 64},
                       {'jobId': 'am-' + 'a' * 32}, {'workspaceIdentity': {'device': 1, 'inode': 1}},
                       {'resourcesConfirmedStopped': False}, {'schemaVersion': True}, {'operatorUid': bridge.OPERATOR_UID + 1},
                       {'recordedAt': time.time() + 86400}, {'command': 'approve anything'}):
            bridge.atomic(path, dict(original, **change))
            with self.subTest(change=change), self.assertRaises(bridge.Refused):
                bridge.history_admission()

    def test_marker_and_state_changes_during_observation_invalidate_admission(self):
        for what in ('state', 'marker'):
            old = self.uncertain()
            marker = self.marker(old)
            target = old.path / 'state.json' if what == 'state' else marker
            original = target.read_bytes()
            self.mutate = lambda: target.write_bytes(original + b'\n')
            with self.subTest(what=what), self.assertRaisesRegex(bridge.Refused, 'changed during admission'):
                bridge.verify_operator_reconciliation(old.path / 'state.json')
            self.mutate = None

    def test_linked_or_nonprivate_markers_cannot_authorize_admission(self):
        for mode in ('symlink', 'hardlink', 'public', 'writable-parent'):
            old = self.uncertain()
            path = self.marker(old)
            if mode in ('symlink', 'hardlink'):
                target = self.base / (old.id + '-marker')
                path.rename(target)
                if mode == 'symlink': path.symlink_to(target)
                else: os.link(target, path)
            elif mode == 'public': path.chmod(0o644)
            else: path.parent.chmod(0o777)
            with self.subTest(mode=mode), self.assertRaises((bridge.Refused, OSError)):
                bridge.verify_operator_reconciliation(old.path / 'state.json')
            path.parent.chmod(0o700)

    def test_marker_owned_by_another_account_is_refused(self):
        old = self.uncertain()
        marker = self.marker(old)
        inode = marker.stat().st_ino
        original = os.fstat
        def fstat(fd):
            value = original(fd)
            if value.st_ino == inode:
                values = list(value)
                values[4] += 1
                return os.stat_result(values)
            return value
        with patch.object(bridge.os, 'fstat', side_effect=fstat), self.assertRaisesRegex(bridge.Refused, 'unsafe operator evidence file'):
            bridge.history_admission()

    def test_unknown_or_live_resources_and_failed_inventories_refuse(self):
        old = self.uncertain()
        self.marker(old)
        for failure in ('timeout', 'volume', 'pid', 'group', 'survivor', 'sdk', 'job-process', 'process-format', 'ports', 'port-error', 'labelled-worker', 'docker-error'):
            self.failure = failure
            with self.subTest(failure=failure), self.assertRaises(bridge.Refused):
                bridge.history_admission()

    def test_signed_uid_is_valid_but_does_not_relax_pid_group_or_ownership_checks(self):
        old = self.uncertain()
        self.marker(old)
        self.process_listing = b'-2 779 779 /usr/sbin/unrelated-service\n'
        checked = bridge.history_admission()
        self.assertEqual(checked[0]['observedOwnedPidsOrGroups'], [])
        self.assertTrue(checked[0]['resourcesConfirmedStopped'])
        for row in (b'-2 -779 779 unrelated\n', b'-2 779 -779 unrelated\n', b'uid 779 779 unrelated\n',
                    b'-2 34522 779 retained-leader\n', b'-2 779 34518 retained-group\n',
                    b'-2 35282 779 retained-survivor\n'):
            self.process_listing = row
            with self.subTest(row=row), self.assertRaises(bridge.Refused):
                bridge.history_admission()

    def test_exact_worker_boundary_and_running_state_are_required(self):
        old = self.uncertain()
        self.marker(old)
        base = self.containers[old.state['containerId']]
        for mode in ('running', 'image', 'cid', 'label', 'mount', 'network'):
            container = copy.deepcopy(base)
            if mode == 'running': container['State']['Running'] = True
            elif mode == 'image': container['Image'] = 'sha256:untrusted'
            elif mode == 'cid': container['Id'] = 'a' * 64
            elif mode == 'label': container['Config']['Labels']['org.openclaw.android-job'] = 'foreign'
            elif mode == 'mount': container['Mounts'][0]['Source'] = '/untrusted'
            else: container['HostConfig']['NetworkMode'] = 'host'
            self.containers[old.state['containerId']] = container
            with self.subTest(mode=mode), self.assertRaises(bridge.Refused):
                bridge.history_admission()

    def test_cache_and_symlink_or_replaced_workspace_refuse_without_deletion(self):
        for mode in ('cache', 'cache-symlink', 'workspace-symlink', 'workspace-identity', 'volume-marker'):
            old = self.uncertain()
            self.marker(old)
            cache = old.work / '.android-runtime'
            if mode == 'cache': cache.mkdir()
            elif mode == 'cache-symlink': cache.symlink_to(self.base / 'absent-target')
            elif mode == 'workspace-symlink':
                moved = self.base / ('outside-' + old.id)
                old.work.rename(moved)
                old.work.symlink_to(moved, target_is_directory=True)
            elif mode == 'workspace-identity':
                old.state['workspaceIdentity']['inode'] += 1
                old.persist()
                self.marker(old)
            else: (old.work / '.devlab-volume-id').write_text('wrong')
            with self.subTest(mode=mode), self.assertRaises((bridge.Refused, OSError)):
                bridge.verify_operator_reconciliation(old.path / 'state.json')
            if mode.startswith('cache'): self.assertTrue(os.path.lexists(cache))

    def test_incomplete_recorded_ownership_cannot_create_authority(self):
        for field in ('containerId', 'workspaceIdentity', 'cleanup'):
            old = self.uncertain()
            self.marker(old)
            del old.state[field]
            old.persist()
            path = bridge.ROOT / 'operator-reconciliations' / (old.id + '.json')
            marker = json.loads(path.read_text())
            marker['stateSha256'] = bridge.digest((old.path / 'state.json').read_bytes())
            bridge.atomic(path, marker)
            with self.subTest(field=field), self.assertRaises(bridge.Refused):
                bridge.verify_operator_reconciliation(old.path / 'state.json')

    def test_current_daemon_uncertain_job_remains_blocking_despite_disk_marker(self):
        old = self.uncertain()
        self.marker(old)
        controller = bridge.Controller()
        controller.jobs[old.id] = old
        with self.assertRaisesRegex(bridge.Refused, 'prior cleanup needs operator review'):
            controller.dispatch({'actor': OTHER, 'requestId': 'new', 'params': {'action': 'prepare'}})
        self.assertEqual(set(controller.jobs), {old.id})

    def test_client_surfaces_historical_block_before_spawn_or_poll(self):
        old = self.uncertain()
        request = {'actor': OTHER, 'requestId': 'fresh-prepare', 'params': {'action': 'prepare'}}
        with patch.object(bridge, 'SOCKET', bridge.ROOT / 'absent.sock'), patch.object(bridge.sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))), patch.object(bridge.subprocess, 'Popen') as spawn, patch.object(bridge.time, 'sleep', side_effect=AssertionError('startup poll reached')):
            with self.assertRaisesRegex(bridge.Refused, old.id + ' requires operator reconciliation'):
                bridge.client()
            spawn.assert_not_called()
        self.assertEqual(self.invocations, [])
        self.assertFalse(bridge.cancellation_path(OTHER).exists())



class ReceiptProgressTests(SandboxCase):
    def test_failed_build_returns_recovered_status_and_consumed_attempt(self):
        job = self.job()
        self.sources(job)
        job.state.update(writes=1, lastWrittenSourceRevision=1)
        job.command.side_effect = bridge.Refused('MainActivity.java:6: error: cannot find symbol')
        result = job.perform('first-build', {'action': 'build'})
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'source-ready')
        self.assertEqual(result['status'], job.state['status'])
        self.assertEqual(result['progress']['sourceRevision'], 1)
        self.assertIsNone(result['progress']['qualifiedApkSourceRevision'])
        self.assertEqual(result['progress']['buildsRemaining'], 2)
        self.assertEqual(result['progress']['writesRemaining'], 7)
        self.assertEqual(result['progress']['actionsRemaining'], 39)
        self.assertEqual(result['summary'], 'MainActivity.java:6: error: cannot find symbol')
        recorded = json.loads((job.path / 'state.json').read_text())
        self.assertEqual(recorded['events'][-1]['result'], result)
        self.assertEqual(recorded['status'], 'source-ready')
        job.stop_worker.assert_called_once()

    def test_prepare_failure_reports_final_cleanup_status(self):
        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails):
                job = self.job()
                job.prepare = Mock(side_effect=bridge.Refused('preparation failed'))
                if cleanup_fails:
                    job.stop_worker.side_effect = bridge.Refused('worker stop not confirmed')
                result = job.perform('failed-prepare', {'action': 'prepare'})
                self.assertFalse(result['ok'])
                self.assertEqual(result['status'], 'cleanup-uncertain' if cleanup_fails else 'failed')
                self.assertEqual(result['progress']['controllerSecondsRemaining'], 0)
                self.assertIn('does not confirm cleanup', result['progressSummary'])

    def test_new_source_write_invalidates_recorded_qualification(self):
        job = self.job()
        self.qualify(job)
        job.build_receipt.update({key: {'exitCode': 0} for key in ('build', 'signature', 'packageCheck')})
        job.state.update(apk=job.build_receipt, writes=1, actions=2, lastWrittenSourceRevision=1)
        before = job.perform('before-repair', {'action': 'status'})
        self.assertEqual(before['progress']['qualifiedApkSourceRevision'], 1)
        source = 'package org.openclaw.trial; public class MainActivity { int revision = 2; }'
        def write(argv, label, seconds, data):
            files = json.loads(data)
            self.assertEqual(label, 'write-java')
            for item in files:
                (job.work / 'project/app/src/main/java/org/openclaw/trial' / item['name']).write_text(item['content'])
            return 'written', {}
        job.command.side_effect = write
        result = job.perform('repair', {'action': 'write_sources', 'files': [{'name': 'MainActivity.java', 'content': source}]})
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'source-ready')
        self.assertEqual(result['progress']['sourceRevision'], 2)
        self.assertIsNone(result['progress']['qualifiedApkSourceRevision'])
        self.assertEqual(result['progress']['writesRemaining'], 6)
        self.assertEqual(result['progress']['buildsRemaining'], 2)
        self.assertEqual(result['progress']['actionsRemaining'], 37)
        self.assertIn('successful build is still required', result['progressSummary'])
        self.assertEqual((job.path / 'source-2/MainActivity.java').read_text(), source)
        self.assertTrue((job.path / 'app-build-1.apk').exists())

    def test_qualification_requires_current_revision_and_all_successful_checks(self):
        job = self.job()
        job.state.update(status='built', sourceRevision=2)
        good = {'sourceRevision': 2, **{key: {'exitCode': 0} for key in ('build', 'signature', 'packageCheck')}}
        for mode in ('current', 'stale', 'missing-check', 'failed-check', 'boolean-exit'):
            apk = copy.deepcopy(good)
            if mode == 'stale': apk['sourceRevision'] = 1
            elif mode == 'missing-check': del apk['signature']
            elif mode == 'failed-check': apk['packageCheck']['exitCode'] = 1
            elif mode == 'boolean-exit': apk['build']['exitCode'] = False
            job.state['apk'] = apk
            with self.subTest(mode=mode):
                result = job.perform('qualification', {'action': 'status'})
                self.assertEqual(result['progress']['qualifiedApkSourceRevision'], 2 if mode == 'current' else None)
        job.command.assert_not_called()

    def test_status_refreshes_monotonic_clock_without_spending_actions(self):
        job = self.job()
        job.state.update(status='source-ready', writes=2, builds=1, actions=4, sourceRevision=2)
        job.deadline = 400
        with patch.object(bridge.time, 'monotonic', return_value=100.75), patch.object(bridge.time, 'time', return_value=9999999999):
            first = job.perform('same-status', {'action': 'status'})
        with patch.object(bridge.time, 'monotonic', return_value=140.75), patch.object(bridge.time, 'time', return_value=1):
            second = job.perform('same-status', {'action': 'status'})
        self.assertEqual(first['progress']['controllerSecondsRemaining'], 299)
        self.assertEqual(second['progress']['controllerSecondsRemaining'], 259)
        self.assertEqual(second['progress']['actionsRemaining'], 36)
        self.assertEqual(job.state['actions'], 4)
        self.assertEqual(job.deadline, 400)
        self.assertIn('separate from the chat deadline', second['progressSummary'])
        self.assertIn('chat timeout does not confirm cleanup', second['progressSummary'])

    def test_replay_keeps_original_progress_after_clock_and_state_change(self):
        job = self.job()
        def ready():
            job.update_state(status='ready')
            return {'summary': 'Prepared.'}
        job.prepare = Mock(side_effect=ready)
        job.deadline = 400
        with patch.object(bridge.time, 'monotonic', return_value=100):
            first = job.perform('prepare-once', {'action': 'prepare'})
        job.state.update(status='source-ready', sourceRevision=2, writes=2, builds=1, actions=4)
        with patch.object(bridge.time, 'monotonic', return_value=200):
            replay = job.perform('prepare-once', {'action': 'prepare'})
            current = job.perform('current', {'action': 'status'})
        self.assertEqual(replay, first)
        self.assertEqual(replay['status'], 'ready')
        self.assertEqual(replay['progress']['controllerSecondsRemaining'], 300)
        self.assertEqual(replay['progress']['sourceRevision'], 0)
        self.assertEqual(current['progress']['controllerSecondsRemaining'], 200)
        self.assertEqual(current['progress']['sourceRevision'], 2)
        self.assertEqual(job.prepare.call_count, 1)
        self.assertEqual(job.state['actions'], 4)

    def test_expired_clock_and_exhausted_counters_never_show_negative_allowances(self):
        job = self.job()
        job.state.update(status='ready', writes=8, builds=3, actions=40)
        job.deadline = time.monotonic() - 1
        result = job.perform('expired-status', {'action': 'status'})
        for key in ('writesRemaining', 'buildsRemaining', 'actionsRemaining', 'controllerSecondsRemaining'):
            self.assertEqual(result['progress'][key], 0)
        self.assertEqual(job.state['status'], 'ready', 'reading the clock must not introduce a lifecycle action')
        job.stop_worker.assert_not_called()

    def test_retained_terminal_progress_preserves_evidence_and_unknown_counts(self):
        job = self.job()
        job.state.update(status='cleanup-uncertain', sourceRevision=2, writes=2, builds=1)
        del job.state['actions']
        job.persist()
        original = {p.name: p.read_bytes() for p in job.path.iterdir() if p.is_file()}
        retained = bridge.retained_job(ACTOR, job.id)
        with patch.object(bridge, 'Job', side_effect=AssertionError('retained job adopted')), patch.object(bridge.time, 'monotonic', side_effect=AssertionError('no retained monotonic clock')):
            for action in ('status', 'stop', 'prepare'):
                with self.subTest(action=action):
                    result = bridge.retained_receipt(retained, action)
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['status'], 'cleanup-uncertain')
                    self.assertEqual(result['progress']['controllerSecondsRemaining'], 0)
                    self.assertIsNone(result['progress']['actionsRemaining'])
                    self.assertEqual(result['progress']['buildsRemaining'], 2)
                    self.assertEqual(result['progress']['writesRemaining'], 6)
                    self.assertIn('cannot continue', result['progressSummary'])
                    self.assertIn('does not confirm cleanup', result['progressSummary'])
        self.assertEqual({p.name: p.read_bytes() for p in job.path.iterdir() if p.is_file()}, original)
        job.stop_worker.assert_not_called()


class InitialTimeLimitTests(SandboxCase):
    def request(self, actor=ACTOR, rid='first-prepare', **params):
        return {'actor': actor, 'requestId': rid, 'params': {'action': 'prepare', **params}}

    def prepare(self, controller, request):
        def ready(job):
            job.update_state(status='ready')
            return {'summary': 'Prepared without external operations.'}
        with patch.object(bridge.Job, 'prepare', ready):
            result = controller.dispatch(request)
        return controller.jobs[result['jobId']], result

    def test_new_jobs_apply_default_or_explicit_bounded_duration_once(self):
        for supplied, expected in ((None, 60), (5, 5), (30, 30), (60, 60)):
            with self.subTest(supplied=supplied):
                controller = bridge.Controller()
                actor = 'agent:main:duration-%s|session' % supplied
                params = {} if supplied is None else {'timeLimitMinutes': supplied}
                with patch.object(bridge.time, 'monotonic', return_value=1000):
                    job, result = self.prepare(controller, self.request(actor=actor, **params))
                self.assertEqual(job.deadline, 1000 + expected * 60)
                self.assertEqual(job.state['timeLimitMinutes'], expected)
                self.assertEqual(job.state['deadlineSeconds'], expected * 60)
                self.assertEqual(result['progress']['controllerTimeLimitMinutes'], expected)
                self.assertEqual(result['progress']['controllerSecondsRemaining'], expected * 60)
                self.assertIn('%d minutes, fixed at preparation' % expected, result['progressSummary'])
                saved = json.loads((job.path / 'state.json').read_text())
                self.assertEqual(saved['timeLimitMinutes'], expected)
                self.assertEqual(saved['deadlineSeconds'], expected * 60)
                self.assertEqual((job.state['writes'], job.state['builds'], job.state['actions']), (0, 0, 1))
                self.assertEqual((result['progress']['writesRemaining'], result['progress']['buildsRemaining'], result['progress']['actionsRemaining']), (8, 3, 39))

    def test_invalid_limits_fail_before_job_creation_or_external_operations(self):
        for value in (True, False, 4, 61, 0, -1, 30.0, 5.5, '30', None, [], {}, float('inf'), float('nan')):
            controller = bridge.Controller()
            with self.subTest(value=value), patch.object(bridge, 'Job', side_effect=AssertionError('invalid request created job')):
                with self.assertRaisesRegex(bridge.Refused, 'timeLimitMinutes must be an integer'):
                    controller.dispatch(self.request(timeLimitMinutes=value))
            self.assertEqual(controller.jobs, {})
        self.assertFalse((bridge.ROOT / 'jobs').exists())

    def test_constructor_revalidates_duration_before_creating_evidence(self):
        for value in (True, 4, 61, 30.0, '30', None):
            with self.subTest(value=value), self.assertRaisesRegex(bridge.Refused, 'timeLimitMinutes must be an integer'):
                bridge.Job(ACTOR, time_limit_minutes=value)
        self.assertFalse((bridge.ROOT / 'jobs').exists())

    def test_limit_is_only_accepted_on_prepare(self):
        for action in ('status', 'stop', 'build', 'write_sources', 'start_test', 'tap', 'observe'):
            request = self.request(timeLimitMinutes=30)
            request['params']['action'] = action
            with self.subTest(action=action), patch.object(bridge, 'cancel_actor', side_effect=AssertionError('invalid stop mutated cancellation')):
                with self.assertRaisesRegex(bridge.Refused, 'unknown parameters refused'):
                    bridge.Controller().dispatch(request)
        self.assertFalse(bridge.ROOT.exists())

    def test_repeated_prepare_omission_preserves_sixty_minutes_and_different_limit_refuses(self):
        controller = bridge.Controller()
        with patch.object(bridge.time, 'monotonic', return_value=1000):
            job, first = self.prepare(controller, self.request(timeLimitMinutes=60))
        before = copy.deepcopy(job.state)
        files = {p.name: p.read_bytes() for p in job.path.iterdir() if p.is_file()}
        with patch.object(bridge.time, 'monotonic', return_value=1300), patch.object(job, 'prepare', side_effect=AssertionError('preparation repeated')):
            omitted = controller.dispatch(self.request(rid='omitted-limit'))
            same = controller.dispatch(self.request(rid='same-limit', timeLimitMinutes=60))
            changed = controller.dispatch(self.request(rid='changed-limit', timeLimitMinutes=30))
        self.assertTrue(omitted['ok'])
        self.assertTrue(same['ok'])
        self.assertFalse(changed['ok'])
        self.assertIn('cannot be changed', changed['summary'])
        for result in (omitted, same, changed):
            self.assertEqual(result['progress']['controllerTimeLimitMinutes'], 60)
            self.assertEqual(result['progress']['controllerSecondsRemaining'], 3300)
        self.assertEqual(job.deadline, 4600)
        self.assertEqual(job.state, before)
        self.assertEqual({p.name: p.read_bytes() for p in job.path.iterdir() if p.is_file()}, files)
        self.assertEqual(len(controller.jobs), 1)

    def test_changed_request_arguments_still_refuse_and_original_replay_is_historical(self):
        controller = bridge.Controller()
        with patch.object(bridge.time, 'monotonic', return_value=1000):
            job, first = self.prepare(controller, self.request(timeLimitMinutes=5))
        before = copy.deepcopy(job.state)
        with patch.object(bridge.time, 'monotonic', return_value=1100):
            replay = controller.dispatch(self.request(timeLimitMinutes=5))
            for params in ({'timeLimitMinutes': 60}, {}):
                with self.subTest(params=params), self.assertRaisesRegex(bridge.Refused, 'request ID reused for different arguments'):
                    controller.dispatch(self.request(**params))
        self.assertEqual(replay, first)
        self.assertEqual(replay['progress']['controllerSecondsRemaining'], 300)
        self.assertEqual(job.deadline, 1300)
        self.assertEqual(job.state, before)

    def test_preparing_job_cannot_be_extended_before_first_action_returns(self):
        controller = bridge.Controller()
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        def delayed_prepare(job):
            entered.set()
            if not release.wait(2): raise AssertionError('test did not release prepare')
            job.update_state(status='ready')
            return {'summary': 'First preparation returned.'}
        def first_request():
            try: outcomes.append(controller.dispatch(self.request(timeLimitMinutes=5)))
            except Exception as error: outcomes.append(error)
        with patch.object(bridge.Job, 'prepare', delayed_prepare):
            thread = threading.Thread(target=first_request)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                job = next(iter(controller.jobs.values()))
                deadline, before = job.deadline, copy.deepcopy(job.state)
                changed = controller.dispatch(self.request(rid='while-preparing', timeLimitMinutes=60))
                self.assertFalse(changed['ok'])
                self.assertEqual(changed['status'], 'preparing')
                self.assertEqual(changed['progress']['controllerTimeLimitMinutes'], 5)
                self.assertEqual(job.deadline, deadline)
                self.assertEqual(job.state, before)
                with self.assertRaisesRegex(bridge.Refused, 'another operation is active'):
                    controller.dispatch(self.request(rid='same-while-preparing'))
                self.assertEqual(job.deadline, deadline)
            finally:
                release.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], dict)
        self.assertTrue(outcomes[0]['ok'])
        self.assertEqual(job.state['actions'], 1)

    def test_cancelled_session_cannot_choose_a_new_duration_after_restart(self):
        original = bridge.Controller()
        original.dispatch({'actor': ACTOR, 'requestId': 'cancel', 'params': {'action': 'stop'}})
        cancellation = bridge.cancellation_path(ACTOR).read_bytes()
        for controller in (original, bridge.Controller()):
            for minutes in (5, 60):
                with self.subTest(minutes=minutes), patch.object(bridge, 'Job', side_effect=AssertionError('cancelled job created')):
                    with self.assertRaisesRegex(bridge.Refused, 'preparation was cancelled'):
                        controller.dispatch(self.request(timeLimitMinutes=minutes))
                self.assertEqual(controller.jobs, {})
        self.assertEqual(bridge.cancellation_path(ACTOR).read_bytes(), cancellation)

    def test_terminal_jobs_keep_original_time_and_budgets_when_reprepared(self):
        for status in bridge.TERMINAL:
            job = self.job()
            job.state.update(status=status, timeLimitMinutes=5, deadlineSeconds=300, writes=8, builds=3, actions=40)
            job.persist()
            controller = bridge.Controller()
            controller.jobs[job.id] = job
            deadline, before = job.deadline, copy.deepcopy(job.state)
            for params in ({}, {'timeLimitMinutes': 5}, {'timeLimitMinutes': 60}):
                with self.subTest(status=status, params=params):
                    result = controller.dispatch(self.request(rid='ended', **params))
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['progress']['controllerTimeLimitMinutes'], 5)
                    self.assertEqual(result['progress']['controllerSecondsRemaining'], 0)
                    self.assertEqual(job.state, before)
                    self.assertEqual(job.deadline, deadline)
            job.command.assert_not_called()

    def test_retained_twenty_minute_job_is_not_relabelled_or_adopted(self):
        job = self.job()
        job.state.update(deadlineSeconds=1200)
        del job.state['timeLimitMinutes']
        job.stop('failed')
        files = {p.name: p.read_bytes() for p in job.path.iterdir() if p.is_file()}
        controller = bridge.Controller()
        with patch.object(bridge, 'Job', side_effect=AssertionError('retained job adopted')):
            result = controller.dispatch(self.request(timeLimitMinutes=60))
            state = controller.dispatch({'actor': ACTOR, 'requestId': 'historical-status', 'params': {'action': 'status', 'jobId': job.id}})
        self.assertFalse(result['ok'])
        for receipt in (result, state):
            self.assertEqual(receipt['progress']['controllerTimeLimitMinutes'], 20)
            self.assertEqual(receipt['progress']['controllerSecondsRemaining'], 0)
            self.assertIn('20 minutes, fixed at preparation', receipt['progressSummary'])
        self.assertEqual(controller.jobs, {})
        self.assertEqual({p.name: p.read_bytes() for p in job.path.iterdir() if p.is_file()}, files)

    def test_missing_historical_duration_stays_unknown_instead_of_using_default(self):
        job = self.job()
        job.state.update(status='failed')
        for value in (None, True, '1200', 1200.0, -1200):
            job.state['deadlineSeconds'] = value
            result = bridge.retained_receipt((job.path, job.state), 'status')
            with self.subTest(value=value):
                self.assertIsNone(result['progress']['controllerTimeLimitMinutes'])
                self.assertIn('time limit is unknown', result['progressSummary'])

    def test_selected_deadline_expiry_still_refuses_work_and_requests_cleanup(self):
        controller = bridge.Controller()
        with patch.object(bridge.time, 'monotonic', return_value=1000):
            job, _ = self.prepare(controller, self.request(timeLimitMinutes=5))
        job.stop_worker = Mock(return_value={'running': False})
        job.retire_cache = Mock(return_value={'complete': True})
        job.build = Mock(side_effect=AssertionError('work ran past selected deadline'))
        with patch.object(bridge.time, 'monotonic', return_value=1301):
            result = controller.dispatch({'actor': ACTOR, 'requestId': 'after-deadline', 'params': {'action': 'build', 'jobId': job.id}})
        self.assertFalse(result['ok'])
        self.assertEqual(result['progress']['controllerTimeLimitMinutes'], 5)
        self.assertEqual(result['progress']['controllerSecondsRemaining'], 0)
        self.assertTrue(result['cleanup']['cleanupComplete'])
        self.assertEqual(job.deadline, 1300)
        self.assertEqual(job.state['actions'], 1)
        job.build.assert_not_called()


class TimeExtensionTests(SandboxCase):
    """Extensions add time after verified progress, never attempts or chat time."""

    def built_job(self, builds=1):
        job = self.job()
        self.qualify(job)
        job.state.update(builds=builds, lastQualifiedBuild=builds, actions=builds)
        return job

    def extend(self, job, minutes=20, rid=None, reason='Two interaction checks and the final checkpoint remain.'):
        self.calls = getattr(self, 'calls', 0) + 1
        rid = rid or 'extend-call-%d' % self.calls
        return job.perform(rid, {'action': 'extend', 'extendMinutes': minutes, 'reason': reason})

    def test_new_jobs_record_no_extensions_and_advertise_the_gate(self):
        job = self.job()
        self.assertEqual(job.state['timeLimitMinutes'], 60)
        self.assertEqual(job.state['extensions'], [])
        status = job.perform('status', {'action': 'status'})
        self.assertEqual(status['progress']['controllerExtensionMinutes'], 0)
        self.assertEqual(status['progress']['controllerExtensionsRemaining'], 3)
        self.assertIn('The extend action can add 5 to 30 minutes after a new successful build', status['progressSummary'])
        self.assertIn('never extends the chat', status['progressSummary'])

    def test_extension_requires_a_successful_build_first(self):
        job = self.job()
        self.sources(job)
        job.state.update(builds=1, writes=1)  # A failed build is an attempt, not progress.
        deadline = job.deadline
        result = self.extend(job)
        self.assertFalse(result['ok'])
        self.assertIn('requires a new successful build since preparation', result['summary'])
        self.assertEqual(job.deadline, deadline)
        self.assertEqual(job.state['extensions'], [])
        self.assertEqual((job.state['writes'], job.state['builds'], job.state['actions']), (1, 1, 1))
        self.assertFalse(job.cancelled.is_set())
        self.assertEqual(job.state['status'], 'source-ready')
        job.stop_worker.assert_not_called()

    def test_granted_extension_moves_deadline_without_changing_counters(self):
        job = self.built_job()
        deadline = job.deadline
        result = self.extend(job, 20)
        self.assertTrue(result['ok'])
        self.assertEqual(job.deadline, deadline + 1200)
        self.assertEqual((result['grantedMinutes'], result['requestedMinutes'], result['totalMinutes']), (20, 20, 80))
        self.assertIn('does not extend the chat', result['summary'])
        self.assertEqual((job.state['writes'], job.state['builds'], job.state['actions']), (0, 1, 2))
        self.assertEqual(result['progress']['buildsRemaining'], 2)
        self.assertEqual(result['progress']['controllerExtensionMinutes'], 20)
        self.assertEqual(result['progress']['controllerExtensionsRemaining'], 2)
        self.assertEqual(result['progress']['controllerTimeLimitMinutes'], 60)
        self.assertIn('60 minutes, fixed at preparation, plus 20 extension minutes granted', result['progressSummary'])
        record = json.loads((job.path / 'state.json').read_text())['extensions'][0]
        self.assertEqual((record['seconds'], record['afterQualifiedBuild']), (1200, 1))
        self.assertEqual(record['reason'], 'Two interaction checks and the final checkpoint remain.')
        self.assertEqual(job.state['deadlineSeconds'], 3600, 'the initial selection stays historical evidence')
        replay = self.extend(job, 20, rid='extend-call-1')
        self.assertEqual(replay, result)
        self.assertEqual(job.deadline, deadline + 1200)

    def test_each_further_extension_needs_another_successful_build(self):
        job = self.built_job()
        self.assertTrue(self.extend(job, 10)['ok'])
        again = self.extend(job, 10)
        self.assertFalse(again['ok'])
        self.assertIn('since the previous extension', again['summary'])
        self.assertEqual(len(job.state['extensions']), 1)
        job.state.update(builds=2, lastQualifiedBuild=2)
        self.assertTrue(self.extend(job, 10)['ok'])
        self.assertEqual(len(job.state['extensions']), 2)

    def test_total_cap_limits_grant_and_count_cap_refuses(self):
        job = self.built_job()
        deadline = job.deadline
        self.assertEqual(self.extend(job, 30)['grantedMinutes'], 30)
        job.state.update(builds=2, lastQualifiedBuild=2)
        self.assertEqual(self.extend(job, 25)['grantedMinutes'], 25)
        job.state.update(builds=3, lastQualifiedBuild=3)
        capped = self.extend(job, 30)
        self.assertTrue(capped['ok'])
        self.assertEqual((capped['grantedMinutes'], capped['totalMinutes']), (5, 120))
        self.assertIn('limited by the 120-minute total cap', capped['summary'])
        self.assertEqual(job.deadline, deadline + 3600)
        job.state.update(lastQualifiedBuild=4)
        refused = self.extend(job, 5)
        self.assertFalse(refused['ok'])
        self.assertIn('used all 3 time extensions', refused['summary'])
        self.assertEqual(job.deadline, deadline + 3600)
        self.assertEqual(refused['progress']['controllerExtensionsRemaining'], 0)

    def test_total_cap_refuses_once_reached(self):
        job = self.built_job()
        job.state['extensions'] = [{'seconds': 3600, 'afterQualifiedBuild': 0}]
        deadline = job.deadline
        result = self.extend(job, 5)
        self.assertFalse(result['ok'])
        self.assertIn('120-minute total time cap', result['summary'])
        self.assertEqual(job.deadline, deadline)

    def test_running_guest_deadline_moves_with_the_job(self):
        job = self.built_job()
        job.state['status'] = 'testing'
        guest = Mock()
        job.emulator = guest
        deadline = job.deadline
        self.assertTrue(self.extend(job, 15)['ok'])
        guest.extend_deadline.assert_called_once_with(deadline + 900)
        guest.stop.assert_not_called()
        self.assertEqual(job.state['status'], 'testing')

    def test_guest_that_cannot_extend_leaves_job_deadline_unchanged(self):
        job = self.built_job()
        job.state['status'] = 'testing'
        guest = Mock()
        guest.extend_deadline.side_effect = RuntimeError('emulator stopped')
        job.emulator = guest
        deadline = job.deadline
        result = self.extend(job, 15)
        self.assertFalse(result['ok'])
        self.assertEqual(job.deadline, deadline)
        self.assertEqual(job.state['extensions'], [])

    def test_expired_or_cancelled_jobs_cannot_be_revived(self):
        for mode in ('expired', 'cancelled'):
            job = self.built_job()
            if mode == 'expired':
                job.deadline = time.monotonic() - 1
            else:
                job.cancelled.set()
            result = self.extend(job, 30)
            with self.subTest(mode=mode):
                self.assertFalse(result['ok'])
                self.assertIn('cleanup', result)
                self.assertIn(job.state['status'], bridge.TERMINAL)
                self.assertEqual(job.state['extensions'], [])
                self.assertEqual(result['progress']['controllerExtensionsRemaining'], 0)

    def test_extension_parameters_are_validated_before_any_job_work(self):
        good = {'action': 'extend', 'jobId': 'am-' + 'a' * 32, 'extendMinutes': 10, 'reason': 'Tests remain.'}
        self.assertEqual(bridge.validate_params(dict(good)), good)
        for patch_values in ({'extendMinutes': 4}, {'extendMinutes': 31}, {'extendMinutes': True}, {'extendMinutes': 10.0},
                             {'extendMinutes': '10'}, {'reason': ''}, {'reason': '   '}, {'timeLimitMinutes': 60}):
            with self.subTest(patch_values=patch_values), self.assertRaises(bridge.Refused):
                bridge.validate_params({**good, **patch_values})
        for missing in ('extendMinutes', 'reason'):
            with self.subTest(missing=missing), self.assertRaises(bridge.Refused):
                bridge.validate_params({k: v for k, v in good.items() if k != missing})
        with self.assertRaisesRegex(bridge.Refused, 'unknown parameters'):
            bridge.validate_params({'action': 'prepare', 'extendMinutes': 10})

    def test_real_build_success_records_progress_event(self):
        job = self.job()
        self.sources(job)
        job.state.update(writes=1, lastWrittenSourceRevision=1)
        apk = job.work / 'project/app/build/outputs/apk/debug/app-debug.apk'
        apk.parent.mkdir(parents=True)
        with zipfile.ZipFile(apk, 'w') as archive:
            archive.writestr('AndroidManifest.xml', b'manifest fixture')
            archive.writestr('classes.dex', b'dex fixture')
        job.command.side_effect = [('build success', {'exitCode': 0}), ('signature success', {'exitCode': 0}),
                                   ("package: name='org.openclaw.trial'\nlaunchable-activity: name='org.openclaw.trial.MainActivity'", {'exitCode': 0})]
        self.assertTrue(job.perform('build', {'action': 'build'})['ok'])
        self.assertEqual(job.state['lastQualifiedBuild'], 1)
        self.assertTrue(self.extend(job, 5)['ok'])

    def test_controller_routes_extend_only_to_the_owning_session(self):
        controller = bridge.Controller()
        job = self.built_job()
        controller.jobs[job.id] = job
        request = {'requestId': 'route', 'params': {'action': 'extend', 'jobId': job.id, 'extendMinutes': 5, 'reason': 'Finish checks.'}}
        with self.assertRaisesRegex(bridge.Refused, 'not owned'):
            controller.dispatch({'actor': OTHER, **request})
        self.assertEqual(job.state['extensions'], [])
        self.assertTrue(controller.dispatch({'actor': ACTOR, **request})['ok'])

    def test_historical_jobs_without_extension_records_report_none(self):
        job = self.job()
        job.state.update(status='stopped')
        del job.state['extensions']
        result = bridge.retained_receipt((job.path, job.state), 'status')
        self.assertEqual(result['progress']['controllerExtensionMinutes'], 0)
        self.assertEqual(result['progress']['controllerExtensionsRemaining'], 0)
        self.assertNotIn('extend action can add', result['progressSummary'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
