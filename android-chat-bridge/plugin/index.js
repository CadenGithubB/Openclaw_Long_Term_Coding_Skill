import { spawn } from 'node:child_process';
import { constants, promises as fs } from 'node:fs';
import path from 'node:path';

export const CONTROLLER = '/CONFIGURE/android-chat-bridge/bridge.py';
export const JOBS_ROOT = '/CONFIGURE/android-chat-bridge/jobs';
export const CLIENT_TIMEOUT_MS = 420_000;
const OUTPUT_LIMIT = 1024 * 1024;
const PNG_LIMIT = 4 * 1024 * 1024;
const REPORT_LIMIT = 128 * 1024;
const CLIENT_ERROR_LIMIT = 1000;
const JOB_ID = /^am-[a-f0-9]{32}$/;
const ACTIONS = ['prepare', 'write_sources', 'build', 'start_test', 'tap', 'observe', 'extend', 'status', 'stop'];
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

export const PARAMETERS = {
  type: 'object', additionalProperties: false, required: ['action'],
  properties: {
    action: { type: 'string', enum: ACTIONS },
    jobId: { type: 'string', pattern: '^am-[a-f0-9]{32}$', description: 'Reuse the exact jobId returned by prepare. Required on every write_sources, build, start_test, tap, observe and extend call.' },
    reason: { type: 'string', maxLength: 1000 },
    timeLimitMinutes: { type: 'integer', minimum: 5, maximum: 60, description: 'prepare only: choose the controller time budget on the first preparation of a new job. Omit for the new-job default of 60 minutes. Repeating prepare cannot extend an existing job; nothing here extends the separate chat deadline.' },
    extendMinutes: { type: 'integer', minimum: 5, maximum: 30, description: 'extend only: request 5 to 30 more controller minutes. Granted only after a new successful build since the previous extension, at most 3 times and 120 minutes in total. Requires a reason naming the remaining work. Does not add source writes, builds, actions or chat time.' },
    files: { type: 'array', description: 'Java class source files only. Construct the UI programmatically in Java; XML layouts, custom resources, dependency files and build scripts are not accepted.', minItems: 1, maxItems: 16, items: {
      type: 'object', additionalProperties: false, required: ['name', 'content'],
      properties: {
        name: { type: 'string', description: 'A simple Java class basename such as MainActivity.java or BirdView.java. Start with an uppercase letter. Do not include directories, paths or XML/resource filenames.', pattern: '^[A-Z][A-Za-z0-9_]{0,63}\\.java$' },
        content: { type: 'string', description: 'The complete original Java class source, with package org.openclaw.trial;. Build all views and graphics programmatically using Android SDK APIs. Do not reference custom R.layout/R.id resources, XML layouts or external dependencies.', minLength: 1, maxLength: 131072 },
      },
    } },
    x: { type: 'integer', minimum: 0, maximum: 8192, description: 'tap only: horizontal position in actual pixels of the observed display reported by start_test/observe, not a scaled range. For a 480x800 display use 0 <= x < 480; prefer the center of a control from uiSummary.' },
    y: { type: 'integer', minimum: 0, maximum: 8192, description: 'tap only: vertical position in actual pixels of the observed display reported by start_test/observe, not a scaled range. For a 480x800 display use 0 <= y < 800; prefer the center of a control from uiSummary.' },
    count: { type: 'integer', minimum: 1, maximum: 30 },
    intervalMs: { type: 'integer', minimum: 80, maximum: 1500 },
  },
};

const ACTION_FIELDS = {
  prepare: ['action', 'reason', 'timeLimitMinutes'],
  write_sources: ['action', 'jobId', 'reason', 'files'],
  build: ['action', 'jobId', 'reason'],
  start_test: ['action', 'jobId', 'reason'],
  tap: ['action', 'jobId', 'reason', 'x', 'y', 'count', 'intervalMs'],
  observe: ['action', 'jobId', 'reason'],
  extend: ['action', 'jobId', 'reason', 'extendMinutes'],
  status: ['action', 'jobId', 'reason'],
  stop: ['action', 'jobId', 'reason'],
};

function invalid(message) { throw new Error(`Android request refused: ${message}`); }

// Name a rejected field so the envelope can be corrected; echo only identifiers.
function fieldName(key) {
  return typeof key === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,39}$/.test(key) ? `"${key}"` : 'with an unsupported name';
}

function plainData(value, permitted, label, hint = permitted) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
      || ![Object.prototype, null].includes(Object.getPrototypeOf(value))) invalid(`${label} must be an object`);
  for (const key of Reflect.ownKeys(value)) {
    if (typeof key !== 'string' || !permitted.includes(key)) invalid(`unknown ${label} field ${fieldName(key)}; use only ${hint.join(', ')}`);
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !Object.hasOwn(descriptor, 'value')) invalid(`${label} accessors are forbidden`);
  }
}

export function validateParams(value) {
  // Read the descriptor, never a getter, before plainData has refused accessors.
  const action = value && typeof value === 'object' ? Object.getOwnPropertyDescriptor(value, 'action')?.value : undefined;
  const hint = typeof action === 'string' && Object.hasOwn(ACTION_FIELDS, action) ? ACTION_FIELDS[action] : Object.keys(PARAMETERS.properties);
  plainData(value, Object.keys(PARAMETERS.properties), 'request', hint);
  if (!ACTIONS.includes(value.action)) invalid('unsupported action');
  const permitted = ACTION_FIELDS[value.action];
  const extra = Object.keys(value).find(key => !permitted.includes(key));
  if (extra !== undefined) invalid(`field does not apply to this action: ${fieldName(extra)}; ${value.action} accepts ${permitted.join(', ')}`);
  if (Object.hasOwn(value, 'jobId') && (typeof value.jobId !== 'string' || !JOB_ID.test(value.jobId))) invalid('invalid jobId');
  if (!['prepare', 'status', 'stop'].includes(value.action) && !Object.hasOwn(value, 'jobId')) invalid('jobId is required');
  if (Object.hasOwn(value, 'reason') && (typeof value.reason !== 'string' || value.reason.length > 1000)) invalid('reason is too long or is not text');
  if (Object.hasOwn(value, 'timeLimitMinutes') && (!Number.isInteger(value.timeLimitMinutes)
      || value.timeLimitMinutes < 5 || value.timeLimitMinutes > 60)) invalid('timeLimitMinutes must be an integer from 5 to 60, chosen only on the first prepare');
  if (value.action === 'extend') {
    if (!Number.isInteger(value.extendMinutes) || value.extendMinutes < 5 || value.extendMinutes > 30) invalid('extendMinutes must be an integer from 5 to 30');
    if (typeof value.reason !== 'string' || !value.reason.trim()) invalid('extend requires a reason naming the remaining work');
  }
  if (value.action === 'write_sources') {
    if (!Array.isArray(value.files) || value.files.length < 1 || value.files.length > 16) invalid('provide 1 to 16 Java files');
    let total = 0;
    const names = new Set();
    for (const file of value.files) {
      plainData(file, ['name', 'content'], 'file');
      if (typeof file.name !== 'string' || !/^[A-Z][A-Za-z0-9_]{0,63}\.java$/.test(file.name)) invalid('use a simple Java class filename such as MainActivity.java; basename only, no directories or XML/resource files');
      if (names.has(file.name)) invalid('duplicate Java filename');
      names.add(file.name);
      if (typeof file.content !== 'string' || Buffer.byteLength(file.content, 'utf8') > 128 * 1024) invalid('Java file exceeds 128 KiB');
      if (file.content.length === 0 || file.content.includes('\0')) invalid('Java source must be nonempty text without NUL characters');
      total += Buffer.byteLength(file.content, 'utf8');
      if (total > 512 * 1024) invalid('Java source batch exceeds 512 KiB');
    }
  }
  if (value.action === 'tap') {
    for (const axis of ['x', 'y']) if (!Number.isSafeInteger(value[axis]) || value[axis] < 0 || value[axis] > 8192) invalid('tap coordinates must be integers from 0 to 8192');
    if (Object.hasOwn(value, 'count') && (!Number.isInteger(value.count) || value.count < 1 || value.count > 30)) invalid('tap count must be 1 to 30');
    if (Object.hasOwn(value, 'intervalMs') && (!Number.isInteger(value.intervalMs) || value.intervalMs < 80 || value.intervalMs > 1500)) invalid('tap interval must be 80 to 1500 ms');
    if (((value.count ?? 1) - 1) * (value.intervalMs ?? 300) > 15000) invalid('tap burst exceeds 15000 ms');
  }
  // Capture the validated values before any await; caller mutation grants no new input.
  return JSON.parse(JSON.stringify(value));
}

function identity(ctx) {
  if (ctx?.agentId !== 'main') return null;
  const valid = value => typeof value === 'string' && value.length > 0 && value.length <= 512 && !/[\x00-\x20\x7f|]/.test(value);
  if (!valid(ctx.sessionKey) || !valid(ctx.sessionId)) return null;
  return `${ctx.sessionKey}|${ctx.sessionId}`;
}

function abortError(message = 'Android action cancelled; controller cleanup was requested.') {
  const error = new Error(message); error.name = 'AbortError'; return error;
}

function allowedModel(model) {
  if (!model || typeof model !== 'object') return false;
  if (model.provider !== undefined && model.provider !== 'ollama') return false;
  if (model.modelId !== undefined && model.modelId !== 'qwen3.6:35b') return false;
  if (model.modelRef !== undefined && model.modelRef !== 'ollama/qwen3.6:35b') return false;
  return (model.provider === 'ollama' && model.modelId === 'qwen3.6:35b')
    || model.modelRef === 'ollama/qwen3.6:35b';
}

function clientFailure(code, output) {
  const generic = new Error(`Android controller client failed (exit ${code ?? 'unknown'}).`);
  if (!Number.isInteger(code) || code === 0) return generic;
  try {
    const result = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(output));
    // A failed client can report only its bounded diagnostic, never a receipt,
    // image path, report, authority claim or stderr supplied alongside it.
    if (!result || typeof result !== 'object' || Array.isArray(result)
        || Object.keys(result).length !== 2 || !Object.hasOwn(result, 'ok')
        || !Object.hasOwn(result, 'summary') || result.ok !== false
        || typeof result.summary !== 'string' || !result.summary.trim()
        || Buffer.byteLength(result.summary, 'utf8') > CLIENT_ERROR_LIMIT
        || /[\u0000-\u001f\u007f-\u009f\u2028\u2029\ud800-\udfff]/u.test(result.summary)) return generic;
    return new Error(`Android controller reported: ${result.summary}`);
  } catch { return generic; }
}

// The only production subprocess command is this fixed controller client. No shell,
// model-supplied path, inherited credentials, or generated app code is executed here.
export function runClient(payload, { signal, timeoutMs = CLIENT_TIMEOUT_MS, spawnProcess = spawn } = {}) {
  if (signal?.aborted) return Promise.reject(abortError());
  return new Promise((resolve, reject) => {
    let child, timer, killTimer, joinTimer, failure, settled = false;
    const output = [];
    let outputBytes = 0, diagnosticBytes = 0;
    const finish = (error, result) => {
      if (settled) return; settled = true;
      clearTimeout(timer); clearTimeout(killTimer); clearTimeout(joinTimer); signal?.removeEventListener('abort', cancel);
      if (error) reject(error); else resolve(result);
    };
    const terminate = error => {
      if (settled || failure) return;
      failure = error;
      child?.stdin?.destroy();
      if (child) {
        child.kill('SIGTERM');
        killTimer = setTimeout(() => {
          child.kill('SIGKILL');
          joinTimer = setTimeout(() => finish(new Error('Android controller client termination is unconfirmed.')), 2000);
        }, 2000);
      }
    };
    const cancel = () => terminate(abortError());
    try {
      child = spawnProcess('/usr/bin/python3', ['-B', CONTROLLER, 'client'], {
        shell: false, stdio: ['pipe', 'pipe', 'pipe'],
        env: { PATH: '/usr/bin:/bin:/usr/sbin:/sbin', LANG: 'en_US.UTF-8' },
      });
      child.stdout.on('data', data => {
        outputBytes += data.length;
        if (outputBytes > OUTPUT_LIMIT) terminate(new Error('Android controller response exceeded its size limit.'));
        else output.push(Buffer.from(data));
      });
      // Consume diagnostics without exposing arbitrary stderr as a trusted receipt.
      child.stderr.on('data', data => {
        diagnosticBytes += data.length;
        if (diagnosticBytes > OUTPUT_LIMIT) terminate(new Error('Android controller diagnostics exceeded their size limit.'));
      });
      child.stdin.on('error', () => {});
      child.on('error', error => finish(new Error(`Android controller client could not start (${error.code ?? 'unknown'}).`)));
      child.on('close', code => {
        if (failure) return finish(failure);
        if (code !== 0) return finish(clientFailure(code, Buffer.concat(output)));
        try { finish(null, JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(output)))); }
        catch { finish(new Error('Android controller returned an invalid JSON receipt.')); }
      });
      signal?.addEventListener('abort', cancel, { once: true });
      timer = setTimeout(() => terminate(new Error('Android controller action exceeded its time limit.')), timeoutMs);
      if (signal?.aborted) cancel();
      else child.stdin.end(JSON.stringify(payload) + '\n');
    } catch (error) { finish(error); }
  });
}

export async function loadPng(filename, jobId, { jobsRoot = JOBS_ROOT } = {}) {
  if (!JOB_ID.test(jobId) || typeof filename !== 'string' || !path.isAbsolute(filename)) invalid('invalid screenshot identity');
  const expectedRoot = path.join(jobsRoot, jobId);
  if (path.resolve(filename) !== filename || !filename.startsWith(expectedRoot + path.sep)) invalid('screenshot lies outside its job');
  if (await fs.realpath(jobsRoot) !== jobsRoot || await fs.realpath(filename) !== filename) invalid('screenshot path uses a symlink');
  const relative = path.relative(jobsRoot, filename).split(path.sep);
  let current = jobsRoot;
  for (const part of relative) {
    current = path.join(current, part);
    if ((await fs.lstat(current)).isSymbolicLink()) invalid('screenshot path uses a symlink');
  }
  const handle = await fs.open(filename, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.nlink !== 1 || before.size < 8 || before.size > PNG_LIMIT) invalid('screenshot is not a bounded regular PNG');
    const data = Buffer.alloc(before.size);
    let offset = 0;
    while (offset < data.length) {
      const { bytesRead } = await handle.read(data, offset, data.length - offset, offset);
      if (!bytesRead) invalid('screenshot was truncated');
      offset += bytesRead;
    }
    const after = await handle.stat();
    if (before.size !== after.size || before.mtimeMs !== after.mtimeMs || before.ctimeMs !== after.ctimeMs
        || await fs.realpath(filename) !== filename) invalid('screenshot changed during capture');
    if (!data.subarray(0, 8).equals(PNG_SIGNATURE)) invalid('screenshot is not a PNG');
    return { type: 'image', data: data.toString('base64'), mimeType: 'image/png' };
  } finally { await handle.close(); }
}

function validateReceipt(result) {
  if (!result || typeof result !== 'object' || Array.isArray(result) || typeof result.ok !== 'boolean'
      || typeof result.summary !== 'string' || result.summary.length > 16000) invalid('controller receipt is incomplete');
  if (result.jobId !== undefined && (typeof result.jobId !== 'string' || !JOB_ID.test(result.jobId))) invalid('controller returned an invalid jobId');
  if (result.images !== undefined && (!Array.isArray(result.images) || result.images.length > 8)) invalid('controller returned too many images');
  if (Object.hasOwn(result, 'reportText') && (typeof result.reportText !== 'string'
      || Buffer.byteLength(result.reportText, 'utf8') > REPORT_LIMIT || result.reportStatus !== 'available')) {
    invalid('controller report must be available text within 128 KiB');
  }
  return result;
}

function compactJavaFailure(summary) {
  // Presentation only: the controller keeps the original receipt and build logs.
  // Recognize the pinned Gradle/Javac failure form conservatively. Unfamiliar,
  // incomplete or oversized compiler context remains available verbatim instead.
  if (!summary.startsWith('offline-build failed: ')
      || /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(summary)) return summary;
  const parts = summary.replaceAll('\r\n', '\n').split('\nFAILURE: Build failed with an exception.\n');
  if (parts.length !== 2) return summary;
  const heading = "\n* What went wrong:\nExecution failed for task ':app:compileDebugJavaWithJavac'.\n> Compilation failed; see the compiler output below.\n";
  if (!parts[1].startsWith(heading)) return summary;
  const sections = parts[1].slice(heading.length).split('\n* Try:\n');
  if (sections.length !== 2 || !/^> Check your code and dependencies to fix the compilation error\(s\)\n> Run with --scan to get full insights\.\n\nBUILD FAILED in [0-9][0-9a-z .]*\n?$/.test(sections[1])) return summary;
  const diagnosticStart = parts[0].search(/\n\/workspace\/project\/app\/src\/main\/java\/org\/openclaw\/trial\/[A-Z][A-Za-z0-9_]{0,63}\.java:[1-9][0-9]*: error:/);
  if (diagnosticStart < 0 || parts[0].slice(diagnosticStart + 1).trimEnd()
      !== sections[0].split('\n').map(line => line.startsWith('  ') ? line.slice(2) : line).join('\n').trimEnd()) return summary;
  // Only known boilerplate from this fixed offline toolchain may be omitted.
  // A second failure, unfamiliar warning, or other context falls back intact.
  const boilerplate = [
    /^To honour the JVM settings for this build a single-use Daemon process will be forked\. For more on this, please refer to https:\/\/docs\.gradle\.org\/8\.13\/userguide\/gradle_daemon\.html#sec:disabling_the_daemon in the Gradle documentation\.$/,
    /^Daemon will be stopped at the end of the build$/,
    /^Loading local repository\.\.\.$/,
    /^Info: SDK Manager found the following installed packages: build-tools;35\.0\.0 platforms;android-35$/,
    /^\[[= ]+\] [0-9]+% Loading local repository\.\.\.$/,
    /^WARNING: platform-tools package is not installed\.$/,
    /^> Task :app:[A-Za-z0-9]+(?: (?:UP-TO-DATE|NO-SOURCE|FROM-CACHE))?$/,
    /^> Task :app:compileDebugJavaWithJavac FAILED$/,
    /^\[Incubating\] Problems report is available at: file:\/\/\/workspace\/project\/build\/reports\/problems\/problems-report\.html$/,
    /^Deprecated Gradle features were used in this build, making it incompatible with Gradle 9\.0\.$/,
    /^You can use '--warning-mode all' to show the individual deprecation warnings and determine if they come from your own scripts or plugins\.$/,
    /^For more on this, please refer to https:\/\/docs\.gradle\.org\/8\.13\/userguide\/command_line_interface\.html#sec:command_line_warnings in the Gradle documentation\.$/,
    /^[0-9]+ actionable tasks?: [0-9]+ executed(?:, [0-9]+ (?:up-to-date|from cache))*$/,
    /^Picked up JAVA_TOOL_OPTIONS: -XX:ActiveProcessorCount=2 -Duser\.home=\/workspace\/\.android-runtime\/home -Djava\.io\.tmpdir=\/workspace\/\.android-runtime\/tmp$/,
    /^Warning: SDK processing\. This version only understands SDK XML versions up to 3 but an SDK XML file of version 4 was encountered\. This can happen if you use versions of Android Studio and the command-line tools that were released at different times\.$/,
  ];
  const prelude = parts[0].slice('offline-build failed: '.length, diagnosticStart).split(/[\r\n]/).map(line => line.trim()).filter(Boolean);
  if (prelude.some(line => !boilerplate.some(pattern => pattern.test(line)))) return summary;
  const blocks = new Set(), notes = new Set(), counts = [];
  let block = null, indent = '';
  const finish = () => {
    if (!block) return true;
    if (block.length < 3 || block.length > 12 || !/^[ \t]*\^[ \t]*$/.test(block[2])
        || block.slice(3).some(line => !/^[ \t]+\S/.test(line))
        || block.join('\n').length > 1200) return false;
    blocks.add(block.join('\n')); block = null;
    return true;
  };
  for (const line of sections[0].split('\n')) {
    const header = /^([ \t]*)\/workspace\/project\/app\/src\/main\/java\/org\/openclaw\/trial\/([A-Z][A-Za-z0-9_]{0,63}\.java):([1-9][0-9]{0,6}): error: (\S.*)$/.exec(line);
    const count = /^[ \t]*([1-9][0-9]{0,3}) errors?[ \t]*$/.exec(line);
    if (header || count || /^[ \t]*Note: /.test(line) || !line.trim()) {
      if (!finish()) return summary;
      if (header) {
        indent = header[1]; block = [`${header[2]}:${header[3]}: error: ${header[4]}`];
      } else if (count) counts.push(Number(count[1]));
      else if (line.trim()) notes.add(line.trim());
    } else {
      if (!block || !line.startsWith(indent)) return summary;
      block.push(line.slice(indent.length).trimEnd());
    }
  }
  if (!finish() || !blocks.size || counts.length !== 1 || counts[0] !== blocks.size) return summary;
  const shown = [...blocks].slice(0, 8);
  const selected = shown.length === blocks.size ? `${shown.length} distinct compiler blocks` : `the first ${shown.length} of ${blocks.size} distinct compiler blocks`;
  const warnings = prelude.filter(line => /^warning:/i.test(line));
  const context = [...notes, ...warnings];
  const compact = `offline-build failed: The Java compiler rejected the source with ${counts[0]} reported ${counts[0] === 1 ? 'error' : 'errors'}. Showing ${selected}. A source edit still needs a successful build before it is verified.\n\n${shown.join('\n\n')}${context.length ? '\n\n' + context.join('\n') : ''}\n\nFull build logs and the original controller receipt remain in this job's retained evidence; see reportPath.`;
  return compact.length < summary.length ? compact : summary;
}

function receiptDetails(receipt) {
  const details = { ok: receipt.ok };
  for (const [key, limit] of [['jobId', 35], ['status', 64], ['reportPath', 1024], ['reportStatus', 64]]) {
    if (typeof receipt[key] === 'string' && receipt[key].length <= limit) details[key] = receipt[key];
  }
  return details;
}

export function createTool(ctx, { client = runClient, readImage = loadPng } = {}) {
  // Catalog discovery can identify the agent before it has a concrete session.
  // Publish only the main-agent schema then; missing identity never authorizes execution.
  if (ctx?.agentId !== 'main') return null;
  const actor = identity(ctx);
  const model = ctx.activeModel ? { provider: ctx.activeModel.provider, modelId: ctx.activeModel.modelId, modelRef: ctx.activeModel.modelRef } : null;
  return {
    name: 'android_project', label: 'Offline Android project', executionMode: 'sequential',
    description: 'Create, build and test an original Android app through the prepared offline Java/SDK 35 worker and a separate private emulator. Before implementation, read the available long-task-runner skill and call prepare for the actual environment check. On the first prepare, optionally choose timeLimitMinutes (integer 5 to 60; new jobs default to 60). Repeating prepare cannot extend an existing job. If more time is needed after a new successful build, extend adds 5 to 30 minutes (at most 3 times, 120 minutes in total); it never extends the separate chat deadline. Retain its jobId, then use write_sources, build, start_test, tap and observe. write_sources accepts only Java class basenames such as MainActivity.java, with complete source in package org.openclaw.trial. Construct the UI programmatically using Android SDK APIs; no XML layouts, custom R.layout/R.id resources, external dependencies or pathnames. The controller owns the scaffold and build configuration. Returned screens are actual emulator captures; inspect them against the requested behavior and preserve incomplete checks. Observations include uiSummary: the visible text and controls with bounds and centers, captured separately after the screenshot. Tap x/y are actual pixels of the reported display, not a scaled range; a tap outside the display is refused without stopping the test. Use status to inspect and stop to retire the owned job. Explain each material action in plain language in reason. This tool cannot run host commands or download tools.',
    parameters: PARAMETERS,
    async execute(toolCallId, rawParams, signal) {
      if (!actor) invalid('missing or ambiguous trusted main-session identity');
      if (!allowedModel(model)) invalid('this tool requires the verified local ollama/qwen3.6:35b model');
      if (typeof toolCallId !== 'string' || !/^[A-Za-z0-9_.:-]{1,200}$/.test(toolCallId)) invalid('missing trusted tool call identity');
      const params = validateParams(rawParams);
      if (signal?.aborted) throw abortError('Android action was already cancelled; no controller action was sent.');
      let receipt;
      try {
        receipt = validateReceipt(await client({ actor, requestId: toolCallId, params }, { signal }));
        if (signal?.aborted) throw abortError();
        if (params.jobId && receipt.jobId && params.jobId !== receipt.jobId) invalid('controller returned another job');
        const content = [];
        const record = { ...receipt }; delete record.reportText;
        if (params.action === 'build' && !receipt.ok) record.summary = compactJavaFailure(receipt.summary);
        if (Object.hasOwn(receipt, 'reportText')) content.push({ type: 'text', text: receipt.reportText });
        content.push({ type: 'text', text: JSON.stringify(record, null, 2) });
        for (const image of receipt.images ?? []) {
          const jobId = params.jobId ?? receipt.jobId;
          if (!image || typeof image !== 'object' || !JOB_ID.test(jobId ?? '')) invalid('screenshot is not tied to this job');
          content.push(await readImage(image.path, jobId));
          if (signal?.aborted) throw abortError();
        }
        // AgentToolResult has content/details, not MCP's isError property. A
        // verified ok:false controller receipt remains available for a focused
        // repair; throwing here would incorrectly retire the writable job.
        return { content, details: receiptDetails(receipt) };
      } catch (error) {
        // An interrupted response is not evidence that its controller job stopped.
        // This second request is an idempotent cleanup action, never a work retry.
        if (signal?.aborted || !['status', 'stop'].includes(params.action)) {
          const cleanup = { action: 'stop', reason: 'The calling Android action was interrupted or its receipt could not be verified.' };
          if (params.jobId) cleanup.jobId = params.jobId;
          else if (receipt?.jobId && JOB_ID.test(receipt.jobId)) cleanup.jobId = receipt.jobId;
          let stopped = false;
          try {
            const result = validateReceipt(await client({ actor, requestId: `${toolCallId}:abort`, params: cleanup }, { timeoutMs: 30000 }));
            stopped = result.ok;
          } catch { /* The independent controller deadline still applies. */ }
          error.message += stopped ? ' The controller acknowledged the stop request.' : ' Controller cleanup is unconfirmed; inspect the retained job before retrying.';
        }
        throw error;
      }
    },
  };
}

export function register(api) {
  api.registerTool(ctx => createTool(ctx), { names: ['android_project'], optional: true });
}

export default {
  id: 'android-chat-bridge', name: 'Offline Android projects',
  description: 'Normal-chat access to a confined offline Android builder and private emulator.',
  register,
};
