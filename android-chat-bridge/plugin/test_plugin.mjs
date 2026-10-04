import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import plugin, { CONTROLLER, createTool, loadPng, register, runClient, validateParams } from './index.js';

const JOB = 'am-' + '1'.repeat(32);
const OTHER_JOB = 'am-' + '2'.repeat(32);
const CONTEXT = { agentId: 'main', sessionKey: 'agent:main:normal-android', sessionId: 'session-123', activeModel: { provider: 'ollama', modelId: 'qwen3.6:35b' } };
const response = extra => ({ ok: true, summary: 'The action completed.', jobId: JOB, ...extra });
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a54cAAAAASUVORK5CYII=', 'base64');

// Six diagnostic blocks from the retained 4AN compiler receipt; no chat,
// session identifiers or full application source are included in this fixture.
const JAVA_ERRORS = [
  'BirdView.java:123: error: method does not override or implement a method from a supertype\n    @Override\n    ^',
  'BirdView.java:132: error: method does not override or implement a method from a supertype\n    @Override\n    ^',
  'BirdView.java:184: error: incompatible types: possible lossy conversion from float to int\n            int maxYGap = SCREEN_H - GROUND_H - PIPE_GAP - 80;\n                                                         ^',
  'BirdView.java:289: error: incompatible types: int cannot be converted to Paint\n        c.drawCircle(birdX + 8, birdY - 6, 1, Color.WHITE);\n                                                   ^',
  'MainActivity.java:50: error: cannot find symbol\n        gameView.setMaxWidth(scaledW);\n                ^\n  symbol:   method setMaxWidth(int)\n  location: variable gameView of type BirdView',
  'MainActivity.java:51: error: cannot find symbol\n        gameView.setMaxHeight(scaledH);\n                ^\n  symbol:   method setMaxHeight(int)\n  location: variable gameView of type BirdView',
];
function javaFailure(blocks = JAVA_ERRORS) {
  const diagnostics = blocks.map(block => '/workspace/project/app/src/main/java/org/openclaw/trial/' + block).join('\n');
  const notes = 'Note: Some messages have been simplified; recompile with -Xdiags:verbose to get full output';
  return 'offline-build failed: ' + '> Task :app:preBuild UP-TO-DATE\n'.repeat(40)
    + `> Task :app:compileDebugJavaWithJavac FAILED\n${diagnostics}\n${notes}\n${blocks.length} errors\n`
    + '\nFAILURE: Build failed with an exception.\n\n* What went wrong:\n'
    + "Execution failed for task ':app:compileDebugJavaWithJavac'.\n> Compilation failed; see the compiler output below.\n"
    + [diagnostics, notes, `${blocks.length} errors`].join('\n').split('\n').map(line => '  ' + line).join('\n')
    + '\n\n* Try:\n> Check your code and dependencies to fix the compilation error(s)\n> Run with --scan to get full insights.\n\nBUILD FAILED in 32s\n';
}

async function presentedBuild(summary, extra = {}, action = 'build') {
  const receipt = response({ ok: false, summary, ...extra });
  const before = structuredClone(receipt), calls = [];
  const tool = createTool(CONTEXT, { client: async payload => { calls.push(payload); return receipt; } });
  const result = await tool.execute('call_feedback', { action, jobId: JOB });
  assert.deepEqual(receipt, before, 'presentation must not mutate the retained controller receipt');
  assert.equal(calls.length, 1, 'a structured failure must not trigger cleanup or retries');
  return { result, record: JSON.parse(result.content[0].text), receipt };
}

function fakeSpawn(behavior) {
  const calls = [];
  const spawnProcess = (...args) => {
    const child = new EventEmitter();
    child.stdin = new PassThrough(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
    const call = { args, child, input: [], signals: [] }; calls.push(call);
    child.stdin.on('data', data => call.input.push(data));
    child.kill = signal => { call.signals.push(signal); queueMicrotask(() => child.emit('close', null)); return true; };
    child.stdin.on('finish', () => queueMicrotask(() => behavior(call)));
    return child;
  };
  return { calls, spawnProcess };
}

test('manifest declares startup activation and the one discoverable tool contract', async () => {
  const manifest = JSON.parse(await fs.readFile(new URL('./openclaw.plugin.json', import.meta.url), 'utf8'));
  assert.deepEqual(manifest.activation, { onStartup: true });
  assert.deepEqual(manifest.contracts, { tools: ['android_project'] });
  assert.deepEqual(manifest.configSchema, { type: 'object', additionalProperties: false, properties: {} });
});

test('main tool registration is optional, sequential and uses no conversation hooks', () => {
  let factory, options;
  register({ registerTool(fn, opts) { factory = fn; options = opts; }, on() { assert.fail('conversation hooks must not be registered'); } });
  assert.equal(plugin.id, 'android-chat-bridge');
  assert.deepEqual(options, { names: ['android_project'], optional: true });
  assert.equal(factory(CONTEXT).executionMode, 'sequential');
  assert.equal(factory({ agentId: 'main' }).name, 'android_project');
  assert.equal(factory({ ...CONTEXT, agentId: 'devlab' }), null);
  assert.match(factory(CONTEXT).description, /read the available long-task-runner skill/);
  assert.match(factory(CONTEXT).description, /prepare for the actual environment check/);
});

test('unknown and non-main agents expose no tool even with a main-looking session key', () => {
  for (const ctx of [undefined, {}, { ...CONTEXT, agentId: undefined }, { ...CONTEXT, agentId: 'devlab' }]) {
    assert.equal(createTool(ctx), null);
  }
});

test('main catalog remains describable with missing identity but cannot execute or spoof it', async () => {
  for (const ctx of [{ agentId: 'main' }, { ...CONTEXT, sessionId: '' }, { ...CONTEXT, sessionKey: undefined }, { ...CONTEXT, sessionKey: 'a|b' }, { ...CONTEXT, sessionId: 'a\nb' }]) {
    let calls = 0;
    const tool = createTool(ctx, { client: async () => { calls++; return response(); } });
    assert.equal(tool.name, 'android_project');
    await assert.rejects(tool.execute('call_catalog', { action: 'prepare' }), /trusted main-session identity/);
    await assert.rejects(tool.execute('call_catalog', { action: 'prepare', actor: CONTEXT.sessionKey + '|' + CONTEXT.sessionId }), /trusted main-session identity/);
    assert.equal(calls, 0);
  }
});

test('tool remains describable without active model metadata but execution fails closed', async () => {
  for (const model of [undefined, {}, { provider: 'openai', modelId: 'qwen3.6:35b' }, { provider: 'ollama', modelId: 'qwen3.6:cloud' }, { provider: 'openai', modelRef: 'ollama/qwen3.6:35b' }, { provider: 'ollama', modelId: 'qwen3.6:35b', modelRef: 'openai/other' }]) {
    let calls = 0;
    const tool = createTool({ ...CONTEXT, activeModel: model }, { client: async () => { calls++; return response(); } });
    assert.equal(tool.name, 'android_project');
    await assert.rejects(tool.execute('call_1', { action: 'prepare' }), /requires the verified local/);
    assert.equal(calls, 0);
  }
});

test('exact modelRef variant is accepted without inferred provider metadata', async () => {
  const tool = createTool({ ...CONTEXT, activeModel: { modelRef: 'ollama/qwen3.6:35b' } }, { client: async () => response() });
  assert.equal((await tool.execute('call_ref', { action: 'prepare' })).details.ok, true);
});

test('actor comes only from captured trusted context; request values are snapshotted', async () => {
  const context = structuredClone(CONTEXT), calls = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const tool = createTool(context, { client: async payload => { calls.push(payload); await gate; return response(); } });
  const params = { action: 'write_sources', jobId: JOB, files: [{ name: 'MainActivity.java', content: 'class MainActivity {}' }], reason: 'Draw the game in an original native view.' };
  const pending = tool.execute('call_bound', params);
  params.files[0].content = 'mutated'; context.sessionKey = 'changed';
  release(); await pending;
  assert.equal(calls[0].actor, CONTEXT.sessionKey + '|' + CONTEXT.sessionId);
  assert.equal(calls[0].requestId, 'call_bound');
  assert.equal(calls[0].params.files[0].content, 'class MainActivity {}');
});

test('initial time budget is described without injecting a global or existing-job default', async () => {
  const calls = [];
  const tool = createTool(CONTEXT, { client: async payload => { calls.push(payload); return response(); } });
  const field = tool.parameters.properties.timeLimitMinutes;
  assert.equal(field.type, 'integer'); assert.equal(field.minimum, 5); assert.equal(field.maximum, 60);
  assert.equal(Object.hasOwn(field, 'default'), false);
  assert.equal(tool.parameters.required.includes('timeLimitMinutes'), false);
  assert.match(field.description, /prepare only/);
  assert.match(field.description, /new-job default of 30 minutes/);
  assert.match(field.description, /cannot extend an existing job or the separate chat deadline/);
  for (const params of [{ action: 'prepare' }, ...[5, 30, 60].map(timeLimitMinutes => ({ action: 'prepare', timeLimitMinutes }))]) {
    await tool.execute('call_initial_budget', params);
    assert.deepEqual(calls.at(-1).params, params);
  }
  assert.equal(Object.hasOwn(calls[0].params, 'timeLimitMinutes'), false);
});

test('initial time budget rejects nonintegers, coercion and out-of-range values before any client action', async () => {
  let calls = 0, coerced = false, getterRead = false;
  const tool = createTool(CONTEXT, { client: async () => { calls++; return response(); } });
  const values = [undefined, null, true, false, '30', '5', 5.5, 30.5, 4, 61, 0, -5, Infinity, -Infinity, NaN, [], {}, new Number(30), { valueOf() { coerced = true; return 30; } }];
  for (const timeLimitMinutes of values) {
    await assert.rejects(tool.execute('call_invalid_budget', { action: 'prepare', timeLimitMinutes }), /timeLimitMinutes must be an integer from 5 to 60/);
  }
  const accessor = { action: 'prepare' };
  Object.defineProperty(accessor, 'timeLimitMinutes', { enumerable: true, get() { getterRead = true; return 30; } });
  await assert.rejects(tool.execute('call_budget_accessor', accessor), /accessors are forbidden/);
  assert.equal(calls, 0); assert.equal(coerced, false); assert.equal(getterRead, false);
});

test('only prepare accepts a time budget and no budget alias reaches the controller', async () => {
  let calls = 0;
  const tool = createTool(CONTEXT, { client: async () => { calls++; return response(); } });
  for (const action of ['write_sources', 'build', 'start_test', 'tap', 'observe', 'status', 'stop']) {
    await assert.rejects(tool.execute('call_wrong_action_budget', { action, jobId: JOB, timeLimitMinutes: 60 }), /field does not apply to this action/);
  }
  for (const key of ['timeLimitSeconds', 'deadlineSeconds', 'timeoutMs', 'durationMinutes']) {
    await assert.rejects(tool.execute('call_budget_alias', { action: 'prepare', [key]: 60 }), /unknown request field/);
  }
  assert.equal(calls, 0);
});

test('validated initial budget is captured before await and existing-job refusal triggers no retry or cleanup', async () => {
  const calls = [];
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const receipt = response({ ok: false, summary: 'The existing job keeps its original time budget; it cannot be extended.', status: 'source-ready', progress: { controllerTimeLimitMinutes: 5, controllerSecondsRemaining: 120 } });
  const tool = createTool(CONTEXT, { client: async payload => { calls.push(payload); await gate; return receipt; } });
  const target = { action: 'prepare', timeLimitMinutes: 5 };
  const params = new Proxy(target, {});
  const pending = tool.execute('call_snapshot_budget', params);
  target.timeLimitMinutes = 60;
  release();
  const result = await pending;
  assert.deepEqual(calls[0].params, { action: 'prepare', timeLimitMinutes: 5 });
  assert.notEqual(calls[0].params, params);
  assert.equal(calls.length, 1);
  assert.equal(result.details.ok, false);
  assert.deepEqual(JSON.parse(result.content[0].text), receipt);
});

test('unknown fields and model/context/host-command spoofing cannot reach the controller', async () => {
  let calls = 0;
  const tool = createTool(CONTEXT, { client: async () => { calls++; return response(); } });
  for (const field of ['actor', 'sessionKey', 'sessionId', 'requestId', 'model', 'command', 'path', 'durationMs', '__proto__']) {
    const value = { action: 'prepare' };
    Object.defineProperty(value, field, { value: 'untrusted', enumerable: true });
    await assert.rejects(tool.execute('call_unknown', value), /unknown request field/);
  }
  assert.equal(calls, 0);
});

test('accessors and exotic objects are rejected without running their code', () => {
  let read = false;
  const value = { action: 'prepare' };
  Object.defineProperty(value, 'reason', { get() { read = true; return 'bad'; }, enumerable: true });
  assert.throws(() => validateParams(value), /accessors/);
  assert.equal(read, false);
  assert.throws(() => validateParams(Object.create({ action: 'prepare' })), /must be an object/);
  assert.throws(() => validateParams({ action: 'prepare', [Symbol('x')]: true }), /unknown/);
});

test('Java source accepts only unique simple class filenames and text', () => {
  for (const name of ['../MainActivity.java', '/tmp/Main.java', 'foo/Main.java', 'foo\\Main.java', 'build.gradle', '.java', 'a.java\0', 'name.class', 'lowercase.java', '$View.java', 'A$View.java', 'A'.repeat(65) + '.java']) {
    assert.throws(() => validateParams({ action: 'write_sources', jobId: JOB, files: [{ name, content: 'x' }] }), /filename/);
  }
  assert.throws(() => validateParams({ action: 'write_sources', jobId: JOB, files: [{ name: 'A.java', content: 'x', path: '/tmp' }] }), /unknown file field/);
  assert.throws(() => validateParams({ action: 'write_sources', jobId: JOB, files: [{ name: 'A.java', content: 1 }] }), /128 KiB/);
  assert.throws(() => validateParams({ action: 'write_sources', jobId: JOB, files: [{ name: 'A.java', content: 'x' }, { name: 'A.java', content: 'y' }] }), /duplicate/);
  assert.equal(validateParams({ action: 'write_sources', jobId: JOB, files: [{ name: 'BirdView.java', content: 'class BirdView {}' }] }).files.length, 1);
  assert.equal(validateParams({ action: 'write_sources', jobId: JOB, files: [{ name: 'A'.repeat(64) + '.java', content: '// bounded name' }] }).files.length, 1);
});

test('Java-only contract remains explicit when catalog normalization omits regex patterns', () => {
  const tool = createTool(CONTEXT);
  const files = structuredClone(tool.parameters.properties.files);
  delete files.items.properties.name.pattern;
  assert.match(files.description, /Java class source files only/);
  assert.match(files.items.properties.name.description, /MainActivity\.java/);
  assert.match(files.items.properties.name.description, /Do not include directories, paths or XML/);
  assert.match(files.items.properties.content.description, /package org\.openclaw\.trial/);
  assert.match(files.items.properties.content.description, /programmatically/);
  assert.match(files.items.properties.content.description, /Do not reference custom R\.layout\/R\.id/);
  assert.match(tool.description, /no XML layouts/);
});

test('XML and path submissions get actionable Java guidance before any controller call', async () => {
  let calls = 0;
  const tool = createTool(CONTEXT, { client: async () => { calls++; return response(); } });
  for (const name of ['activity_main.xml', 'app/src/main/java/org/openclaw/trial/MainActivity.java']) {
    await assert.rejects(tool.execute('call_source_format', { action: 'write_sources', jobId: JOB, files: [{ name, content: 'unaccepted file' }] }), /MainActivity\.java; basename only, no directories or XML\/resource files/);
  }
  assert.equal(calls, 0);
});

test('Java source refuses empty content and embedded NUL before controller admission', async () => {
  let calls = 0;
  const tool = createTool(CONTEXT, { client: async () => { calls++; return response(); } });
  for (const content of ['', 'class A {\0}']) {
    await assert.rejects(tool.execute('call_empty', { action: 'write_sources', jobId: JOB, files: [{ name: 'A.java', content }] }), /nonempty text without NUL/);
  }
  assert.equal(calls, 0);
});

test('source limits count UTF-8 bytes and bound the whole batch', () => {
  const file = content => ({ name: 'A.java', content });
  assert.throws(() => validateParams({ action: 'write_sources', jobId: JOB, files: [file('é'.repeat(65537))] }), /128 KiB/);
  assert.throws(() => validateParams({ action: 'write_sources', jobId: JOB, files: Array.from({ length: 5 }, (_, i) => ({ name: `A${i}.java`, content: 'x'.repeat(128 * 1024) })) }), /512 KiB/);
  assert.throws(() => validateParams({ action: 'write_sources', jobId: JOB, files: [] }), /1 to 16/);
  assert.throws(() => validateParams({ action: 'write_sources', jobId: JOB, files: Array.from({ length: 17 }, (_, i) => ({ name: `A${i}.java`, content: '' })) }), /1 to 16/);
});

test('tap limits and action-specific fields cannot be bypassed', () => {
  const tap = { action: 'tap', jobId: JOB, x: 10, y: 20 };
  for (const patch of [{ x: -1 }, { x: 1.1 }, { x: Number.MAX_SAFE_INTEGER + 1 }, { x: 8193 }, { y: 8193 }, { y: '3' }, { count: 0 }, { count: 31 }, { intervalMs: 79 }, { intervalMs: 1501 }]) {
    assert.throws(() => validateParams({ ...tap, ...patch }), /coordinates|count|interval/);
  }
  assert.throws(() => validateParams({ action: 'prepare', jobId: JOB }), /does not apply/);
  assert.throws(() => validateParams({ action: 'build' }), /jobId is required/);
  assert.throws(() => validateParams({ action: 'build', jobId: 'am-../bad' }), /invalid jobId/);
  assert.throws(() => validateParams({ action: 'observe', jobId: JOB, count: 2 }), /does not apply/);
  assert.throws(() => validateParams({ action: 'prepare', reason: 'x'.repeat(1001) }), /reason/);
});

test('tap bursts use controller defaults and stop at fifteen seconds', () => {
  const tap = { action: 'tap', jobId: JOB, x: 8192, y: 8192 };
  assert.equal(validateParams({ ...tap, count: 11, intervalMs: 1500 }).count, 11);
  assert.throws(() => validateParams({ ...tap, count: 12, intervalMs: 1500 }), /burst exceeds 15000/);
  assert.throws(() => validateParams({ ...tap, count: 30, intervalMs: 518 }), /burst exceeds 15000/);
  assert.equal(validateParams({ ...tap, count: 30 }).count, 30);
  assert.equal(validateParams({ ...tap, intervalMs: 1500 }).intervalMs, 1500);
});

test('already aborted call performs no work or cleanup request', async () => {
  let calls = 0; const abort = new AbortController(); abort.abort();
  const tool = createTool(CONTEXT, { client: async () => { calls++; return response(); } });
  await assert.rejects(tool.execute('call_preabort', { action: 'prepare' }, abort.signal), { name: 'AbortError' });
  assert.equal(calls, 0);
});

test('in-flight prepare cancellation sends actor-bound stop without a caller-chosen job', async () => {
  const calls = [], abort = new AbortController();
  let entered;
  const ready = new Promise(resolve => { entered = resolve; });
  const client = (payload, options) => {
    calls.push({ payload, options });
    if (payload.params.action === 'stop') return Promise.resolve(response({ summary: 'Stop requested.' }));
    return new Promise((_, reject) => {
      options.signal.addEventListener('abort', () => { const error = new Error('cancelled'); error.name = 'AbortError'; reject(error); }, { once: true });
      entered();
    });
  };
  const pending = createTool(CONTEXT, { client }).execute('call_abort', { action: 'prepare' }, abort.signal);
  await ready; abort.abort(); await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].payload, { actor: CONTEXT.sessionKey + '|' + CONTEXT.sessionId, requestId: 'call_abort:abort', params: { action: 'stop', reason: 'The calling Android action was interrupted or its receipt could not be verified.' } });
  assert.equal(calls[1].options.signal, undefined);
  assert.equal(calls[1].options.timeoutMs, 30000);
});

test('an uncertain build transport triggers cleanup once and does not retry work', async () => {
  const calls = [];
  const client = async payload => { calls.push(payload); if (payload.params.action === 'build') throw new Error('transport lost'); return response(); };
  await assert.rejects(createTool(CONTEXT, { client }).execute('call_lost', { action: 'build', jobId: JOB }), /transport lost.*acknowledged the stop/);
  assert.deepEqual(calls.map(x => x.params.action), ['build', 'stop']);
  assert.equal(calls[1].params.jobId, JOB);
});

test('cleanup failure remains explicitly unconfirmed', async () => {
  const calls = [];
  const client = async payload => { calls.push(payload); throw new Error('lost'); };
  await assert.rejects(createTool(CONTEXT, { client }).execute('call_uncertain', { action: 'build', jobId: JOB }), /cleanup is unconfirmed/);
  assert.equal(calls.length, 2);
});

test('structured build failure is preserved for repair without inventing success or stopping the job', async () => {
  let calls = 0;
  const tool = createTool(CONTEXT, { client: async () => { calls++; return response({ ok: false, summary: 'Compilation failed.', exitCode: 1 }); } });
  const result = await tool.execute('call_compile', { action: 'build', jobId: JOB });
  assert.equal(Object.hasOwn(result, 'isError'), false); assert.equal(result.details.ok, false);
  assert.equal(JSON.parse(result.content[0].text).exitCode, 1); assert.equal(calls, 1);
  assert.match(result.content[0].text, /Compilation failed/);
  assert.match(result.content[0].text, /"ok": false/);
  assert.equal(result.content.length, 1);
});

test('one receipt preserves progress and outcome metadata without repeating summaries in details', async () => {
  const receipt = response({
    status: 'built', reportPath: '/retained/report.md', reportStatus: 'available',
    progress: { sourceRevision: 2, qualifiedApkSourceRevision: 2, writesRemaining: 6, buildsRemaining: 1, actionsRemaining: 35, controllerSecondsRemaining: 214 },
    progressSummary: 'Revision 2 has a verified APK; one build remains.',
    apk: { sha256: 'a'.repeat(64), size: 14180 }, baseline: { network: 'none' },
    cleanup: null, futureMetadata: { retained: true },
  });
  const result = await createTool(CONTEXT, { client: async () => receipt }).execute('call_metadata', { action: 'build', jobId: JOB });
  assert.equal(result.content.length, 1);
  assert.deepEqual(JSON.parse(result.content[0].text), receipt);
  assert.deepEqual(result.details, { ok: true, jobId: JOB, status: 'built', reportPath: '/retained/report.md', reportStatus: 'available' });
  const serialized = JSON.stringify(result);
  for (const phrase of [receipt.summary, receipt.progressSummary, receipt.apk.sha256]) assert.equal(serialized.split(phrase).length - 1, 1);
});

test('six actual Java error forms retain source, caret and missing-symbol context once', async () => {
  const raw = javaFailure();
  const { result, record } = await presentedBuild(raw, { status: 'building', reportPath: '/retained/report.md' });
  assert.match(record.summary, /6 reported errors.*6 distinct compiler blocks/);
  for (const block of JAVA_ERRORS) {
    assert.equal(record.summary.split(block).length - 1, 1);
    assert.equal(JSON.stringify(result).split(block.split('\n')[0]).length - 1, 1);
  }
  assert.match(record.summary, /symbol:   method setMaxWidth\(int\)/);
  assert.match(record.summary, /symbol:   method setMaxHeight\(int\)/);
  assert.match(record.summary, /location: variable gameView of type BirdView/);
  assert.match(record.summary, /Some messages have been simplified/);
  assert.match(record.summary, /source edit still needs a successful build/);
  assert.match(record.summary, /Full build logs and the original controller receipt remain.*reportPath/);
  assert.doesNotMatch(record.summary, /Task :app:preBuild/);
  assert.equal(result.details.ok, false);
  assert.equal(result.content.length, 1);
  assert.ok(record.summary.length < raw.length / 2);
});

test('diagnostic selection deduplicates complete blocks and explicitly reports omitted errors', async () => {
  const blocks = Array.from({ length: 10 }, (_, index) => `MainActivity.java:${index + 1}: error: cannot find symbol\n    missing${index}();\n    ^\n  symbol: method missing${index}()`);
  const repeated = javaFailure([...blocks, blocks[0]]).replaceAll('11 errors', '10 errors');
  const { record } = await presentedBuild(repeated);
  assert.match(record.summary, /first 8 of 10 distinct compiler blocks/);
  assert.equal(record.summary.match(/\.java:[0-9]+: error:/g).length, 8);
  for (const block of blocks.slice(0, 8)) assert.equal(record.summary.split(block).length - 1, 1);
  assert.doesNotMatch(record.summary, /missing8|missing9/);
  assert.match(record.summary, /Full build logs/);
});

test('unknown, mixed, incomplete and non-build failures preserve their entire original diagnostic', async () => {
  const raw = javaFailure();
  const unknown = [
    'offline-build failed: Could not resolve the Android plugin. Offline cache was missing.',
    raw.replace('offline-build failed: ', 'other-build failed: '),
    raw.replace("Execution failed for task ':app:compileDebugJavaWithJavac'.", "Execution failed for task ':app:mergeDebugResources'."),
    raw.replace('  6 errors', '  7 errors'),
    raw.replace('BUILD FAILED in 32s\n', ''),
    raw.replace('\n* Try:', '\nCaused by: disk I/O failed\n* Try:'),
    raw.replace('\n* Try:', '\nFAILURE: A second task failed.\n* Try:'),
    raw.replace('\nFAILURE:', '\nERROR: dependency verification failed for an additional reason\nFAILURE:'),
    raw.replace('> Task :app:preBuild', 'Unfamiliar prelude context\n> Task :app:preBuild'),
    raw.replace('> Task :app:preBuild', 'WARNING: additional toolchain problem\n> Task :app:preBuild'),
    raw.replace('> Task :app:preBuild', '> Task :app:processDebugResources FAILED\n> Task :app:preBuild'),
    raw.replace('  symbol:   method setMaxWidth(int)', 'unrecognized unindented compiler detail'),
    raw.replaceAll('                ^', '                (caret missing)'),
    raw.replaceAll('/org/openclaw/trial/BirdView.java:', '/other/package/BirdView.java:'),
  ];
  for (const summary of unknown) assert.equal((await presentedBuild(summary)).record.summary, summary);
  assert.equal((await presentedBuild(raw, {}, 'status')).record.summary, raw);
  assert.equal((await presentedBuild(raw, { ok: true })).record.summary, raw);
});

test('hostile and oversized compiler context is never executed, silently shortened or hidden', async () => {
  const hostile = 'MainActivity.java:1: error: cannot find symbol\n    "; globalThis.androidFeedbackExecuted = true; // </script> ```\n    ^\n  symbol: method missing()';
  const { result, record } = await presentedBuild(javaFailure([hostile]));
  assert.ok(record.summary.includes(hostile));
  assert.equal(globalThis.androidFeedbackExecuted, undefined);
  assert.equal(result.content.length, 1);
  for (const block of [
    'MainActivity.java:1: error: cannot find symbol\n    ' + 'x'.repeat(1300) + '\n    ^',
    'MainActivity.java:1: error: cannot find symbol\n    missing();\n    ^\n' + '  additional compiler context\n'.repeat(13),
    'MainActivity.java:1: error: cannot find symbol\n    missing();\n    ^\n  symbol: \u001b[31mmissing()',
  ]) {
    const raw = javaFailure([block]);
    assert.equal((await presentedBuild(raw)).record.summary, raw);
  }
  let calls = 0;
  const tool = createTool(CONTEXT, { client: async payload => { calls++; return payload.params.action === 'stop' ? response() : response({ ok: false, summary: 'x'.repeat(16001) }); } });
  await assert.rejects(tool.execute('call_oversize', { action: 'build', jobId: JOB }), /receipt is incomplete.*acknowledged the stop/);
  assert.equal(calls, 2);
});

test('details stay bounded while unexpected nonerror metadata remains in the single receipt', async () => {
  const extra = { status: 'x'.repeat(65), reportPath: 'x'.repeat(1025), reportStatus: { unexpected: true }, future: 'retained metadata' };
  const { record, result } = await presentedBuild('Unrecognized failure remains complete.', extra);
  for (const [key, value] of Object.entries(extra)) assert.deepEqual(record[key], value);
  assert.deepEqual(result.details, { ok: false, jobId: JOB });
});

test('human report is a separate Markdown block before its technical receipt without JSON duplication', async () => {
  const reportText = '# Android progress\n\nThe app has not been built yet.\n\n- Build: NOT RUN\n';
  const receipt = response({ summary: 'The retained report is available.', reportStatus: 'available', reportText });
  const tool = createTool(CONTEXT, { client: async () => receipt });
  const result = await tool.execute('call_report', { action: 'status', jobId: JOB });
  assert.deepEqual(result.content[0], { type: 'text', text: reportText });
  assert.equal(result.content[0].text.includes('\\n'), false);
  const record = JSON.parse(result.content[1].text);
  assert.equal(Object.hasOwn(record, 'reportText'), false);
  assert.equal(record.reportStatus, 'available');
  assert.equal(result.content.filter(block => block.text.includes('The app has not been built yet.')).length, 1);
  assert.equal(Object.hasOwn(result.details, 'reportText'), false);
  assert.equal(Object.hasOwn(result.details, 'summary'), false);
  assert.equal(result.content.length, 2);
});

test('report content must be bounded UTF-8 text with an available status before delivery', async () => {
  for (const patch of [
    { reportText: 'é'.repeat(65537), reportStatus: 'available' },
    { reportText: 42, reportStatus: 'available' },
    { reportText: 'unverified', reportStatus: 'unavailable' },
    { reportText: 'unverified' },
  ]) {
    let imageReads = 0;
    const tool = createTool(CONTEXT, {
      client: async () => response({ ...patch, images: [{ path: '/unread.png' }] }),
      readImage: async () => { imageReads++; return {}; },
    });
    await assert.rejects(tool.execute('call_invalid_report', { action: 'status', jobId: JOB }), /report must be available text within 128 KiB/);
    assert.equal(imageReads, 0);
  }
  const boundary = 'é'.repeat(65536);
  const tool = createTool(CONTEXT, { client: async () => response({ reportText: boundary, reportStatus: 'available' }) });
  assert.equal((await tool.execute('call_report_boundary', { action: 'status', jobId: JOB })).content[0].text, boundary);
});

test('response identity substitution is rejected before image delivery', async () => {
  let imageRead = false;
  const client = async payload => payload.params.action === 'stop' ? response() : response({ jobId: OTHER_JOB, images: [{ path: '/arbitrary.png' }] });
  const tool = createTool(CONTEXT, { client, readImage: async () => { imageRead = true; return {}; } });
  await assert.rejects(tool.execute('call_wrongjob', { action: 'observe', jobId: JOB }), /another job/);
  assert.equal(imageRead, false);
});

test('native image content is returned only through the validated loader', async () => {
  let loaded;
  const image = { type: 'image', data: png.toString('base64'), mimeType: 'image/png' };
  const tool = createTool(CONTEXT, {
    client: async () => response({ summary: 'Captured the current screen.', images: [{ path: '/validated/screen.png' }] }),
    readImage: async (filename, jobId) => { loaded = { filename, jobId }; return image; },
  });
  const result = await tool.execute('call_image', { action: 'observe', jobId: JOB });
  assert.deepEqual(loaded, { filename: '/validated/screen.png', jobId: JOB });
  assert.deepEqual(result.content[1], image);
  assert.match(result.content[0].text, new RegExp(JOB));
  assert.deepEqual(JSON.parse(result.content[0].text).images, [{ path: '/validated/screen.png' }]);
  assert.equal(Object.hasOwn(result.details, 'images'), false);
  assert.equal(result.content.filter(block => block.type === 'image').length, 1);
});

test('screenshot loader rejects path escape, symlinks, hardlinks, excessive size and non-PNG data', async t => {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'android-plugin-test-')));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const jobsRoot = path.join(root, 'jobs');
  const jobRoot = path.join(jobsRoot, JOB);
  await fs.mkdir(jobRoot, { recursive: true });
  const filename = path.join(jobRoot, 'screen.png');
  await fs.writeFile(filename, png);
  const options = { jobsRoot };
  assert.equal((await loadPng(filename, JOB, options)).mimeType, 'image/png');
  const outside = path.join(root, 'outside.png'); await fs.writeFile(outside, png);
  await assert.rejects(loadPng(outside, JOB, options), /outside its job/);
  await assert.rejects(loadPng(filename, OTHER_JOB, options), /outside its job/);
  const linked = path.join(jobRoot, 'linked.png'); await fs.symlink(outside, linked);
  await assert.rejects(loadPng(linked, JOB, options), /symlink/);
  const directoryLink = path.join(jobRoot, 'linked-dir'); await fs.symlink(root, directoryLink);
  await assert.rejects(loadPng(path.join(directoryLink, 'outside.png'), JOB, options), /symlink/);
  const hardlink = path.join(jobRoot, 'hard.png'); await fs.link(outside, hardlink);
  await assert.rejects(loadPng(hardlink, JOB, options), /bounded regular PNG/);
  const huge = path.join(jobRoot, 'huge.png'); await fs.writeFile(huge, png); await fs.truncate(huge, 4 * 1024 * 1024 + 1);
  await assert.rejects(loadPng(huge, JOB, options), /bounded regular PNG/);
  const invalid = path.join(jobRoot, 'bad.png'); await fs.writeFile(invalid, 'not a PNG image');
  await assert.rejects(loadPng(invalid, JOB, options), /not a PNG/);
});

test('controller command and environment are fixed; model content travels only on stdin', async () => {
  const fake = fakeSpawn(call => { call.child.stdout.end(JSON.stringify(response())); call.child.emit('close', 0); });
  const payload = { actor: 'trusted|identity', requestId: 'call_client', params: { action: 'write_sources', jobId: JOB, files: [{ name: 'A.java', content: '$(echo secret); `touch /tmp/bad`' }] } };
  const receipt = await runClient(payload, { spawnProcess: fake.spawnProcess });
  assert.equal(receipt.ok, true);
  const [command, args, options] = fake.calls[0].args;
  assert.equal(command, '/usr/bin/python3'); assert.deepEqual(args, ['-B', CONTROLLER, 'client']);
  assert.equal(options.shell, false);
  assert.deepEqual(Object.keys(options.env).sort(), ['LANG', 'PATH']);
  assert.deepEqual(JSON.parse(Buffer.concat(fake.calls[0].input)), payload);
});

test('client rejects malformed, non-UTF8 and nonzero-exit replies', async () => {
  for (const [output, code] of [[Buffer.from('bad'), 0], [Buffer.from([123, 34, 97, 34, 58, 34, 255, 34, 125]), 0], [Buffer.from('{}'), 2]]) {
    const fake = fakeSpawn(call => { call.child.stdout.end(output); call.child.emit('close', code); });
    await assert.rejects(runClient({}, { spawnProcess: fake.spawnProcess }), /invalid JSON|failed/);
  }
});

test('failed client exposes only its well-formed controller summary and never stderr', async () => {
  const summary = 'unfinished Android job requires operator reconciliation';
  const fake = fakeSpawn(call => {
    call.child.stderr.end('SECRET_TOKEN=not-for-model\nTraceback: private host details');
    call.child.stdout.end(JSON.stringify({ ok: false, summary }));
    call.child.emit('close', 1);
  });
  await assert.rejects(runClient({}, { spawnProcess: fake.spawnProcess }), error => {
    assert.equal(error.message, `Android controller reported: ${summary}`);
    assert.deepEqual(Object.keys(error), []);
    return true;
  });
  assert.deepEqual(fake.calls[0].signals, []);
});

test('nonzero client rejects unexpected envelopes and hides malformed stdout and stderr', async () => {
  const secret = 'SECRET_NOT_A_DIAGNOSTIC';
  const malformed = [
    Buffer.from(secret),
    Buffer.from(JSON.stringify({ ok: true, summary: secret })),
    Buffer.from(JSON.stringify({ ok: false, summary: secret, images: [{ path: '/outside/secret.png' }] })),
    Buffer.from(JSON.stringify({ ok: false, summary: secret, reportText: secret })),
    Buffer.from(JSON.stringify({ ok: false, summary: secret, permissions: 'host-shell' })),
    Buffer.from(JSON.stringify({ ok: false, summary: { text: secret } })),
    Buffer.from(JSON.stringify([{ ok: false, summary: secret }])),
    Buffer.from('null'),
    Buffer.from('{"ok":false,"summary":"' + secret + '","__proto__":{}}'),
    Buffer.from('{"ok":false,"summary":"' + secret + '"}\n{"ok":true}'),
    Buffer.from([123, 34, 111, 107, 34, 58, 102, 97, 108, 115, 101, 44, 34, 115, 117, 109, 109, 97, 114, 121, 34, 58, 34, 255, 34, 125]),
  ];
  for (const output of malformed) {
    const fake = fakeSpawn(call => {
      call.child.stderr.end(secret);
      call.child.stdout.end(output);
      call.child.emit('close', 2);
    });
    await assert.rejects(runClient({}, { spawnProcess: fake.spawnProcess }), error => {
      assert.equal(error.message, 'Android controller client failed (exit 2).');
      return true;
    });
  }
});

test('controller diagnostic is bounded by UTF-8 bytes and rejects empty or control text', async () => {
  for (const [summary, valid] of [
    ['é'.repeat(500), true], ['🙂'.repeat(250), true],
    ['é'.repeat(501), false], [' ', false], ['', false],
    ['hidden\nline', false], ['hidden\0text', false], ['hidden\u001b[31m', false],
    ['hidden\u2028line', false], ['unpaired\ud800', false],
  ]) {
    const fake = fakeSpawn(call => { call.child.stdout.end(JSON.stringify({ ok: false, summary })); call.child.emit('close', 1); });
    await assert.rejects(runClient({}, { spawnProcess: fake.spawnProcess }), error => {
      assert.equal(error.message, valid ? `Android controller reported: ${summary}` : 'Android controller client failed (exit 1).');
      return true;
    });
  }
});

test('reported controller failure still sends one actor-bound stop and cannot deliver images', async () => {
  const payloads = [];
  let imageReads = 0;
  const fake = fakeSpawn(call => {
    const payload = JSON.parse(Buffer.concat(call.input)); payloads.push(payload);
    if (payload.params.action === 'prepare') {
      call.child.stderr.end('private startup trace');
      call.child.stdout.end(JSON.stringify({ ok: false, summary: 'unfinished Android job requires operator reconciliation' }));
      call.child.emit('close', 1);
    } else {
      assert.equal(payload.params.action, 'stop');
      call.child.stdout.end(JSON.stringify({ ok: true, summary: 'No Android job for this session.', status: 'none' }));
      call.child.emit('close', 0);
    }
  });
  const tool = createTool(CONTEXT, {
    client: (payload, options) => runClient(payload, { ...options, spawnProcess: fake.spawnProcess }),
    readImage: async () => { imageReads++; assert.fail('error output cannot supply image authority'); },
  });
  await assert.rejects(tool.execute('call_admission', { action: 'prepare' }), error => {
    assert.equal(error.message, 'Android controller reported: unfinished Android job requires operator reconciliation The controller acknowledged the stop request.');
    return true;
  });
  assert.deepEqual(payloads.map(item => item.params.action), ['prepare', 'stop']);
  assert.equal(payloads[1].actor, CONTEXT.sessionKey + '|' + CONTEXT.sessionId);
  assert.equal(payloads[1].requestId, 'call_admission:abort');
  assert.equal(Object.hasOwn(payloads[1].params, 'jobId'), false);
  assert.equal(imageReads, 0);
});

test('client rejects oversized output and joins the terminated client', async () => {
  const fake = fakeSpawn(call => call.child.stdout.write(Buffer.alloc(1024 * 1024 + 1)));
  await assert.rejects(runClient({}, { spawnProcess: fake.spawnProcess }), /size limit/);
  assert.deepEqual(fake.calls[0].signals, ['SIGTERM']);
});

test('client deadline and abort terminate its exact client without shell or group kills', async () => {
  const timeoutFake = fakeSpawn(() => {});
  await assert.rejects(runClient({}, { spawnProcess: timeoutFake.spawnProcess, timeoutMs: 10 }), /time limit/);
  assert.deepEqual(timeoutFake.calls[0].signals, ['SIGTERM']);
  const abort = new AbortController();
  const abortFake = fakeSpawn(() => abort.abort());
  await assert.rejects(runClient({}, { spawnProcess: abortFake.spawnProcess, signal: abort.signal }), { name: 'AbortError' });
  assert.deepEqual(abortFake.calls[0].signals, ['SIGTERM']);
});
