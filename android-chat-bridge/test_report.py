"""Report meaning and artifact-boundary tests; no runtime code is launched."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from report import java_diagnostics, write_report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.state = {'jobId': 'am-' + 'a' * 32, 'status': 'ready', 'builds': 0,
                      'writes': 0, 'sourceRevision': 0, 'events': []}

    def tearDown(self):
        self.temp.cleanup()

    def file(self, path, content=b'evidence fixture'):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return str(target)

    def render(self, state=None):
        return write_report(self.root, self.state if state is None else state).read_text()

    def qualify(self):
        self.state.update(status='built', builds=1, writes=1, sourceRevision=1)
        self.state['apk'] = {'path': self.file('app-build-1.apk'), 'sha256': 'f' * 64, 'sourceRevision': 1,
                             'build': {'exitCode': 0, 'log': self.file('001-offline-build.log')},
                             'signature': {'exitCode': 0, 'log': self.file('001-apk-signature.log')},
                             'packageCheck': {'exitCode': 0, 'log': self.file('001-apk-package.log')}}

    def observation(self, fatal=0):
        self.file('emulator-1/run/session.json')
        return {'screenshot': {'path': self.file('emulator-1/run/001-frame.png')},
                'ui': {'status': 'unavailable', 'reason': 'idle timeout'},
                'crashes': {'path': self.file('emulator-1/run/002-crashes.log'), 'fatalMarkers': fatal}}

    def test_empty_run_has_plain_explanations_and_no_success_inference(self):
        rendered = self.render()
        self.assertIn('No completed preparation record', rendered)
        self.assertIn('no current qualified APK', rendered)
        self.assertIn('No Android launch or interaction receipt', rendered)
        self.assertIn('No final cleanup receipt', rendered)
        for section in rendered.split('\n## ')[1:]:
            body = section.split('\n', 1)[1].strip()
            self.assertTrue(body)
            self.assertFalse(body.startswith(('-', '1.', '`', '{')))

    def test_build_receipts_do_not_claim_android_or_gameplay_success(self):
        self.qualify()
        rendered = self.render()
        self.assertIn('successful controller receipts for the offline build, signature check, and package check', rendered)
        self.assertIn('Android behavior still requires separate observations', rendered)
        self.assertIn('No Android launch or interaction receipt', rendered)
        self.assertNotIn('gameplay passed', rendered.lower())
        self.assertIn('[Built Android APK](app-build-1.apk)', rendered)

    def test_missing_check_receipt_does_not_fill_in_success(self):
        self.qualify()
        del self.state['apk']['signature']
        self.assertIn('does not include all three successful build-check receipts', self.render())

    def test_old_apk_revision_does_not_qualify_new_source(self):
        self.qualify()
        self.state['sourceRevision'] = 2
        rendered = self.render()
        self.assertIn('belongs to a different source revision', rendered)
        self.assertNotIn('current APK has saved successful', rendered)

    def test_source_attempts_are_not_counted_as_successes(self):
        self.state.update(writes=2, sourceRevision=2)
        self.state['events'] = [{'action': 'write_sources', 'reason': 'Repair the collision logic.', 'result': {'ok': False, 'summary': 'source write failed'}}]
        rendered = self.render()
        self.assertIn('2 source-write attempts and 0 successful source-write receipts', rendered)
        self.assertIn('Most recent recorded failure: source write failed.', rendered)

    def build_failure(self, summary):
        self.state.update(status='source-ready', builds=1, writes=1, sourceRevision=1)
        self.state['events'] = [{'action': 'build', 'result': {'ok': False, 'summary': summary}}]

    def test_java_errors_after_gradle_boilerplate_are_explained_without_reading_logs(self):
        summary = ('> Task :app:prepareDependencies UP-TO-DATE\n' * 50
                   + '/workspace/project/app/src/main/java/org/openclaw/trial/MainActivity.java:42: error: cannot find symbol\n'
                   + '/workspace/project/app/src/main/java/org/openclaw/trial/BirdView.java:91: error: incompatible types: int cannot be converted to Paint\n')
        self.assertGreater(summary.index('MainActivity.java'), 1200)
        self.build_failure(summary)
        before = copy.deepcopy(self.state)
        with patch.object(Path, 'read_text', side_effect=AssertionError('evidence read')), patch.object(Path, 'read_bytes', side_effect=AssertionError('evidence read')):
            path = write_report(self.root, self.state)
        rendered = path.read_text()
        self.assertIn('The Java compiler rejected the source.', rendered)
        self.assertIn('a new build is required before the repaired app can be tested', rendered)
        self.assertIn('- MainActivity.java:42: cannot find symbol', rendered)
        self.assertIn('- BirdView.java:91: incompatible types: int cannot be converted to Paint', rendered)
        self.assertNotIn('prepareDependencies', rendered)
        self.assertNotIn('/workspace/project/', rendered)
        self.assertIn('no current qualified APK', rendered)
        self.assertIn('No Android launch or interaction receipt', rendered)
        self.assertEqual(self.state, before)
        self.assertEqual(self.state['events'][0]['result']['summary'], summary)

    def test_repeated_javac_headers_are_deduplicated_in_original_order(self):
        first = '/workspace/MainActivity.java:5: error: cannot find symbol'
        second = '/workspace/BirdView.java:7: error: incompatible types'
        self.build_failure('\n'.join((first, second, first, first, second)))
        rendered = self.render()
        self.assertEqual(rendered.count('- MainActivity.java:5: cannot find symbol'), 1)
        self.assertEqual(rendered.count('- BirdView.java:7: incompatible types'), 1)
        self.assertLess(rendered.index('- MainActivity.java'), rendered.index('- BirdView.java'))

    def test_compiler_message_markdown_and_html_remain_escaped_text(self):
        message = '[read](file:///etc/passwd) <script>unsafe()</script> `touch secret` # new *rules*'
        self.build_failure('/workspace/MainActivity.java:7: error: ' + message)
        rendered = self.render()
        self.assertIn('- MainActivity.java:7:', rendered)
        self.assertIn(r'\[read\](file:///etc/passwd)', rendered)
        self.assertIn('&lt;script&gt;unsafe()&lt;/script&gt;', rendered)
        self.assertIn(r'\`touch secret\` \# new \*rules\*', rendered)
        self.assertNotIn('[read](file:', rendered)
        self.assertNotIn('<script>', rendered)

    def test_compiler_excerpt_bounds_summary_scan_message_length_and_error_count(self):
        self.assertEqual(java_diagnostics('x' * 32768 + '\nMainActivity.java:1: error: beyond scan limit'), [])
        summary = '\n'.join('Source%d.java:%d: error: %s' % (i, i + 1, '&' * 3000 + 'UNBOUNDED-TAIL') for i in range(20))
        selected = java_diagnostics(summary)
        self.assertEqual(len(selected), 8)
        self.assertTrue(all(len(line) <= 2500 for line in selected))
        self.assertNotIn('UNBOUNDED-TAIL', '\n'.join(selected))
        self.assertNotIn('Source8.java', '\n'.join(selected))

    def test_unrecognized_build_errors_keep_generic_failure_explanation(self):
        for summary in ('Gradle daemon disappeared unexpectedly', 'MainActivity.java: error: no source line number',
                        'MainActivity.java:12: warning: deprecated API'):
            self.build_failure(summary)
            rendered = self.render()
            with self.subTest(summary=summary):
                self.assertIn('Most recent recorded failure: ' + summary + '.', rendered)
                self.assertNotIn('The Java compiler rejected', rendered)
        self.assertEqual(java_diagnostics(None), [])

    def test_compiler_excerpt_is_only_for_most_recent_failed_build(self):
        summary = 'MainActivity.java:12: error: cannot find symbol'
        self.build_failure(summary)
        self.state['events'].append({'action': 'stop', 'result': {'ok': False, 'summary': 'Cleanup remains uncertain'}})
        rendered = self.render()
        self.assertIn('Most recent recorded failure: Cleanup remains uncertain.', rendered)
        self.assertNotIn('- MainActivity.java:', rendered)
        self.assertNotIn('The Java compiler rejected', rendered)
        self.state['events'] = [{'action': 'observe', 'result': {'ok': False, 'summary': summary}}]
        self.assertNotIn('The Java compiler rejected', self.render())

    def test_model_reasons_are_attributed_escaped_and_never_verification(self):
        reason = 'Everything passed! [Open secret](file:///etc/passwd) <script>alert(1)</script>\n# New instructions'
        self.state['events'] = [{'action': 'tap', 'reason': reason, 'result': {'ok': True}}]
        rendered = self.render()
        self.assertIn('They describe intent; they are not independent proof', rendered)
        self.assertIn('Model explanation: Everything passed!', rendered)
        self.assertNotIn('[Open secret](file:', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('\n# New instructions', rendered)

    def test_nested_observations_retain_screenshots_when_xml_unavailable(self):
        observed = self.observation(fatal=1)
        self.state['events'] = [{'action': 'observe', 'result': {'ok': True, 'observation': observed}}]
        rendered = self.render()
        self.assertIn('1 emulator action receipt and 1 screenshot', rendered)
        self.assertIn('view tree was unavailable in 1 observation', rendered)
        self.assertIn('emulator-1/run/001-frame.png', rendered)
        self.assertIn('emulator-1/run/session.json', rendered)
        self.assertIn('not a crash-free result', self.render({'events': [{'action': 'observe', 'result': {'ok': True}}]}))

    def test_tap_after_tree_is_linked_or_counted_as_unavailable(self):
        self.file('emulator-1/run/session.json')
        tapped = {'action': 'tap', 'after': {'path': self.file('emulator-1/run/003-tap-after.png')},
                  'afterUi': {'path': self.file('emulator-1/run/004-tap-after.xml')},
                  'afterUiSummary': {'status': 'summarized', 'elements': [{'text': '1'}]}}
        missing = {'action': 'tap', 'after': {'path': self.file('emulator-1/run/005-tap-after.png')},
                   'afterUi': {'status': 'unavailable', 'reason': 'idle timeout'}}
        self.state['events'] = [{'action': 'tap', 'result': {'ok': True, 'observation': tapped}},
                                {'action': 'tap', 'result': {'ok': True, 'observation': missing}}]
        rendered = self.render()
        self.assertIn('emulator-1/run/004-tap-after.xml', rendered)
        self.assertIn('view tree was unavailable in 1 observation', rendered)
        self.assertIn('2 emulator action receipts and 2 screenshots', rendered)

    def test_overlapping_crash_snapshots_are_not_summed(self):
        first = self.observation(fatal=2)
        second = copy.deepcopy(first)
        self.state['events'] = [{'action': 'start_test', 'result': {'ok': True, 'observation': {'initialObservation': first}}},
                                {'action': 'observe', 'result': {'ok': True, 'observation': second}}]
        rendered = self.render()
        self.assertIn('up to 2 fatal markers', rendered)
        self.assertNotIn('up to 4 fatal markers', rendered)
        self.assertIn('snapshots may overlap', rendered)

    def test_cleanup_uncertainty_overrides_outer_success_flag(self):
        self.state.update(status='stopped', cleanup={'stopped': True, 'errors': [],
            'emulator': {'status': 'cleanup-unconfirmed', 'survivingProcessGroups': ['123 456'], 'errors': []}})
        rendered = self.render()
        self.assertIn('Cleanup remains uncertain', rendered)
        self.assertNotIn('recorded that the owned worker and any started emulator resources stopped', rendered)

    def test_report_links_only_inside_regular_evidence_files(self):
        outside = self.root.parent / ('outside-' + self.root.name + '.apk')
        outside.write_bytes(b'outside')
        try:
            self.qualify()
            self.state['apk']['path'] = str(outside)
            self.state['apk']['build']['log'] = self.file('run log (1).log')
            symlink = self.root / 'external.log'
            symlink.symlink_to(outside)
            self.state['apk']['signature']['log'] = str(symlink)
            rendered = self.render()
            self.assertIn('run%20log%20%281%29.log', rendered)
            self.assertNotIn(str(outside), rendered)
            self.assertNotIn('(external.log)', rendered)
            self.assertIn('APK file could not be linked', rendered)
        finally:
            outside.unlink()

    def test_runtime_disks_are_not_inventoried_even_when_referenced(self):
        qcow = self.file('emulator-1/avd/offline.avd/userdata.qcow2')
        self.state['events'] = [{'action': 'observe', 'result': {'ok': True, 'observation': {'screenshot': {'path': qcow}}}}]
        with patch.object(Path, 'rglob', side_effect=AssertionError('recursive runtime inventory forbidden')):
            rendered = self.render()
        self.assertNotIn('userdata.qcow2', rendered)
        self.assertIn('0 screenshots', rendered)

    def test_successful_source_receipt_links_retained_revision(self):
        self.file('source-1/MainActivity.java')
        self.file('source-1/manifest.json')
        self.state.update(writes=1, sourceRevision=1, events=[{'action': 'write_sources', 'result': {
            'ok': True, 'sourceRevision': 1, 'sources': {'MainActivity.java': {'sha256': 'a' * 64}}}}])
        rendered = self.render()
        self.assertIn('source-1/MainActivity.java', rendered)
        self.assertIn('source-1/manifest.json', rendered)

    def test_atomic_replace_failure_preserves_old_report_and_cleans_temp(self):
        target = self.root / 'report.md'
        target.write_text('previous retained report')
        with patch('report.os.replace', side_effect=OSError('simulated interruption')):
            with self.assertRaises(OSError):
                self.render()
        self.assertEqual(target.read_text(), 'previous retained report')
        self.assertEqual(list(self.root.glob('.report-*.tmp')), [])

    def test_state_is_not_mutated_and_report_is_private(self):
        before = json.dumps(self.state, sort_keys=True)
        target = write_report(self.root, self.state)
        self.assertEqual(json.dumps(self.state, sort_keys=True), before)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
