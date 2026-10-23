// 本地 whisper 转写桥（复用 dsh-ears 插件自带的 whisper.cpp 运行时）
// 用法: node whisper_bridge.mjs <transcribeLibPath> <modelPath> <lang>
// stdin: 裸 PCM16 小端、单声道、16kHz 音频
// stdout: 单行 JSON {"ok":true,"text":"..."} / {"ok":false,"error":"..."}

import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const [, , libPath, modelPath, langArg] = process.argv;

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks);
}

try {
  if (!libPath || !modelPath) {
    emit({ ok: false, error: '缺少参数：transcribe 库或模型路径' });
    process.exit(0);
  }
  const mod = await import(pathToFileURL(libPath).href);
  const Transcriber = mod.Transcriber;
  const resolveConfig = mod.resolveConfig;
  if (typeof Transcriber !== 'function') {
    emit({ ok: false, error: 'dsh-ears 转写库未导出 Transcriber' });
    process.exit(0);
  }

  const pcm = await readStdin();
  if (!pcm || pcm.length < 3200) {
    emit({ ok: false, error: '音频太短（PCM16 少于 0.1 秒）' });
    process.exit(0);
  }

  const env = { ...process.env, DSH_EARS_MODEL: modelPath, DSH_EARS_LANG: langArg || 'zh' };
  const config = typeof resolveConfig === 'function'
    ? resolveConfig(env, libPath.replace(/[\\/]lib[\\/][^\\/]+$/, ''))
    : { modelPath, language: langArg || 'zh', threads: 4, useGpu: true };

  const transcriber = new Transcriber(config);
  if (typeof transcriber.load === 'function') {
    await transcriber.load();
  }
  const result = await transcriber.transcribe(pcm, { kind: 'pcm16', language: langArg || 'zh' });

  // dsh-ears 的 transcribe() 返回 {text, language, elapsedMs, ...}
  let text = '';
  if (typeof result === 'string') text = result.trim();
  else if (result && typeof result.text === 'string') text = result.text.trim();
  else if (typeof result?.result === 'string') text = result.result.trim();
  else if (Array.isArray(result?.segments)) text = result.segments.map((s) => (s && s.text) || '').join('').trim();
  emit({ ok: true, text });
} catch (err) {
  emit({ ok: false, error: String((err && err.message) || err) });
}
process.exit(0);
