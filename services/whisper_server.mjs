// 常驻本地 whisper 服务（复用 dsh-ears 的 whisper.cpp 运行时）
// 用法: node whisper_server.mjs <transcribeLibPath> <modelPath> <lang>
// 启动后输出一行 "READY <port>"，之后提供：
//   GET  /health
//   POST /transcribe?lang=zh    body = 裸 PCM16(16kHz 单声道小端)
// 模型只加载一次常驻显存/内存，后续每次识别仅做推理（与 dsh-ears 插件同路径）。

import http from 'node:http';
import { pathToFileURL } from 'node:url';

const [, , libPath, modelPath, langArg, parentPidArg] = process.argv;
const defaultLang = langArg || 'zh';
const parentPid = Number(parentPidArg || 0);

// Parent-owned pipe closes even when the parent is terminated without atexit.
// This avoids Windows permission/PID-namespace differences in process.kill(pid, 0).
if (parentPid > 0) {
  process.stdin.resume();
  process.stdin.once('end', () => process.exit(0));
}

function logErr(err) {
  try {
    process.stderr.write('[whisper_server] ' + String((err && err.stack) || err) + '\n');
  } catch (_) {}
}

let transcriber = null;

async function boot() {
  if (!libPath || !modelPath) throw new Error('缺少参数：transcribe 库或模型路径');
  const mod = await import(pathToFileURL(libPath).href);
  const Transcriber = mod.Transcriber;
  const resolveConfig = mod.resolveConfig;
  if (typeof Transcriber !== 'function') throw new Error('dsh-ears 转写库未导出 Transcriber');

  const env = { ...process.env, DSH_EARS_MODEL: modelPath, DSH_EARS_LANG: defaultLang };
  const pluginDir = libPath.replace(/[\\/]lib[\\/][^\\/]+$/, '');
  const config = typeof resolveConfig === 'function'
    ? resolveConfig(env, pluginDir)
    : { modelPath, language: defaultLang, threads: 4, useGpu: true };

  transcriber = new Transcriber(config);
  if (typeof transcriber.load === 'function') await transcriber.load();
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function sendJson(res, status, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': body.length });
  res.end(body);
}

await boot();

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url || '/', 'http://127.0.0.1');

    if (req.method === 'GET' && url.pathname === '/health') {
      sendJson(res, 200, { ok: true, ready: Boolean(transcriber), model: modelPath, language: defaultLang });
      return;
    }

    if (req.method === 'POST' && url.pathname === '/transcribe') {
      const pcm = await readBody(req);
      if (!pcm || pcm.length < 3200) {
        sendJson(res, 400, { ok: false, error: '音频太短（PCM16 少于 0.1 秒）' });
        return;
      }
      const language = url.searchParams.get('lang') || defaultLang;
      const startedAt = Date.now();
      const result = await transcriber.transcribe(pcm, { kind: 'pcm16', language });
      let text = '';
      if (typeof result === 'string') text = result;
      else if (result && typeof result.text === 'string') text = result.text;
      else if (Array.isArray(result?.segments)) text = result.segments.map((s) => (s && s.text) || '').join('');
      sendJson(res, 200, {
        ok: true,
        text: String(text || '').trim(),
        elapsedMs: Date.now() - startedAt,
        durationSeconds: Number((pcm.length / 2 / 16000).toFixed(2)),
      });
      return;
    }

    sendJson(res, 404, { ok: false, error: 'not found' });
  } catch (err) {
    logErr(err);
    try {
      sendJson(res, 500, { ok: false, error: String((err && err.message) || err) });
    } catch (_) {}
  }
});

server.on('error', logErr);
server.listen(0, '127.0.0.1', () => {
  const port = server.address().port;
  process.stdout.write('READY ' + port + '\n');
});

process.on('SIGTERM', () => process.exit(0));
process.on('SIGINT', () => process.exit(0));
process.on('uncaughtException', logErr);
process.on('unhandledRejection', logErr);
