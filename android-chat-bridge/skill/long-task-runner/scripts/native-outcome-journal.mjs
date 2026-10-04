// Trusted native lifecycle bookkeeping. No native calls, dispatch or claim recovery.
import { DatabaseSync } from 'node:sqlite';
import { createHash, randomBytes } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { pinStore } from './private-store-files.mjs';

const SCHEMA = Object.freeze({
  registration: 'CREATE TABLE registration (singleton INTEGER PRIMARY KEY CHECK(singleton=1), body TEXT NOT NULL, digest TEXT NOT NULL) STRICT',
  events: 'CREATE TABLE events (revision INTEGER PRIMARY KEY, body TEXT NOT NULL, parent TEXT NOT NULL, digest TEXT NOT NULL) STRICT',
  header: 'CREATE TABLE header (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL, digest TEXT NOT NULL, holder TEXT) STRICT',
});
const hash = raw => createHash('sha256').update(raw).digest('hex');
const encode = value => JSON.stringify(value);
const hex = (v, n = 64) => typeof v === 'string' && new RegExp(`^[a-f0-9]{${n}}$`).test(v);
const fail = () => { throw new Error('native-outcome-unavailable-or-unconfirmed'); };
function need(value) { if (!value) fail(); }
function exact(value, keys) {
  need(value !== null && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value))
    && Reflect.ownKeys(value).length === keys.length);
  for (const key of keys) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    need(descriptor && Object.hasOwn(descriptor, 'value'));
  }
}
function frozen(value) {
  if (value && typeof value === 'object') { Object.values(value).forEach(frozen); Object.freeze(value); }
  return value;
}
function canonical(text, maximum) {
  need(typeof text === 'string' && text.length > 0 && text.length <= maximum && /^[\x20-\x7e]*$/.test(text));
  let value; try { value = JSON.parse(text); } catch { fail(); }
  need(encode(value) === text); return value;
}
function registration(text) {
  const r = canonical(text, 4096);
  exact(r, ['format', 'jobId', 'runId', 'sessionKey', 'gatewayInstanceId', 'sourceHashes']);
  need(r.format === 1 && typeof r.jobId === 'string' && /^[a-z][a-z0-9-]{0,47}$/.test(r.jobId)
    && typeof r.runId === 'string' && /^[A-Za-z0-9-]{1,128}$/.test(r.runId)
    && typeof r.sessionKey === 'string' && /^agent:devlab:[A-Za-z0-9:_-]{1,160}$/.test(r.sessionKey)
    && hex(r.gatewayInstanceId, 32));
  exact(r.sourceHashes, ['controllerSha256', 'pluginSha256', 'nativeRuntimeSha256']);
  need(Object.values(r.sourceHashes).every(v => hex(v)));
  return frozen(r);
}
function token(value) {
  exact(value, ['revision', 'chainSha256']);
  need(Number.isSafeInteger(value.revision) && value.revision >= 0 && value.revision <= 4 && hex(value.chainSha256));
  return { revision: value.revision, chainSha256: value.chainSha256 };
}
function sameToken(a, b) { return a.revision === b.revision && a.chainSha256 === b.chainSha256; }
function sessionId(value) { need(typeof value === 'string' && /^[A-Za-z0-9:_-]{1,128}$/.test(value)); }
function outcome(value) {
  exact(value, ['nativeJoined', 'capabilityRetired', 'cancellationRequested', 'outcome']);
  need(value.nativeJoined === true && value.capabilityRetired === true
    && typeof value.cancellationRequested === 'boolean' && ['returned', 'rejected'].includes(value.outcome));
}
function settings(db, write) {
  need(typeof db.enableDefensive === 'function'); db.enableDefensive(true);
  db.exec('PRAGMA trusted_schema=OFF; PRAGMA busy_timeout=0; PRAGMA cell_size_check=ON;');
  if (write) db.exec('PRAGMA synchronous=FULL; PRAGMA fullfsync=ON; PRAGMA max_page_count=512;');
  else db.exec('PRAGMA query_only=ON;');
  need(db.prepare('PRAGMA journal_mode').get().journal_mode === 'delete');
  if (write) need(db.prepare('PRAGMA synchronous').get().synchronous === 2
    && db.prepare('PRAGMA fullfsync').get().fullfsync === 1);
  else need(db.prepare('PRAGMA query_only').get().query_only === 1);
}
function initial(registrationText) {
  return { registrationSha256: hash(registrationText), phase: 'registered', sessionId: null,
    sessionCreateOperationId: null, launchOperationId: null, nativeJoined: false,
    capabilityRetired: false, cancellationRequested: null };
}
function reduce(state, event) {
  exact(event, ['kind', 'data']); const data = event.data;
  if (event.kind === 'session-create-intent') {
    exact(data, ['operationId']); need(state.phase === 'registered' && hex(data.operationId, 32));
    state.sessionCreateOperationId = data.operationId; state.phase = 'session-create-pending';
  } else if (event.kind === 'session-created') {
    exact(data, ['operationId', 'sessionId']); sessionId(data.sessionId);
    need(state.phase === 'session-create-pending' && data.operationId === state.sessionCreateOperationId);
    state.sessionId = data.sessionId; state.phase = 'session-created';
  } else if (event.kind === 'launch-intent') {
    exact(data, ['operationId', 'sessionId']);
    need(state.phase === 'session-created' && data.sessionId === state.sessionId && hex(data.operationId, 32)
      && data.operationId !== state.sessionCreateOperationId);
    state.launchOperationId = data.operationId; state.phase = 'launch-pending';
  } else if (event.kind === 'observed-outcome') {
    exact(data, ['operationId', 'sessionId', 'nativeJoined', 'capabilityRetired', 'cancellationRequested', 'outcome']);
    outcome({ nativeJoined: data.nativeJoined, capabilityRetired: data.capabilityRetired,
      cancellationRequested: data.cancellationRequested, outcome: data.outcome });
    need(state.phase === 'launch-pending' && data.operationId === state.launchOperationId && data.sessionId === state.sessionId);
    state.nativeJoined = true; state.capabilityRetired = true; state.cancellationRequested = data.cancellationRequested;
    state.phase = `observed-${data.outcome}`;
  } else fail();
}
function view(state, head, holder, r) {
  // Join/retirement booleans describe recorded original-call observations only.
  // Missing cancellation observation is null, never inferred as non-cancellation.
  const reason = { registered: 'no-session-create-intent-recorded',
    'session-create-pending': 'session-create-outcome-unknown', 'session-created': 'native-launch-not-recorded',
    'launch-pending': 'native-run-outcome-unknown', 'observed-returned': 'original-native-return-observed',
    'observed-rejected': 'original-native-rejection-observed' }[state.phase];
  return frozen({ ...state, token: head, identity: { jobId: r.jobId, runId: r.runId,
    sessionKey: r.sessionKey, gatewayInstanceId: r.gatewayInstanceId }, holderRetained: holder !== null,
    providerInactive: 'unknown', reason, nextPermittedAction: 'inspect-only',
    allowDispatch: false, allowRelease: false, allowRetry: false, allowPublication: false });
}
function readState(db, text) {
  const schema = db.prepare('SELECT type,name,sql FROM sqlite_schema ORDER BY name LIMIT 4').all();
  need(schema.length === 3 && schema.every(row => row.type === 'table' && SCHEMA[row.name] === row.sql));
  need(encode(db.prepare('PRAGMA quick_check').all()) === '[{"quick_check":"ok"}]');
  const bindings = db.prepare('SELECT * FROM registration LIMIT 2').all();
  need(bindings.length === 1 && bindings[0].singleton === 1 && bindings[0].body === text && bindings[0].digest === hash(text));
  const headers = db.prepare('SELECT * FROM header LIMIT 2').all(); need(headers.length === 1);
  const h = headers[0]; need(h.singleton === 1 && Number.isSafeInteger(h.revision) && h.revision >= 0 && h.revision <= 4
    && hex(h.digest) && (h.holder === null || hex(h.holder, 32)) && (h.holder !== null || h.revision === 0));
  const rows = db.prepare('SELECT * FROM events ORDER BY revision LIMIT 5').all(); need(rows.length === h.revision);
  const state = initial(text); let parent = hash(text);
  for (const [i, row] of rows.entries()) {
    need(row.revision === i + 1 && row.parent === parent && row.digest === hash(parent + '\n' + row.body));
    reduce(state, canonical(row.body, 1024)); parent = row.digest;
  }
  need(h.digest === parent);
  return { state, head: { revision: h.revision, chainSha256: h.digest }, holder: h.holder };
}

export function initializeNativeOutcome(root, registrationText) {
  const r = registration(registrationText), files = pinStore(root, true); let db;
  try {
    db = new DatabaseSync(files.name, { timeout: 0, allowExtension: false, enableDoubleQuotedStringLiterals: false });
    settings(db, true); db.exec('BEGIN IMMEDIATE');
    for (const sql of Object.values(SCHEMA)) db.exec(sql);
    const digest = hash(registrationText);
    db.prepare('INSERT INTO registration VALUES (1,?,?)').run(registrationText, digest);
    db.prepare('INSERT INTO header VALUES (1,0,?,NULL)').run(digest);
    db.exec('COMMIT'); files.sync();
    return view(initial(registrationText), { revision: 0, chainSha256: digest }, null, r);
  } catch { fail(); }
  finally { try { db?.close(); } finally { files.close(); } }
}

export function openNativeOutcome(root, registrationText, expectedToken) {
  const r = registration(registrationText), expected = token(expectedToken), files = pinStore(root, false);
  let db, closed = false, poisoned = false, holder = randomBytes(16).toString('hex'), latest = expected;
  const pid = process.pid;
  function current() { need(!closed && !poisoned && process.pid === pid); files.check(); }
  function transaction(work, write) {
    let began = false;
    try {
      current(); db.exec(write ? 'BEGIN IMMEDIATE' : 'BEGIN'); began = true;
      const state = readState(db, registrationText); need(sameToken(state.head, latest));
      const result = work(state);
      current(); db.exec('COMMIT'); began = false;
      if (write) files.sync(); else files.check();
      if (result?.token) latest = { ...result.token };
      return result;
    } catch {
      if (began) try { db.exec('ROLLBACK'); } catch {}
      poisoned = true; fail();
    }
  }
  try {
    db = new DatabaseSync(files.name, { timeout: 0, allowExtension: false, enableDoubleQuotedStringLiterals: false });
    settings(db, true);
    transaction(s => {
      need(s.holder === null && s.head.revision === 0 && sameToken(s.head, expected));
      need(db.prepare('UPDATE header SET holder=? WHERE singleton=1 AND holder IS NULL').run(holder).changes === 1);
    }, true);
  } catch { try { db?.close(); } finally { files.close(); } fail(); }
  function mutate(expectedInput, makeEvent) {
    try {
      const expected = token(expectedInput);
      return transaction(s => {
        need(s.holder === holder && sameToken(s.head, expected));
        const event = makeEvent(s.state), next = { ...s.state }; reduce(next, event);
        const body = encode(event), revision = s.head.revision + 1, digest = hash(s.head.chainSha256 + '\n' + body);
        need(revision <= 4);
        db.prepare('INSERT INTO events VALUES (?,?,?,?)').run(revision, body, s.head.chainSha256, digest);
        need(db.prepare('UPDATE header SET revision=?,digest=? WHERE singleton=1 AND revision=? AND digest=? AND holder=?')
          .run(revision, digest, s.head.revision, s.head.chainSha256, holder).changes === 1);
        return view(next, { revision, chainSha256: digest }, holder, r);
      }, true);
    } catch { poisoned = true; fail(); }
  }
  return Object.freeze({
    snapshot() { return transaction(s => { need(s.holder === holder); return view(s.state, s.head, s.holder, r); }, false); },
    sessionCreateIntent(expected) { return mutate(expected, () => ({ kind: 'session-create-intent', data: { operationId: randomBytes(16).toString('hex') } })); },
    sessionCreated(expected, id) { return mutate(expected, s => { sessionId(id); return { kind: 'session-created',
      data: { operationId: s.sessionCreateOperationId, sessionId: id } }; }); },
    launchIntent(expected) { return mutate(expected, s => ({ kind: 'launch-intent',
      data: { operationId: randomBytes(16).toString('hex'), sessionId: s.sessionId } })); },
    observedOutcome(expected, observed) { return mutate(expected, s => { outcome(observed); return { kind: 'observed-outcome',
      data: { operationId: s.launchOperationId, sessionId: s.sessionId, ...observed } }; }); },
    close() { if (closed) return; closed = true; try { db.close(); } finally { files.close(); } },
  });
}

// N's writer helper intentionally opens O_RDWR. This reader pins only O_RDONLY
// descriptors and rejects every sidecar instead of invoking hot-journal recovery.
function pinReadOnly(root, rootPin) {
  need(typeof root === 'string' && path.isAbsolute(root) && path.normalize(root) === root && root !== '/'
    && root.length <= 2048 && !/[\x00-\x1f]/.test(root));
  const pins = []; let fd;
  const name = path.join(root, 'recovery.sqlite');
  const id = st => `${st.dev}:${st.ino}`;
  function check() {
    for (const p of pins) {
      const st = fs.fstatSync(p.fd), named = fs.lstatSync(p.path);
      need(st.isDirectory() && named.isDirectory() && id(st) === p.id && id(named) === p.id
        && [0, process.getuid()].includes(st.uid) && (!(st.mode & 0o022) || (st.uid === 0 && (st.mode & 0o1000))));
    }
    const leaf = fs.fstatSync(pins.at(-1).fd);
    need(leaf.uid === process.getuid() && (leaf.mode & 0o7777) === 0o700);
    if (rootPin) need(leaf.dev === rootPin.dev && leaf.ino === rootPin.ino);
    for (const suffix of ['-journal', '-wal', '-shm']) {
      try { fs.lstatSync(name + suffix); fail(); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    const opened = fs.fstatSync(fd), named = fs.lstatSync(name);
    for (const st of [opened, named]) need(st.isFile() && st.nlink === 1 && st.uid === process.getuid()
      && (st.mode & 0o7777) === 0o600 && st.size <= 2097152);
    need(id(opened) === id(named));
  }
  function close() { if (fd !== undefined) { fs.closeSync(fd); fd = undefined; } for (const p of pins.splice(0)) fs.closeSync(p.fd); }
  try {
    let current = '/';
    for (const part of ['', ...root.slice(1).split('/')]) {
      if (part) current = path.join(current, part);
      const opened = fs.openSync(current, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
      pins.push({ path: current, fd: opened, id: id(fs.fstatSync(opened)) });
    }
    fd = fs.openSync(name, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    check(); return { name, check, close };
  } catch { close(); fail(); }
}

export function readNativeOutcome(root, registrationText, expectedToken, rootPin = null) {
  const r = registration(registrationText), expected = token(expectedToken);
  if (rootPin !== null) { exact(rootPin, ['dev', 'ino']); need(Number.isSafeInteger(rootPin.dev) && rootPin.dev > 0
    && Number.isSafeInteger(rootPin.ino) && rootPin.ino > 0); }
  const files = pinReadOnly(root, rootPin); let db;
  try {
    db = new DatabaseSync(files.name, { readOnly: true, timeout: 0, allowExtension: false, enableDoubleQuotedStringLiterals: false });
    settings(db, false); files.check(); db.exec('BEGIN');
    const s = readState(db, registrationText); need(sameToken(s.head, expected));
    files.check(); db.exec('COMMIT'); files.check();
    return view(s.state, s.head, s.holder, r);
  } catch { fail(); }
  finally { try { db?.close(); } finally { files.close(); } }
}
