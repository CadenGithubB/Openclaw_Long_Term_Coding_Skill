// Trusted controller storage only. Workers must never receive this path or UID.
// Derived from the sealed Stage 4G filesystem checks; no edits to that stage.
import fs from 'node:fs';
import path from 'node:path';

export function unavailable() { throw new Error('recovery-store-unavailable-or-unconfirmed'); }
const identity = st => `${st.dev}:${st.ino}`;
function file(st, maximum) {
  if (!st.isFile() || st.nlink !== 1 || st.uid !== process.getuid()
      || (st.mode & 0o7777) !== 0o600 || st.size > maximum) unavailable();
}
export function pinStore(root, create) {
  if (typeof root !== 'string' || !path.isAbsolute(root) || path.normalize(root) !== root
      || root === '/' || root.length > 2048 || /[\x00-\x1f]/.test(root)) unavailable();
  const pins = [];
  let current = '/', fd;
  const name = path.join(root, 'recovery.sqlite');
  function checkDir() {
    for (const p of pins) {
      const st = fs.fstatSync(p.fd), named = fs.lstatSync(p.path);
      const stickyRoot = st.uid === 0 && (st.mode & 0o1000) !== 0;
      if (!st.isDirectory() || !named.isDirectory() || identity(st) !== p.id || identity(named) !== p.id
          || ![0, process.getuid()].includes(st.uid) || ((st.mode & 0o022) !== 0 && !stickyRoot)) unavailable();
    }
    const leaf = fs.fstatSync(pins.at(-1).fd);
    if (leaf.uid !== process.getuid() || (leaf.mode & 0o7777) !== 0o700) unavailable();
  }
  function sidecars() {
    for (const suffix of ['-journal', '-wal', '-shm']) {
      let st;
      try { st = fs.lstatSync(name + suffix); } catch (e) { if (e.code === 'ENOENT') continue; throw e; }
      if (suffix !== '-journal') unavailable();
      file(st, 16777216);
    }
  }
  function check() {
    checkDir(); sidecars();
    const st = fs.fstatSync(fd), named = fs.lstatSync(name);
    file(st, 8388608); file(named, 8388608);
    if (identity(st) !== identity(named)) unavailable();
  }
  function close() {
    if (fd !== undefined) { fs.closeSync(fd); fd = undefined; }
    for (const p of pins.splice(0)) fs.closeSync(p.fd);
  }
  try {
    for (const part of ['', ...root.slice(1).split('/')]) {
      if (part) current = path.join(current, part);
      const dirfd = fs.openSync(current, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
      pins.push({ path: current, fd: dirfd, id: identity(fs.fstatSync(dirfd)) });
    }
    checkDir(); sidecars();
    // Never overwrite an existing database, follow a link, or block opening a FIFO.
    fd = fs.openSync(name, fs.constants.O_RDWR | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK
      | (create ? fs.constants.O_CREAT | fs.constants.O_EXCL : 0), 0o600);
    check();
    return Object.freeze({ name, check, close,
      sync() { check(); fs.fsyncSync(fd); fs.fsyncSync(pins.at(-1).fd); check(); } });
  } catch { close(); unavailable(); }
}
