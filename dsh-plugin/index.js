/**
 * Host half of the shortvideo bundle.
 *
 * Owns three things:
 *   1. the local editor process (`ui/preview.py` in the shortvideo repo) — started
 *      on demand, so a fresh Harness boot does not need a second terminal;
 *   2. a `/shortvideo/*` reverse proxy on this server, so the workbench is served
 *      from the same origin as the Harness window. This matters: the editor used
 *      to be reached at `http://<host>:8099`, which breaks whenever the Harness is
 *      opened through a tunnel or a different port mapping;
 *   3. one `/api/shortvideo` Fetch route plus a `shortvideo` tool, so the sidebar
 *      page and the agent drive the same code path.
 *
 * The proxy rewrites absolute paths inside text assets (`/api/`, `/files/`,
 * `/app/`, `/batch`) to the prefixed form. The editor is a zero-build app whose
 * URLs are all quoted literals, so a textual rewrite is enough and keeps the
 * editor code free of any knowledge about where it is mounted.
 */

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, openSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import http from 'node:http';

export const name = 'shortvideo';
export const inject = ['webServer', 'connection', 'tools'];

/** Same-origin mount point of the workbench. */
const PREFIX = '/shortvideo';
/** Host action route the sidebar calls. */
const ROUTE = '/api/shortvideo';
/** Actions shared by the route and the tool. */
const ACTIONS = ['status', 'start', 'stop', 'restart'];
/** Absolute paths the editor emits that must become prefix-relative. */
const REWRITE_PREFIXES = ['/api/', '/files/', '/app/', '/batch'];
/** Response types worth buffering for a rewrite; everything else streams. */
const TEXTUAL = /^(text\/|application\/(json|javascript|x-javascript)|image\/svg)/i;

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  });
}

/** Decode a body we intend to rewrite; unknown charsets are treated as UTF-8. */
function decode(buffer, contentType) {
  const charset = /charset=([\w-]+)/i.exec(contentType)?.[1]?.toLowerCase() ?? 'utf-8';
  try {
    return new TextDecoder(charset).decode(buffer);
  } catch {
    return buffer.toString('utf8');
  }
}

/** Point every absolute editor path at the mounted prefix. */
export function rewrite(body) {
  let out = body;
  for (const prefix of REWRITE_PREFIXES) {
    // Only rewrite when the path starts right after a quote or a parenthesis:
    // that covers href/src attributes, string literals and template strings
    // while leaving prose like "见 /api/ 文档" alone.
    const pattern = new RegExp(`([\\"'\`(])${prefix.replace(/\//g, '\\/')}`, 'g');
    out = out.replace(pattern, `$1${PREFIX}${prefix}`);
  }
  return out;
}

export function apply(ctx, config = {}) {
  const port = Number(config.port ?? 8099);
  const repo = String(config.repo ?? '/root/dsh-shortvideo');
  const home = process.env.DSH_HOME ?? join(homedir(), '.dsh');
  const logPath = join(home, 'shortvideo', 'server.log');
  const pythonOverride = typeof config.python === 'string' && config.python !== '' ? config.python : '';

  /** Spawned child, when this plugin owns the process. */
  let child = null;
  /** Resolved interpreter, cached after the first successful probe. */
  let python = '';

  const editorPath = join(repo, 'ui', 'preview.py');
  const installed = existsSync(editorPath);

  function candidates() {
    return [
      pythonOverride,
      process.env.SV_PY ?? '',
      join(repo, '.venv', 'bin', 'python'),
      join(dirname(repo), 'shortvideo-lab', '.venv', 'bin', 'python'),
      join(homedir(), 'shortvideo-lab', '.venv', 'bin', 'python'),
      'python3',
    ].filter((candidate) => candidate !== '');
  }

  /** First interpreter that can import the render dependencies. */
  function pickPython() {
    if (python !== '') return python;
    for (const candidate of candidates()) {
      const probeRun = spawnSync(candidate, ['-c', 'import PIL, imageio_ffmpeg'], { stdio: 'ignore', timeout: 15000 });
      if (probeRun.status === 0) {
        python = candidate;
        return python;
      }
    }
    return '';
  }

  /** True when the editor answers on its port. */
  function probe(timeout = 1200) {
    return new Promise((resolveProbe) => {
      const request = http.get({ host: '127.0.0.1', port, path: '/api/projects', timeout }, (response) => {
        response.resume();
        resolveProbe(response.statusCode === 200);
      });
      request.on('timeout', () => { request.destroy(); resolveProbe(false); });
      request.on('error', () => resolveProbe(false));
    });
  }

  async function waitUntilUp(ms = 12000) {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      if (await probe(600)) return true;
      await new Promise((resolveWait) => setTimeout(resolveWait, 300));
    }
    return false;
  }

  /** Start the editor if it is not already listening. */
  async function start() {
    if (await probe()) return { running: true, port, spawned: false };
    if (!installed) {
      return { running: false, port, error: `找不到编辑器：${editorPath}（在 config.repo 里指定仓库路径）` };
    }
    const interpreter = pickPython() || 'python3';
    mkdirSync(dirname(logPath), { recursive: true });
    const logFd = openSync(logPath, 'a');
    child = spawn(interpreter, [editorPath, '--port', String(port)], {
      cwd: repo,
      detached: true,
      stdio: ['ignore', logFd, logFd],
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
    });
    child.unref();
    const up = await waitUntilUp();
    return { running: up, port, spawned: true, pid: child.pid, python: interpreter, log: logPath,
      ...(up ? {} : { error: `启动 12 秒仍未响应，看日志：${logPath}` }) };
  }

  function stop() {
    if (child === null) return { running: false, stopped: false, reason: '这个进程不是插件启动的' };
    try {
      process.kill(-child.pid, 'SIGTERM');
    } catch {
      try { child.kill('SIGTERM'); } catch { /* already gone */ }
    }
    child = null;
    return { running: false, stopped: true };
  }

  async function status() {
    const running = await probe();
    return {
      running,
      installed,
      repo,
      port,
      url: `${PREFIX}/app/`,
      log: logPath,
      python: python !== '' ? python : (pythonOverride || '（未探测）'),
      ownedByPlugin: child !== null,
    };
  }

  async function run(action) {
    switch (action) {
      case 'status': return status();
      case 'start': return start();
      case 'stop': return stop();
      case 'restart': stop(); return start();
      default: throw new Error(`unhandled action: ${action}`);
    }
  }

  /** Proxy one request to the editor, rewriting textual responses. */
  function proxy(request, response) {
    const incoming = new URL(request.url ?? '/', 'http://127.0.0.1');
    let rest = incoming.pathname.slice(PREFIX.length);
    if (rest === '') rest = '/';
    const headers = { ...request.headers };
    delete headers.host;
    delete headers['accept-encoding'];      // keep bodies intact for rewriting

    const upstream = http.request({
      host: '127.0.0.1',
      port,
      method: request.method,
      path: `${rest}${incoming.search}`,
      headers,
    }, (answer) => {
      const contentType = String(answer.headers['content-type'] ?? '');
      const outHeaders = { ...answer.headers };
      delete outHeaders['content-length'];
      delete outHeaders['transfer-encoding'];
      const location = outHeaders.location;
      if (typeof location === 'string' && location.startsWith('/')) {
        outHeaders.location = `${PREFIX}${location}`;
      }

      if (!TEXTUAL.test(contentType)) {
        response.writeHead(answer.statusCode ?? 502, outHeaders);
        answer.pipe(response);
        return;
      }

      const chunks = [];
      answer.on('data', (chunk) => chunks.push(chunk));
      answer.on('end', () => {
        const body = rewrite(decode(Buffer.concat(chunks), contentType));
        const buffer = Buffer.from(body, 'utf8');
        outHeaders['content-length'] = String(buffer.length);
        response.writeHead(answer.statusCode ?? 200, outHeaders);
        response.end(buffer);
      });
    });

    upstream.on('error', (error) => {
      const payload = Buffer.from(JSON.stringify({
        error: `短视频服务不可用：${error.message}`,
        hint: '侧边栏里点「启动」，或检查端口是否被占用',
      }), 'utf8');
      response.writeHead(502, { 'content-type': 'application/json; charset=utf-8', 'content-length': String(payload.length) });
      response.end(payload);
    });

    request.pipe(upstream);
  }

  ctx.inject(['webServer'], (webCtx) => {
    webCtx.effect(() => webCtx.webServer.register({
      kind: 'prefix',
      path: PREFIX,
      handler: async (request, response) => {
        if (!(await probe())) {
          const started = await start();
          if (!started.running) {
            const payload = Buffer.from(JSON.stringify({ error: started.error ?? '短视频服务没起来', log: logPath }), 'utf8');
            response.writeHead(503, { 'content-type': 'application/json; charset=utf-8', 'content-length': String(payload.length) });
            response.end(payload);
            return;
          }
        }
        proxy(request, response);
      },
    }), 'shortvideo: proxy route');
  });

  ctx.effect(() => ctx.connection.fetch.register({
    path: ROUTE,
    methods: ['POST'],
    requestBody: 'buffered',
    fetch: async (request) => {
      let body;
      try {
        body = await request.json();
      } catch {
        return json({ error: '请求体不是 JSON' }, 400);
      }
      try {
        return json(await run(String(body?.action ?? 'status')));
      } catch (error) {
        ctx.logger?.warn?.(`shortvideo: action failed: ${error?.message ?? error}`);
        return json({ error: error?.message ?? String(error) }, 400);
      }
    },
  }), 'shortvideo: fetch route');

  ctx.effect(() => ctx.tools.register({
    name: 'shortvideo',
    description: '本地短视频工作台：查看/启动/停止剪辑台服务（DSH 侧边栏「短视频」里直接使用，无需记端口）。',
    parameters: {
      type: 'object',
      properties: {
        action: { type: 'string', enum: ACTIONS, description: 'status=看状态，start=启动剪辑台，stop=停止，restart=重启。' },
      },
      required: ['action'],
      additionalProperties: false,
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render(_args, value) {
        return [{ type: 'text', text: JSON.stringify(value, null, 2) }];
      },
    },
    async execute(args) {
      return run(String(args?.action ?? 'status'));
    },
  }), 'shortvideo: tool');

  ctx.logger?.info?.(`shortvideo: mount ${PREFIX} -> 127.0.0.1:${port} (repo ${repo})`);
}
