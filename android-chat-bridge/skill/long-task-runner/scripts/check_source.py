#!/usr/bin/env python3
"""Syntax-only verification. Never execute the checked project's source."""
import argparse
import ast
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import sys
import time

MAX_SOURCE = 262144
MAX_OUTPUT = 8192
MAX_SCRIPTS = 32
TOTAL_SECONDS = 10


class InlineScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.scripts, self.external, self.ignored = [], [], 0
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag != 'script':
            return
        values = dict(attrs)
        if len(values) != len(attrs):
            raise ValueError('duplicate-script-attribute')
        script_type = (values.get('type') or '').strip().lower()
        javascript = script_type in ('', 'module', 'text/javascript', 'application/javascript', 'text/ecmascript', 'application/ecmascript')
        if 'src' in values:
            self.external.append(values['src'] or '')
        self.current = {'type': 'module' if script_type == 'module' else 'commonjs', 'chunks': [],
                        'check': javascript and 'src' not in values, 'line': self.getpos()[0]}
        if not javascript:
            self.ignored += 1

    def handle_data(self, data):
        if self.current is not None:
            self.current['chunks'].append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            if self.current['check']:
                self.scripts.append((self.current['type'], ''.join(self.current['chunks']), self.current['line']))
                if len(self.scripts) > MAX_SCRIPTS:
                    raise ValueError('too-many-inline-scripts')
            self.current = None


def node_check(node, source, dialect, deadline):
    """Bound all IO; stdin is parser input, never a script execution argument."""
    command = [node, '--check', '--input-type=' + dialect]
    data = source.encode('utf-8')
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}, bufsize=0)
    output, position, reason = bytearray(), 0, None
    selector = selectors.DefaultSelector()
    try:
        for stream in (process.stdin, process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE)
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(process.stderr, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                reason = 'parser-timeout'
                break
            for key, events in selector.select(min(remaining, 0.1)):
                stream = key.fileobj
                if stream is process.stdin:
                    try:
                        position += os.write(stream.fileno(), data[position:position + 16384])
                    except BrokenPipeError:
                        position = len(data)
                    if position == len(data):
                        selector.unregister(stream)
                        stream.close()
                else:
                    chunk = os.read(stream.fileno(), 4096)
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                    elif len(output) + len(chunk) > MAX_OUTPUT:
                        reason = 'parser-output-limit'
                        break
                    else:
                        output.extend(chunk)
            if reason:
                break
        if reason:
            process.kill()
        try:
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill()
            code, reason = process.wait(timeout=1), 'parser-timeout'
        return {'ok': code == 0 and reason is None, 'code': code,
                'reason': reason or ('syntax-accepted' if code == 0 else 'parser-rejected'),
                'diagnostic': output.decode('utf-8', errors='replace')}
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def read_source(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('source-not-regular-file')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            raw = source.read(MAX_SOURCE + 1)
        if len(raw) > MAX_SOURCE:
            raise ValueError('source-size-limit')
        return raw, raw.decode('utf-8-sig')
    finally:
        os.close(fd)


def check_source(path, language, node=None):
    result = {'format': 1, 'kind': 'source-syntax-check', 'path': os.path.abspath(path),
              'language': language, 'scope': 'syntax-only', 'projectCodeExecuted': False,
              'runtimeVerified': False, 'checks': [], 'status': 'failed', 'selfTestPassed': False}
    try:
        raw, text = read_source(path)
        result.update(sourceBytes=len(raw), sourceSha256=hashlib.sha256(raw).hexdigest())
        if language == 'python':
            result['parser'] = {'kind': 'ast.parse + compile (never exec)', 'pythonVersion': sys.version.split()[0], 'path': sys.executable}
            ast.parse('value = 1\n')
            try:
                ast.parse('def broken(:\n')
            except SyntaxError:
                result['selfTestPassed'] = True
            if not result['selfTestPassed']:
                raise ValueError('parser-self-test-failed')
            tree = ast.parse(text, filename=os.path.basename(path))
            compile(tree, os.path.basename(path), 'exec')
            result['checks'].append({'ok': True, 'reason': 'syntax-accepted'})
        elif language in ('javascript', 'html'):
            candidate = node if node is not None else shutil.which('node')
            if not candidate or not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
                raise ValueError('node-runtime-missing')
            node = os.path.realpath(os.path.abspath(candidate))
            result['parser'] = {'kind': 'node --check (stdin)', 'path': node}
            if language == 'html':
                html = InlineScripts()
                html.feed(text)
                html.close()
                if html.current is not None:
                    raise ValueError('unclosed-script-element')
                scripts = html.scripts
                result.update(externalScripts=html.external, ignoredDataScripts=html.ignored)
                result['parser']['limitation'] = 'Classic scripts use Node commonjs syntax; browser behavior and global cross-script interactions are unverified.'
            else:
                scripts = [('module' if Path(path).suffix == '.mjs' else 'commonjs', text, 1)]
            deadline = time.monotonic() + TOTAL_SECONDS
            for dialect in sorted({item[0] for item in scripts} or {'commonjs'}):
                good = node_check(node, 'const value = 1;\n', dialect, deadline)
                bad = node_check(node, 'const = ;\n', dialect, deadline)
                if not good['ok'] or bad['ok'] or bad['reason'] != 'parser-rejected':
                    raise ValueError('parser-self-test-failed')
            result['selfTestPassed'] = True
            for dialect, source, line in scripts:
                checked = node_check(node, source, dialect, deadline)
                checked.update(dialect=dialect, htmlLine=line if language == 'html' else None)
                result['checks'].append(checked)
                if not checked['ok']:
                    return result
            if language == 'html' and (result['externalScripts'] or not scripts):
                result.update(status='unverified', reason='external-or-absent-inline-javascript')
                return result
        else:
            raise ValueError('unknown-language')
        result['status'] = 'passed'
    except (OSError, ValueError, SyntaxError, RecursionError) as error:
        result['reason'] = str(error)[:1024]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=('python', 'javascript', 'html'), required=True)
    parser.add_argument('--file', required=True)
    parser.add_argument('--node')
    args = parser.parse_args(argv)
    result = check_source(args.file, args.language, args.node)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
