#!/usr/bin/env python3
"""Measure task prerequisites without installing tools or launching project code."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile


def executable(path):
    if not path:
        return None
    resolved = os.path.realpath(os.path.abspath(path))
    return resolved if os.path.isfile(resolved) and os.access(resolved, os.X_OK) else None


PROFILES = ('python', 'javascript', 'android', 'android-build', 'android-emulator')

def tool(name, candidates=()):
    path = shutil.which(name) or next((str(p) for p in candidates if executable(str(p))), None)
    return {'path': os.path.abspath(path) if path else None,
            'realpath': executable(path), 'status': 'present-unverified' if executable(path) else 'missing'}


def probe_project(project):
    path = os.path.abspath(project)
    result = {'path': path, 'realpath': os.path.realpath(path), 'exists': os.path.exists(path),
              'directory': os.path.isdir(path), 'writable': False, 'probeRemoved': True}
    fd, temporary = None, None
    try:
        if not result['directory']:
            result['error'] = 'project-directory-missing'
            return result
        fd, temporary = tempfile.mkstemp(prefix='.long-task-preflight-', dir=path)
        data = b'long-task-preflight\n'
        if os.write(fd, data) != len(data):
            raise OSError('short-write')
        os.fsync(fd)
        result['writable'] = True
    except OSError as error:
        result['error'] = type(error).__name__
    finally:
        if fd is not None:
            os.close(fd)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError as error:
                result.update(writable=False, probeRemoved=False, error=type(error).__name__)
    return result


def inspect_environment(project, profile):
    if profile not in PROFILES:
        raise ValueError('unknown-profile')
    tools = {}
    current_python = executable(sys.executable)
    result = {'format': 1, 'kind': 'environment-preflight', 'profile': profile,
              'platform': {'system': platform.system(), 'release': platform.release(), 'machine': platform.machine()},
              'python': {'version': platform.python_version(), 'path': sys.executable, 'realpath': current_python},
              'project': probe_project(project), 'tools': tools, 'missing': [], 'unverified': [],
              'authorization': 'not-assessed', 'setupRequired': False,
              'installsPerformed': False, 'networkUsed': False, 'projectCodeExecuted': False}
    if not result['project']['writable']:
        result['missing'].append('writable-project-directory')
    if not current_python:
        result['missing'].append('current-python-executable')
    if profile == 'javascript':
        tools['node'] = tool('node')
        if tools['node']['status'] == 'missing':
            result['missing'].append('node')
        result['unverified'].extend(['javascript-parser', 'browser-or-application-runtime'])
    elif profile == 'python':
        result['unverified'].extend(['project-dependencies', 'application-runtime'])
    else:
        needs_build = profile in ('android', 'android-build')
        needs_emulator = profile in ('android', 'android-emulator')
        result['androidRole'] = 'combined' if profile == 'android' else profile[8:]
        roots = []
        for key in ('ANDROID_SDK_ROOT', 'ANDROID_HOME'):
            value = os.environ.get(key)
            if value and os.path.isabs(value) and value not in roots:
                roots.append(value)
        sdk_tools = {'adb': 'platform-tools/adb', 'emulator': 'emulator/emulator',
                     'sdkmanager': 'cmdline-tools/latest/bin/sdkmanager'}
        for name in ('adb', 'emulator', 'java', 'javac', 'sdkmanager', 'gradle'):
            candidates = [Path(root) / sdk_tools[name] for root in roots] if name in sdk_tools else []
            if name in ('java', 'javac') and os.path.isabs(os.environ.get('JAVA_HOME', '')):
                candidates.append(Path(os.environ['JAVA_HOME']) / 'bin' / name)
            tools[name] = tool(name, candidates)
        sdk = [{'path': path, 'exists': os.path.isdir(path),
                'platforms': os.path.isdir(os.path.join(path, 'platforms')),
                'buildTools': os.path.isdir(os.path.join(path, 'build-tools')),
                'systemImages': os.path.isdir(os.path.join(path, 'system-images'))} for path in roots]
        result['androidSdk'] = {'configuredRoots': sdk, 'status': 'present-unverified' if any(x['exists'] for x in sdk) else 'missing-or-unconfigured'}
        project_path = Path(result['project']['path'])
        wrapper = executable(str(project_path / 'gradlew'))
        build_files = [name for name in ('build.gradle', 'build.gradle.kts', 'settings.gradle', 'settings.gradle.kts')
                       if (project_path / name).is_file()]
        result['buildRoute'] = {'wrapper': wrapper, 'gradle': tools['gradle']['realpath'],
                                'projectFiles': build_files,
                                'status': 'present-unverified' if (wrapper or tools['gradle']['realpath']) and build_files else 'missing'}
        if not needs_build: result['buildRoute']['status'] = 'not-required-for-profile'
        for name in (('java', 'javac') if needs_build else ()) + (('adb', 'emulator') if needs_emulator else ()):
            if tools[name]['status'] == 'missing':
                result['missing'].append(name)
        if not any(x['exists'] for x in sdk):
            result['missing'].append('configured-android-sdk')
        if needs_build:
            if result['buildRoute']['status'] == 'missing':
                result['missing'].append('android-build-route')
            for field, label in (('platforms', 'android-sdk-platforms'), ('buildTools', 'android-build-tools')):
                if not any(x[field] for x in sdk): result['missing'].append(label)
            result['unverified'].extend(['sdk-packages-and-licenses', 'build-tool-compatibility',
                                        'application-build', 'offline-dependency-closure'])
        if needs_emulator:
            if not any(x['systemImages'] for x in sdk): result['missing'].append('android-system-image')
            result['unverified'].extend(['emulator-architecture-and-acceleration', 'avd-boot',
                                        'adb-device-readiness', 'application-install', 'android-runtime-tests'])
    result['setupRequired'] = bool(result['missing'])
    result['status'] = 'prerequisites-missing' if result['missing'] else 'prerequisites-present-unverified'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=PROFILES, required=True)
    parser.add_argument('--project', required=True)
    args = parser.parse_args(argv)
    result = inspect_environment(args.project, args.profile)
    print(json.dumps(result, sort_keys=True))
    return 2 if result['missing'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
