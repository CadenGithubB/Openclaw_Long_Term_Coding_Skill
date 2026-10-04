#!/usr/bin/env python3
"""Bounded parent-owned Android report materialization; no execution or collection.

Consumes the existing acceptance result plus controller-supplied artifact receipts.
It does not authenticate those receipts, execute commands, read artifact paths, or
control a worker. Call from the existing parent's review/finalization/recovery path.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

STAGES = ('preflight', 'build', 'artifact_verification', 'install', 'launch',
          'automated_tests', 'functional', 'visual', 'performance', 'crash_logs', 'cleanup')
STATUSES = ('passed', 'failed', 'not_run', 'blocked', 'inconclusive', 'not_applicable')
LIFECYCLES = ('running', 'completed', 'failed', 'cancelled', 'timed_out', 'interrupted')
KINDS = ('apk', 'source_manifest', 'build_log', 'test_result', 'screenshot',
         'recording', 'crash_log', 'environment', 'cleanup_receipt')
APK_STAGES = set(STAGES) - {'preflight', 'cleanup'}
MAX_BYTES = 1048576
MAX_SNAPSHOTS = 64


def require(ok, message):
    if not ok:
        raise ValueError(message)


def encode(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True,
                       allow_nan=False, separators=(',', ':')) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def text(value, maximum=2048):
    return (type(value) is str and 0 < len(value) <= maximum
            and not any(ord(c) < 32 and c not in '\n\t' for c in value))


def exact(value, fields, name):
    require(type(value) is dict and set(value) == set(fields), 'invalid ' + name)


def source_identity(source):
    exact(source, ('revision', 'dirty', 'snapshotSha256', 'manifestRef'), 'source')
    revision, dirty, snapshot = source['revision'], source['dirty'], source['snapshotSha256']
    require(revision is None or (type(revision) is str and re.fullmatch('[0-9a-f]{7,64}', revision)), 'invalid source revision')
    require(dirty is None or type(dirty) is bool, 'invalid dirty state')
    require(snapshot is None or digest(snapshot), 'invalid source snapshot')
    # A dirty/unknown checkout needs a snapshot; a clean commit may stand alone.
    known = snapshot is not None or (revision is not None and dirty is False)
    return sha(encode(source)) if known else None


def materialize(contract, acceptance_report, acceptance_result, run):
    exact(run, ('version', 'runId', 'lifecycle', 'reason', 'source', 'apk', 'artifacts',
                'environments', 'stages', 'crashes'), 'Android run fields')
    require(run['version'] == 1 and type(run['version']) is int, 'invalid version')
    require(type(run['runId']) is str and re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}', run['runId']), 'invalid run ID')
    require(run['lifecycle'] in LIFECYCLES and text(run['reason']), 'invalid lifecycle')
    require(acceptance_result.get('outcome') in ('complete', 'incomplete', 'invalid'), 'missing acceptance result')
    artifacts = run['artifacts']
    require(type(artifacts) is dict and len(artifacts) <= 64, 'invalid artifacts')
    for key, item in artifacts.items():
        require(type(key) is str and re.fullmatch('[a-z][a-z0-9-]{0,63}', key), 'invalid artifact ID')
        exact(item, ('path', 'sha256', 'bytes', 'kind'), 'artifact receipt')
        path = item['path']
        require(text(path, 512) and not Path(path).is_absolute() and
                '..' not in Path(path).parts and '\\' not in path and ':' not in path
                and all(ord(c) >= 32 for c in path) and str(Path(path)) == path, 'invalid artifact path')
        require(digest(item['sha256']) and type(item['bytes']) is int and
                0 <= item['bytes'] <= 1024**3 and item['kind'] in KINDS, 'invalid artifact metadata')

    def refs(values, kind=None):
        require(type(values) is list and len(values) <= 64 and
                all(type(x) is str and x in artifacts for x in values) and
                len(set(values)) == len(values), 'invalid evidence references')
        if kind:
            require(all(artifacts[x]['kind'] == kind for x in values), 'wrong evidence kind')

    source = run['source'];identity = source_identity(source)
    if source['manifestRef'] is not None:
        refs([source['manifestRef']], 'source_manifest')
        require(artifacts[source['manifestRef']]['sha256'] == source['snapshotSha256'], 'source manifest mismatch')
    apk = run['apk'];apk_sha = None;bound = False
    if apk is not None:
        exact(apk, ('artifactRef', 'sourceIdentitySha256'), 'APK binding')
        refs([apk['artifactRef']], 'apk')
        require(digest(apk['sourceIdentitySha256']), 'invalid APK source identity')
        apk_sha = artifacts[apk['artifactRef']]['sha256']
        bound = (identity is not None and apk['sourceIdentitySha256'] == identity
                 and acceptance_report['artifacts'].get(apk['artifactRef'], {}).get('sha256') == apk_sha)
    environments = run['environments']
    exact(environments, ('buildRef', 'androidRef', 'toolchainRefs'), 'environment references')
    refs([x for x in [environments['buildRef'], environments['androidRef']] if x is not None], 'environment')
    refs(environments['toolchainRefs'], 'environment')
    environment_matches = {name: ref is not None and
                           acceptance_report['environments'].get(ref, {}).get('sha256') == artifacts[ref]['sha256']
                           for name, ref in environments.items() if name != 'toolchainRefs'}
    stages = run['stages'];require(type(stages) is dict and not set(stages) - set(STAGES), 'invalid stage names')
    normalized = {}
    for name in STAGES:
        item = stages.get(name, {'status': 'not_run', 'reason': 'No recorded result.', 'evidenceRefs': [], 'apkSha256': None})
        exact(item, ('status', 'reason', 'evidenceRefs', 'apkSha256'), 'stage result')
        require(item['status'] in STATUSES and text(item['reason']), 'invalid stage status')
        require(item['apkSha256'] is None or digest(item['apkSha256']), 'invalid stage APK')
        refs(item['evidenceRefs']);item = dict(item);issues = []
        if item['status'] == 'passed':
            if not item['evidenceRefs']:issues.append('passing stage lacks evidence')
            if name in APK_STAGES and (not bound or item['apkSha256'] != apk_sha):
                issues.append('APK/source binding missing or mismatched')
            if name in ('install', 'launch', 'automated_tests', 'functional', 'visual', 'performance') and not environment_matches['androidRef']:
                issues.append('Android environment unavailable')
            if name == 'build' and not environment_matches['buildRef']:
                issues.append('build environment unavailable')
            if issues:item.update(status='inconclusive', submittedStatus='passed', bindingIssues=issues)
        normalized[name] = item
    crashes = run['crashes']
    exact(crashes, ('status', 'package', 'startMs', 'endMs', 'observedCrashes', 'evidenceRefs', 'reason'), 'crash observation')
    require(crashes['status'] in ('collected', 'not_run', 'unavailable') and text(crashes['reason']), 'invalid crash status')
    refs(crashes['evidenceRefs'], 'crash_log')
    count = crashes['observedCrashes']
    if crashes['status'] == 'collected':
        require(type(crashes['package']) is str and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', crashes['package']), 'invalid package scope')
        require(type(crashes['startMs']) is int and type(crashes['endMs']) is int and
                0 < crashes['startMs'] < crashes['endMs'] <= acceptance_report['asOfMs'], 'invalid crash window')
        require(type(count) is int and 0 <= count <= 1000000 and crashes['evidenceRefs'], 'missing crash evidence')
        require(normalized['crash_logs'].get('submittedStatus', normalized['crash_logs']['status']) == 'passed' and
                set(crashes['evidenceRefs']) <= set(normalized['crash_logs']['evidenceRefs']), 'crash collection binding missing')
    else:
        require(count is None and crashes['startMs'] is None and crashes['endMs'] is None and
                crashes['package'] is None and not crashes['evidenceRefs'], 'uncollected crashes cannot claim a result')
        require(normalized['crash_logs']['status'] != 'passed', 'crash logs not collected')
    runtime_stages = [normalized[x]['status'] for x in ('install', 'launch', 'functional')]
    crash_bound = normalized['crash_logs']['status'] == 'passed'
    runtime = ('failed' if 'failed' in runtime_stages or (count is not None and count > 0 and crash_bound) else
               'blocked' if 'blocked' in runtime_stages else
               'inconclusive' if normalized['crash_logs']['status'] == 'inconclusive' else
               'passed' if all(x == 'passed' for x in runtime_stages) else 'inconclusive')
    functional_criteria = [x['id'] for x in contract['criteria'] if 'behavior' in x['requiredLevels']]
    met = {x['id'] for x in acceptance_result.get('criteria', []) if x['status'] == 'met'}
    if runtime == 'passed' and (not functional_criteria or not set(functional_criteria) <= met):runtime = 'inconclusive'
    levels = {level for criterion in contract['criteria'] for level in criterion['requiredLevels']}
    required_stages = {'preflight', 'cleanup'}
    if 'build' in levels:required_stages.update(('build', 'artifact_verification'))
    if levels & {'launch', 'behavior', 'visual', 'performance'}:required_stages.update(('install', 'launch'))
    for level, stage in [('behavior','functional'), ('visual','visual'), ('performance','performance')]:
        if level in levels:required_stages.add(stage)
    complete = (run['lifecycle'] == 'completed' and acceptance_result['outcome'] == 'complete'
                and all(normalized[x]['status'] == 'passed' or
                        (x == 'cleanup' and normalized[x]['status'] == 'not_applicable') for x in required_stages)
                and not any(x['status'] == 'failed' for x in normalized.values())
                and (crashes['status'] != 'collected' or crash_bound)
                and not (count is not None and count > 0 and crash_bound))
    result = {'readableVersion': 2, 'version': 1, 'runId': run['runId'], 'lifecycle': run['lifecycle'], 'reason': run['reason'],
              'request': contract['request'], 'contract': contract, 'acceptance': acceptance_result,
              'asOfMs': acceptance_report['asOfMs'], 'source': source, 'sourceIdentitySha256': identity,
              'apk': apk, 'apkSha256': apk_sha, 'apkBoundToSource': bound, 'artifacts': artifacts,
              'environments': environments, 'stages': normalized, 'crashes': crashes,
              'buildOutcome': normalized['build']['status'], 'runtimeOutcome': runtime,
              'crashObservationBoundToApk': crash_bound,
              'complete': complete, 'requiredStages': [x for x in STAGES if x in required_stages],
              'unfinishedChecks': [x for x in acceptance_result.get('criteria', []) if x['status'] != 'met'],
              'unavailableStages': [k for k,v in normalized.items() if v['status'] in ('not_run','blocked','inconclusive')],
              'collections': {kind: {'status': 'available' if any(x['kind']==kind for x in artifacts.values()) else 'not_collected',
                                      'artifactRefs': [key for key,x in artifacts.items() if x['kind']==kind]}
                              for kind in ('build_log','test_result','screenshot','recording','crash_log','cleanup_receipt')},
              'limits': ['Controller-supplied receipts; not independently authenticated by this report.',
                         'No observed crash applies only to the recorded package/window; it does not prove crash freedom.']}
    require(len(encode(result)) <= MAX_BYTES, 'report exceeds 1 MiB')
    return result


def markdown(report):
    from report_language import safe, details, time_ms, STATUS, STAGES, STATE_EXPLANATION, HISTORY
    if report['complete']:
        summary='The requested work is complete according to the recorded acceptance checks. The results below explain what was checked and the limits of that evidence.'
    else:
        summary='The requested work is not yet confirmed complete. Some work may have succeeded; the sections below distinguish those results from failed, unfinished or unconfirmed checks.'
    state={'running':'Work was still running when this record was saved.','completed':'The work attempt ended.','failed':'The work attempt failed.','cancelled':'The work attempt was cancelled.','timed_out':'The work attempt reached its time limit.','interrupted':'The work attempt was interrupted.'}[report['lifecycle']]
    lines=['# Android run report','','## What happened','',summary,'',state+' '+safe(report['reason']),'',
           '## What was requested','','This is the retained request used to judge the result. A successful step only counts toward the parts of this request it actually checks.','',safe(report['request']),'',
           '## Step-by-step evidence','','Each section explains the purpose of the check, then gives its recorded result. Technical details remain available beneath the explanation.','']
    for name in STAGES:
        item=report['stages'][name];title,purpose=STAGES[name]
        lines+=['### '+title+' — '+STATUS[item['status']],'',purpose,'',STATE_EXPLANATION[item['status']]+' '+safe(item['reason']),'']
        if item.get('bindingIssues'):
            lines+=['The supplied evidence could not be matched to the required app, source or test environment. A claimed pass therefore remains unconfirmed.','']
        lines+=details({'stage':name,**item})
    lines+=['## Files kept for checking the result','','These files let a reader inspect the evidence behind the report. SHA-256 is a file fingerprint: matching fingerprints show that the same bytes were retained, not that a claim about them is true.','']
    if not report['artifacts']:lines+=['No evidence files were collected.','']
    else:lines+=details(report['artifacts'])
    available=[k.replace('_',' ') for k,v in report['collections'].items() if v['status']=='available']
    missing=[k.replace('_',' ') for k,v in report['collections'].items() if v['status']!='available']
    lines+=['Available evidence: '+safe(', '.join(available) or 'none recorded')+'.',
            'Evidence not collected: '+safe(', '.join(missing) or 'none of the listed types')+'.','']
    lines+=['## What remains unfinished','']
    if report['unfinishedChecks']:
        lines+=['The following requested checks are not yet satisfied. Their saved details explain why they cannot be counted as complete.','']
        by_id={x['id']:x['check'] for x in report['contract']['criteria']}
        for item in report['unfinishedChecks']:
            lines+=['- '+safe(by_id.get(item['id'],item['id'])), '']+details(item)
    else:
        lines+=['No unmet acceptance checks are listed. Overall completion still depends on the attempt ending successfully and its required stages being confirmed.','']
    history=report.get('decisionHistory')
    lines+=['## Why choices were made','']
    if history is None:
        lines+=['No decision history was attached to this report. The test results do not reveal why the agent chose an approach.','']
    else:
        lines+=[HISTORY[history['availability']],'','The entries below repeat recorded explanations; they are not reasons inferred from a successful or failed test.','']
        if history.get('readablePath'):lines+=['[Pinned decision chronology]('+history['readablePath']+')','']
        for item in history.get('summary',[]):
            lines+=['**Recorded choice:** '+safe(item['choice']),'',
                    '**Recorded reason:** '+('No reason was recorded.' if item['statedReason']=='not_recorded' else safe(item['statedReason'])),'']
        if history.get('summary'):lines+=['These are short excerpts. Consult the linked chronology for the full original wording and later changes.','']
        lines+=details({k:v for k,v in history.items() if k!='summary'})
    lines+=['## What the crash checks show','']
    crash=report['crashes']
    if crash['status']=='collected' and report['crashObservationBoundToApk']:
        count=crash['observedCrashes']
        lines+=[('No crashes were observed' if count==0 else str(count)+' crash event(s) were observed')+' in the collected logs for this app between '+time_ms(crash['startMs'])+' and '+time_ms(crash['endMs'])+'. This does not prove crash freedom outside that app and test window.','']
    elif crash['status']=='collected':
        lines+=['Crash records were supplied, but they could not be confirmed as applying to this tested app. They do not establish whether this app crashed.','']
    else:lines+=['Crash information was not collected or was unavailable. The absence of a count must not be read as zero crashes.','']
    lines+=[safe(crash['reason']),'']+details(crash)
    lines+=['## How to trace this report','','The identifiers below connect the report to its saved source files and Android installation file. The report formats records supplied by the supervising process; it does not independently repeat or authenticate the tests.','']
    lines+=details({k:report[k] for k in ('runId','source','apkSha256','apkBoundToSource','environments')})
    lines+=['Machine-readable record: [report.json](report.json)','']
    return '\n'.join(lines).encode()


def publish(root, report):
    """Publish the same immutable report pair through the shared private store."""
    import private_snapshot
    with private_snapshot.locked(root) as root:
        return private_snapshot.pair(root, encode(report), markdown(report), report['runId'],
                                     MAX_SNAPSHOTS, MAX_BYTES)
