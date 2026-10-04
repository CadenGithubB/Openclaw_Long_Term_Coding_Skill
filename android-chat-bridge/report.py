"""Render controller facts as a human report; never infer gameplay completion."""
import html
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import quote


ACTION_NAMES = {'prepare': 'Prepare the Android environment', 'write_sources': 'Write the app source',
                'build': 'Build the APK', 'start_test': 'Install and launch the app',
                'observe': 'Observe the Android screen', 'tap': 'Interact with the app', 'stop': 'Stop the job'}
STATUSES = {'preparing': 'The Android environment is being prepared.',
            'ready': 'The Android environment is ready for source code.',
            'source-ready': 'Source work is retained, and a current successful build is still needed.',
            'building': 'An Android build is in progress.', 'built': 'An APK build record is retained.',
            'starting-test': 'The controller is starting the Android guest.',
            'testing': 'The Android guest is available for observation and interaction.',
            'stopped': 'The run has stopped.', 'expired': 'The run reached its time or storage limit.',
            'failed': 'The run stopped after an error.',
            'cleanup-uncertain': 'The run stopped with cleanup still uncertain.'}
ALLOWED_SUFFIXES = {'.apk', '.png', '.xml', '.json', '.log', '.java', '.md'}
JAVA_ERROR = re.compile(r'^(?:[A-Za-z]:)?[\\/]?(?:[A-Za-z0-9_. -]+[\\/])*([A-Z][A-Za-z0-9_]{0,63}\.java):([1-9][0-9]{0,8}):[ \t]*error:[ \t]*(.+)$')


def plain(value, maximum=1200):
    """Treat source/model text as prose, never as Markdown instructions or links."""
    text = value if isinstance(value, str) else ''
    text = ' '.join(''.join(c for c in text if c >= ' ' or c in '\n\t').split())[:maximum]
    text = html.escape(text, quote=False)
    return re.sub(r'([\\`*_\[\]{}#|])', r'\\\1', text)


def java_diagnostics(summary):
    """Select bounded javac headers from retained text, without opening logs."""
    if not isinstance(summary, str):
        return []
    selected, seen = [], set()
    # The controller retains at most two 12,000-byte command-output tails.
    for line in summary[:32768].splitlines():
        match = JAVA_ERROR.fullmatch(line.strip()[:2048])
        if match is None:
            continue
        filename, number, message = match.groups()
        diagnostic = plain('%s:%s: %s' % (filename, number, message), 500)
        if diagnostic not in seen:
            selected.append(diagnostic)
            seen.add(diagnostic)
            if len(selected) == 8:
                break
    return selected


def count(value):
    return value if type(value) is int and value >= 0 else 0


def plural(number, singular, multiple=None):
    return '%d %s' % (number, singular if number == 1 else (multiple or singular + 's'))


def mapping(value):
    return value if isinstance(value, dict) else {}


def observation(result):
    result = mapping(result)
    return mapping(result.get('observation')) or result


def write_report(job_dir, state):
    """Atomically replace job_dir/report.md and return its Path.

    Reads no evidence contents. Links only explicit receipt references to regular
    files inside this private job. Runtime disk images are never inventoried.
    """
    root = Path(job_dir)
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise ValueError('report directory must be an existing canonical directory')
    if not isinstance(state, dict):
        raise ValueError('controller state must be an object')
    raw_events = state.get('events', [])
    events = [item for item in raw_events if isinstance(item, dict)][:200] if isinstance(raw_events, list) else []
    links, seen = [], set()

    def artifact(candidate, label):
        if not isinstance(candidate, str) or not candidate or len(candidate) > 8192:
            return None
        path = Path(candidate)
        if not path.is_absolute():
            path = root / path
        try:
            relative = path.relative_to(root)
            if '..' in relative.parts or path.suffix.lower() not in ALLOWED_SUFFIXES:
                return None
            for part in (path, *path.parents):
                if part == root:
                    break
                if part.is_symlink():
                    return None
            if not stat.S_ISREG(path.lstat().st_mode):
                return None
        except (ValueError, OSError):
            return None
        relative_text = relative.as_posix()
        if relative_text not in seen and len(links) < 80:
            seen.add(relative_text)
            links.append('- [%s](%s)' % (plain(label, 160), quote(relative_text, safe='/.-_')))
        return relative_text

    apk = mapping(state.get('apk'))
    current_revision = count(state.get('sourceRevision'))
    apk_current = bool(apk) and apk.get('sourceRevision') == current_revision
    source_events = [e for e in events if e.get('action') == 'write_sources' and mapping(e.get('result')).get('ok') is True]
    build_events = [e for e in events if e.get('action') == 'build']
    emulator_events = [e for e in events if e.get('action') in ('start_test', 'observe', 'tap')]
    failures = [e for e in events if mapping(e.get('result')).get('ok') is False]
    lines = ['# Android run report', '', '## What happened', '',
             STATUSES.get(state.get('status'), 'The controller has retained this run, but its status is not recognized.') +
             ' Build, Android observations, and cleanup are described separately below. This report does not declare gameplay complete.', '']
    if state.get('jobId'):
        lines += ['Run: ' + plain(state['jobId'], 128), '']
    if failures:
        last = failures[-1]
        summary = mapping(last.get('result')).get('summary')
        diagnostics = java_diagnostics(summary) if last.get('action') == 'build' else []
        if diagnostics:
            lines += ['Most recent recorded failure: The Java compiler rejected the source. After repairs, a new build is required before the repaired app can be tested.', '',
                      'Selected source locations and compiler messages are below. The full recorded failure summary remains in the controller state.', '']
            lines += ['- ' + diagnostic for diagnostic in diagnostics]
            lines.append('')
        else:
            lines += ['Most recent recorded failure: ' + plain(summary or 'No explanation was retained.') + '.', '']

    lines += ['## Environment', '']
    baseline = mapping(state.get('baseline'))
    if baseline:
        explanation = 'Preparation retained the build image, project capacity, and local model identity reported by the controller.'
        explanation += (' The saved preparation record says cloud inference was disabled.'
                        if baseline.get('cloudDisabled') is True else ' The saved preparation record does not confirm that cloud inference was disabled.')
        lines += [explanation + ' These are preparation observations; they do not prove app behavior.', '']
        if baseline.get('image'):
            lines += ['Build image: `' + plain(baseline['image'], 160) + '`', '']
        if baseline.get('model'):
            lines += ['Local model recorded: ' + plain(baseline['model'], 160), '']
        if type(baseline.get('freeBytes')) is int:
            lines += ['Free project space at preparation: %.1f MiB.' % (baseline['freeBytes'] / 1048576), '']
    else:
        lines += ['No completed preparation record is retained yet. The report therefore cannot confirm that this run reached the prepared Android environment.', '']

    lines += ['## Source work', '']
    attempts = count(state.get('writes'))
    source_sentence = 'The controller recorded %s and %s.' % (plural(attempts, 'source-write attempt'), plural(len(source_events), 'successful source-write receipt'))
    lines += [source_sentence + ' A saved source file is an implementation artifact; it does not show that the app built or ran.', '']
    if current_revision:
        lines += ['Current source revision: %d.' % current_revision, '']
    for event in source_events:
        result = mapping(event.get('result'))
        revision = count(result.get('sourceRevision'))
        if revision:
            artifact('source-%d/manifest.json' % revision, 'Source revision %d — file fingerprints' % revision)
            for name in mapping(result.get('sources')):
                if isinstance(name, str) and re.fullmatch(r'[A-Z][A-Za-z0-9_]{0,63}\.java', name):
                    artifact('source-%d/%s' % (revision, name), 'Source revision %d — %s' % (revision, name))

    lines += ['## APK build', '']
    if not apk:
        lines += ['The controller recorded %s, but no current qualified APK is retained. Earlier successful-looking output does not substitute for a current artifact and its checks.' % plural(count(state.get('builds')), 'build attempt'), '']
    elif not apk_current:
        lines += ['The retained APK record belongs to a different source revision. It does not qualify the current source, and another build is needed before testing that source.', '']
    else:
        checks = [mapping(apk.get(key)).get('exitCode') == 0 for key in ('build', 'signature', 'packageCheck')]
        if all(checks):
            lines += ['The current APK has saved successful controller receipts for the offline build, signature check, and package check. These checks establish the recorded build result; Android behavior still requires separate observations.', '']
        else:
            lines += ['A current APK record is retained, but the supplied state does not include all three successful build-check receipts. The report does not fill in missing verification.', '']
    if apk:
        retained = artifact(apk.get('path'), 'Built Android APK')
        if not retained:
            lines += ['The APK file could not be linked as a regular file inside this job; its availability is unconfirmed.', '']
        if isinstance(apk.get('sha256'), str) and re.fullmatch('[a-f0-9]{64}', apk['sha256']):
            lines += ['Recorded APK fingerprint: `' + apk['sha256'] + '`', '']
        for key, label in [('build', 'Offline build log'), ('signature', 'APK signature check'), ('packageCheck', 'APK package check')]:
            artifact(mapping(apk.get(key)).get('log'), label)
    # Retain older build evidence links, without treating them as current qualification.
    for event in build_events:
        historical = mapping(mapping(event.get('result')).get('apk'))
        artifact(historical.get('path'), 'APK retained from a build attempt')
        for key in ('build', 'signature', 'packageCheck'):
            artifact(mapping(historical.get(key)).get('log'), 'Build attempt — ' + key)

    lines += ['## Android observations', '']
    screenshots = set()
    crash_records = []
    xml_unavailable = 0

    def collect(value, label, depth=0):
        nonlocal xml_unavailable
        if depth > 7 or not isinstance(value, dict):
            return
        path = artifact(value.get('path'), label)
        if path and path.endswith('.png'):
            screenshots.add(path)
            artifact(str(Path(path).parent / 'session.json'), 'Emulator action and cleanup journal')
        if 'fatalMarkers' in value:
            crash_records.append(count(value['fatalMarkers']))
        if label.endswith('ui') and value.get('status') == 'unavailable':
            xml_unavailable += 1
        for key in ('initialObservation', 'screenshot', 'ui', 'crashes', 'beforeLaunchCrashes', 'before', 'after'):
            collect(value.get(key), 'Android evidence — ' + key, depth + 1)
        items = value.get('intermediateFrames')
        if isinstance(items, list):
            for item in items[:8]:
                collect(item, 'Android evidence — intermediate frame', depth + 1)

    for event in emulator_events:
        collect(observation(event.get('result')), 'Android evidence')
    if emulator_events:
        lines += ['The retained events include %s and %s linked to this job. Taps, images, and app text are observations; scoring, collisions, game over, restart, and visual correctness still require an explicit review of the evidence.' % (plural(len(emulator_events), 'emulator action receipt'), plural(len(screenshots), 'screenshot')), '']
        if xml_unavailable:
            lines += ['The Android view tree was unavailable in %s; available screenshots remain separate evidence. Continuous animation can prevent a view-tree dump from finishing, but the receipts must be checked for the actual failure reason.' % plural(xml_unavailable, 'observation'), '']
        if crash_records:
            lines += ['Crash-buffer receipts contain up to %d fatal markers in a captured snapshot. The snapshots may overlap and cover the whole disposable guest, so the counts are not added together or treated as an app-specific crash verdict.' % max(crash_records), '']
        else:
            lines += ['No crash-buffer count is present in the supplied event receipts. Absence of that evidence is not a crash-free result.', '']
    else:
        lines += ['No Android launch or interaction receipt is retained yet. Installing tools or building an APK does not establish that this app launched or passed its gameplay checks.', '']

    lines += ['## Cleanup', '']
    cleanup = mapping(state.get('cleanup'))
    emulator_cleanup = mapping(cleanup.get('emulator'))
    uncertain = bool(cleanup.get('errors') or emulator_cleanup.get('errors') or emulator_cleanup.get('survivingProcessGroups'))
    uncertain = uncertain or emulator_cleanup.get('status') == 'cleanup-unconfirmed' or state.get('status') == 'cleanup-uncertain'
    cache_cleanup = mapping(cleanup.get('cache'))
    if cleanup.get('stopped') is True and cleanup.get('cleanupComplete') is False:
        lines += ['The controller recorded that the owned worker and any started emulator resources stopped. Cleanup is still incomplete because the derived build cache could not be retired; the retained receipt keeps that storage failure separate from the process shutdown.', '']
    elif uncertain:
        lines += ['Cleanup remains uncertain in the retained records. An error or surviving owned process prevents this report from saying that all resources stopped.', '']
    elif cleanup.get('stopped') is True:
        lines += ['The controller recorded that the owned worker and any started emulator resources stopped. This is a cleanup result and does not imply that the app passed its behavior checks.', '']
    elif cleanup:
        lines += ['A cleanup record exists, but it does not confirm that the owned resources stopped. Keep this result unresolved until the controller records a confirmed outcome.', '']
    else:
        lines += ['No final cleanup receipt has been retained yet. The run may still be active, so this report does not claim that its resources have stopped.', '']
    if cache_cleanup:
        if cache_cleanup.get('complete') is True:
            lines += ['The job’s derived build cache was removed or confirmed absent after the worker stopped. Source files, APKs, logs, and this report remain available; retiring the cache does not qualify the app or reset this job’s limits.', '']
        else:
            lines += ['The derived build cache was not confirmed retired. A later job must not treat this as a complete cleanup or assume that the project space has been recovered.', '']
        measurements = [('logicalBytesBefore', 'Cache file bytes before retirement'), ('logicalBytesAfter', 'Cache file bytes after retirement'),
                        ('freeBytesBefore', 'Free project bytes before retirement'), ('freeBytesAfter', 'Free project bytes after retirement')]
        for key, label in measurements:
            value = cache_cleanup.get(key)
            if type(value) is int and value >= 0:
                lines += [label + ': %d.' % value, '']
    for error in list(cleanup.get('errors') or [])[:8] + list(emulator_cleanup.get('errors') or [])[:8]:
        if isinstance(error, str):
            lines += ['- ' + plain(error)]
    if lines[-1] != '':
        lines.append('')
    processes = emulator_cleanup.get('ownedProcesses', [])
    if isinstance(processes, list):
        for proc in processes[:8]:
            artifact(mapping(proc).get('log'), 'Owned emulator process log')

    lines += ['## Why the agent took these steps', '',
              'These are the model’s recorded explanations for its choices, followed by the controller’s reported outcome. They describe intent; they are not independent proof that an action worked.', '']
    if not events:
        lines += ['No action explanations have been retained yet.', '']
    else:
        for index, event in enumerate(events, 1):
            name = ACTION_NAMES.get(event.get('action'), 'Recorded controller action')
            reason = event.get('reason')
            reason = plain(reason) if isinstance(reason, str) and reason.strip() and reason != 'No reason supplied.' else 'No model explanation was recorded.'
            outcome = mapping(event.get('result'))
            status = 'reported success' if outcome.get('ok') is True else 'reported failure' if outcome.get('ok') is False else 'outcome not confirmed'
            lines += ['%d. **%s** — %s Model explanation: %s' % (index, name, status + '.', reason)]
        lines.append('')

    artifact('state.json', 'Full retained controller state')
    lines += ['## Retained files', '',
              'These links point to explicitly referenced source, APK, logs, or captured observations inside this job. Emulator runtime disks are working storage and are not listed as evidence artifacts.', '']
    lines += links or ['No eligible retained file links are available yet.']
    lines.append('')
    rendered = '\n'.join(lines)
    descriptor, temporary = tempfile.mkstemp(prefix='.report-', suffix='.tmp', dir=str(root))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        target = root / 'report.md'
        os.replace(temporary, target)
        return target
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
