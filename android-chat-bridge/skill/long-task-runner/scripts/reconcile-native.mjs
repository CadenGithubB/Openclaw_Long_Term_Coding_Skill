// Read-only reconciliation: this module never launches native work or writes files.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { readNativeOutcome } from './native-outcome-journal.mjs';

const sha = bytes => createHash('sha256').update(bytes).digest('hex');
function need(value) { if (!value) throw new Error('native-outcome-reconciliation-refused'); }
function pinInput(name, checksum) {
  need(typeof name === 'string' && path.isAbsolute(name) && path.normalize(name) === name
    && name.length <= 2048 && !/[\x00-\x1f]/.test(name) && /^[a-f0-9]{64}$/.test(checksum));
  const descriptors = []; let fd;
  const identity = stat => `${stat.dev}:${stat.ino}`;
  function check() {
    for (const pin of descriptors) {
      const actual = fs.fstatSync(pin.fd), named = fs.lstatSync(pin.path);
      need(actual.isDirectory() && named.isDirectory() && identity(actual) === pin.id && identity(named) === pin.id
        && [0, process.getuid()].includes(actual.uid)
        && (!(actual.mode & 0o022) || (actual.uid === 0 && (actual.mode & 0o1000))));
    }
    const opened = fs.fstatSync(fd), named = fs.lstatSync(name);
    for (const item of [opened, named]) need(item.isFile() && item.nlink === 1 && item.uid === process.getuid()
      && (item.mode & 0o7777) === 0o600 && item.size > 0 && item.size <= 4096);
    need(identity(opened) === identity(named));
  }
  function close() { if (fd !== undefined) { fs.closeSync(fd); fd = undefined; } for (const pin of descriptors.splice(0)) fs.closeSync(pin.fd); }
  try {
    let current = '/';
    for (const piece of ['', ...path.dirname(name).slice(1).split('/').filter(Boolean)]) {
      if (piece) current = path.join(current, piece);
      const dir = fs.openSync(current, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
      descriptors.push({ fd: dir, path: current, id: identity(fs.fstatSync(dir)) });
    }
    fd = fs.openSync(name, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    check();
    const bytes = Buffer.alloc(4097); let size = 0;
    while (size < bytes.length) { const n = fs.readSync(fd, bytes, size, bytes.length - size, size); if (!n) break; size += n; }
    need(size > 0 && size <= 4096); const raw = bytes.subarray(0, size);
    need(sha(raw) === checksum && /^[\x20-\x7e]+$/.test(raw.toString('ascii')) && raw.toString('ascii') === raw.toString('utf8'));
    check(); return { text: raw.toString('ascii'), check, close };
  } catch (error) { close(); throw error; }
}
export function reconcileNative({ root, rootPin, registrationPath, registrationSha256, headPath, headSha256 }) {
  let registered, head;
  try {
    registered = pinInput(registrationPath, registrationSha256); head = pinInput(headPath, headSha256);
    const expected = JSON.parse(head.text); need(JSON.stringify(expected) === head.text);
    const snapshot = readNativeOutcome(root, registered.text, expected, rootPin);
    registered.check(); head.check();
    return snapshot;
  } finally { try { head?.close(); } finally { registered?.close(); } }
}
export function main(args = process.argv.slice(2)) {
  const flags = ['--root', '--root-dev', '--root-ino', '--registration', '--registration-sha256', '--head', '--head-sha256'];
  try {
    need(args.length === flags.length * 2); const values = {};
    for (let i = 0; i < args.length; i += 2) {
      need(flags.includes(args[i]) && !Object.hasOwn(values, args[i])); values[args[i]] = args[i + 1];
    }
    for (const key of ['--root-dev', '--root-ino']) need(/^[1-9][0-9]{0,15}$/.test(values[key]) && Number.isSafeInteger(Number(values[key])));
    const value = reconcileNative({ root: values['--root'], rootPin: { dev: Number(values['--root-dev']), ino: Number(values['--root-ino']) },
      registrationPath: values['--registration'], registrationSha256: values['--registration-sha256'],
      headPath: values['--head'], headSha256: values['--head-sha256'] });
    process.stdout.write(JSON.stringify({ nativeOutcomeReconciliation: value }) + '\n'); return 0;
  } catch { process.stderr.write('native-outcome-reconciliation-refused\n'); return 2; }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) process.exitCode = main();
