#!/usr/bin/env python3
"""Check acceptance evidence against an independently retained original contract.

Data only: no commands, network requests, or artifact files are opened. Digests
bind submitted records, but cannot authenticate invented JSON or prove that a
command tested its claimed property. A trusted parent must retain the original
contract, inspect actual receipts, and assign evidence levels honestly.

CLI: JSON {contract, report} on stdin. Exit 0 complete, 1 incomplete, 2 invalid.
Evidence levels are distinct requirements, not an interchangeable ranking.
"""
import hashlib
import json
import re
import sys

LEVELS = ('source', 'syntax', 'build', 'launch', 'behavior', 'visual', 'performance')
MAX_INPUT = 131072
MAX_INTEGER = 9007199254740991


class Invalid(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Invalid(reason)


def exact(value, keys, reason):
    require(type(value) is dict and set(value) == set(keys), reason)


def identifier(value):
    return type(value) is str and re.fullmatch(r'[a-z][a-z0-9-]{0,63}', value) is not None


def digest(value):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{64}', value) is not None


def integer(value, minimum=0):
    return type(value) is int and minimum <= value <= MAX_INTEGER


def string(value, maximum):
    return type(value) is str and 0 < len(value) <= maximum and '\0' not in value


def digest_request(text):
    require(string(text, 16384), 'invalid-original-request')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def validate_contract(contract):
    exact(contract, ('version', 'request', 'criteria'), 'invalid-contract-fields')
    require(type(contract['version']) is int and contract['version'] == 1, 'invalid-contract-version')
    digest_request(contract['request'])
    criteria = contract['criteria']
    require(type(criteria) is list and 1 <= len(criteria) <= 32, 'invalid-criteria-list')
    indexed = {}
    for criterion in criteria:
        exact(criterion, ('id', 'check', 'requiredLevels', 'artifactId', 'environmentId'), 'invalid-criterion-fields')
        require(identifier(criterion['id']) and criterion['id'] not in indexed, 'invalid-or-duplicate-criterion-id')
        require(string(criterion['check'], 2048) and identifier(criterion['artifactId'])
                and identifier(criterion['environmentId']), 'invalid-criterion-binding')
        levels = criterion['requiredLevels']
        require(type(levels) is list and 1 <= len(levels) <= len(LEVELS)
                and all(type(level) is str and level in LEVELS for level in levels)
                and len(set(levels)) == len(levels), 'invalid-required-levels')
        indexed[criterion['id']] = criterion
    return indexed


def digest_contract(contract):
    validate_contract(contract)
    raw = json.dumps(contract, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def validate_report(report, contract, indexed):
    exact(report, ('requestSha256', 'contractSha256', 'criteria', 'asOfMs', 'artifacts', 'environments', 'evidence'),
          'invalid-report-fields')
    require(report['requestSha256'] == digest_request(contract['request']), 'original-request-binding-mismatch')
    require(report['contractSha256'] == digest_contract(contract), 'original-contract-binding-mismatch')
    submitted = report['criteria']
    # Reordering whole criteria is harmless; changing a definition or dropping a
    # required level is not. Amendments require a separately authorized contract.
    candidate = {'version': 1, 'request': contract['request'], 'criteria': submitted}
    require(validate_contract(candidate) == indexed, 'criteria-changed-or-removed')
    require(integer(report['asOfMs'], 1), 'invalid-report-time')
    for field, keys in [('artifacts', ('sha256', 'changedAtMs')), ('environments', ('sha256',))]:
        records = report[field]
        require(type(records) is dict and len(records) <= 32, 'invalid-' + field)
        for name, record in records.items():
            require(identifier(name), 'invalid-' + field + '-id')
            exact(record, keys, 'invalid-' + field + '-record')
            require(digest(record['sha256']), 'invalid-' + field + '-digest')
            if field == 'artifacts':
                require(integer(record['changedAtMs'], 1) and record['changedAtMs'] <= report['asOfMs'],
                        'invalid-artifact-time')
    evidence = report['evidence']
    require(type(evidence) is list and len(evidence) <= 256, 'invalid-evidence-list')
    seen = set()
    for record in evidence:
        exact(record, ('id', 'criterionId', 'level', 'artifactSha256', 'environmentSha256', 'checkedAtMs',
                       'performed', 'kind', 'command', 'exitCode', 'checks', 'recordSha256'), 'invalid-evidence-fields')
        require(identifier(record['id']) and record['id'] not in seen, 'invalid-or-duplicate-evidence-id')
        seen.add(record['id'])
        require(type(record['criterionId']) is str and record['criterionId'] in indexed
                and type(record['level']) is str and record['level'] in LEVELS, 'invalid-evidence-target')
        require(all(digest(record[k]) for k in ('artifactSha256', 'environmentSha256', 'recordSha256')),
                'invalid-evidence-digest')
        require(integer(record['checkedAtMs'], 1) and record['checkedAtMs'] <= report['asOfMs'],
                'invalid-or-future-evidence-time')
        require(type(record['performed']) is bool and record['kind'] in ('command', 'inspection'), 'invalid-evidence-kind')
        if record['kind'] == 'command':
            command = record['command']
            require(type(command) is list and 1 <= len(command) <= 64
                    and all(string(arg, 2048) for arg in command), 'invalid-evidence-command')
            require(record['exitCode'] is None or (type(record['exitCode']) is int and -255 <= record['exitCode'] <= 255),
                    'invalid-command-exit-code')
        else:
            require(record['command'] is None and record['exitCode'] is None, 'invalid-inspection-command')
        checks = record['checks']
        require(type(checks) is list and len(checks) <= 32, 'invalid-evidence-checks')
        check_ids = set()
        for check in checks:
            exact(check, ('id', 'passed'), 'invalid-evidence-check')
            require(identifier(check['id']) and check['id'] not in check_ids and type(check['passed']) is bool,
                    'invalid-or-duplicate-check-id')
            check_ids.add(check['id'])


def unmet_reason(record, criterion, report):
    if not record['performed']:
        return 'check-not-performed'
    artifact = report['artifacts'].get(criterion['artifactId'])
    environment = report['environments'].get(criterion['environmentId'])
    if artifact is None:
        return 'current-artifact-missing'
    if environment is None:
        return 'current-environment-missing'
    if record['artifactSha256'] != artifact['sha256']:
        return 'artifact-digest-mismatch'
    if record['environmentSha256'] != environment['sha256']:
        return 'environment-digest-mismatch'
    if record['checkedAtMs'] < artifact['changedAtMs']:
        return 'evidence-predates-artifact'
    if record['level'] in ('syntax', 'build', 'launch', 'performance') and record['kind'] != 'command':
        return 'executed-command-required'
    if record['level'] == 'visual' and record['kind'] != 'inspection':
        return 'visual-inspection-required'
    if record['kind'] == 'command' and record['exitCode'] != 0:
        return 'command-did-not-succeed'
    # Exit zero alone is insufficient: the recorded assertion must also pass.
    if not record['checks']:
        return 'explicit-checks-missing'
    if not all(check['passed'] for check in record['checks']):
        return 'recorded-check-failed'
    return None


def evaluate(value):
    """Return complete/incomplete/invalid without executing or reading evidence."""
    try:
        exact(value, ('contract', 'report'), 'invalid-input-fields')
        contract, report = value['contract'], value['report']
        indexed = validate_contract(contract)
        validate_report(report, contract, indexed)
        outcomes = []
        for criterion in contract['criteria']:
            issues = []
            for level in criterion['requiredLevels']:
                records = [record for record in report['evidence']
                           if record['criterionId'] == criterion['id'] and record['level'] == level]
                if not records:
                    issues.append({'level': level, 'reason': 'required-evidence-missing'})
                    continue
                # A newer failed/unperformed check cannot be hidden behind an
                # older pass. Tied observations must all succeed. Earlier
                # failures may be superseded by an explicit later passing check.
                latest = max(record['checkedAtMs'] for record in records)
                for record in records:
                    if record['checkedAtMs'] != latest:
                        continue
                    reason = unmet_reason(record, criterion, report)
                    if reason:
                        issues.append({'level': level, 'reason': reason, 'evidenceId': record['id']})
            outcomes.append({'id': criterion['id'], 'status': 'unmet' if issues else 'met', 'issues': issues})
        return {'outcome': 'complete' if all(item['status'] == 'met' for item in outcomes) else 'incomplete',
                'requestSha256': digest_request(contract['request']), 'contractSha256': digest_contract(contract),
                'criteria': outcomes}
    except (Invalid, ValueError, TypeError, KeyError, OverflowError, RecursionError, UnicodeError) as error:
        return {'outcome': 'invalid', 'reason': str(error) if isinstance(error, Invalid) else 'malformed-input'}


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate-json-key')
        result[key] = value
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-report-dir', help='Existing private parent-owned report root; no worker commands run')
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        require(len(raw) <= MAX_INPUT, 'input-too-large')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                           parse_constant=lambda _: require(False, 'nonfinite-json'))
        if args.android_report_dir:
            expected = ('contract', 'report', 'androidRun', 'decisionHistory') if 'decisionHistory' in value else ('contract', 'report', 'androidRun')
            exact(value, expected, 'invalid-android-report-envelope')
            base = {key: value[key] for key in ('contract', 'report')}
            result = evaluate(base)
            # A malformed contract/acceptance record must not become a durable
            # report with misleading fabricated criterion or time fields.
            require(result['outcome'] != 'invalid', 'invalid-acceptance-record')
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import android_report
            summary = android_report.materialize(value['contract'], value['report'], result, value['androidRun'])
            if 'decisionHistory' in value:
                import decision_history
                try:
                    history = decision_history.projection(args.android_report_dir, value['decisionHistory'],
                                                         summary['runId'], summary['acceptance']['contractSha256'])
                except (ValueError, OSError, KeyError, TypeError, RecursionError, UnicodeError) as error:
                    history = {'availability': 'unavailable', 'reason': str(error)[:256],
                               'summary': [], 'readablePath': None}
                summary['decisionHistory'] = history
                summary['historyReportingComplete'] = history['availability'] in ('recorded', 'not_requested')
                result['historyAvailability'] = history['availability']
            result['androidReport'] = android_report.publish(args.android_report_dir, summary)
            if not summary['complete']:
                result['outcome'] = 'incomplete'
        else:
            result = evaluate(value)
    except (Invalid, ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError, UnicodeError) as error:
        result = {'outcome': 'invalid', 'reason': str(error) if isinstance(error, Invalid) else 'malformed-json'}
        if args.android_report_dir:
            result['reportUnavailable'] = {'type': type(error).__name__, 'detail': str(error)[:256]}
    sys.stdout.write(json.dumps(result, separators=(',', ':'), ensure_ascii=True) + '\n')
    return {'complete': 0, 'incomplete': 1, 'invalid': 2}[result['outcome']]


if __name__ == '__main__':
    raise SystemExit(main())
