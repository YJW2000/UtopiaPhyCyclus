// UtopiaPhyCyclus's adapter for the public whisper.node API (MIT).
// This module does not require the user's DSH installation or profile.
import { createRequire } from 'node:module';
import { join } from 'node:path';

export function resolveConfig(env = process.env) {
  return {
    modelPath: env.DSH_EARS_MODEL,
    moduleDir: env.UTOPIA_WHISPER_MODULE_DIR,
    threads: Math.max(1, Math.min(8, Number(env.DSH_EARS_THREADS || 4))),
    useGpu: env.UTOPIA_WHISPER_GPU === '1',
  };
}

export class Transcriber {
  constructor(config) {
    this.config = config;
    this.context = null;
    this.queue = Promise.resolve();
  }
  async load() {
    if (this.context) return;
    const require = createRequire(join(this.config.moduleDir, 'package.json'));
    const { initWhisper } = require('@fugood/whisper.node');
    this.context = await initWhisper({ filePath: this.config.modelPath, useGpu: this.config.useGpu }, 'default');
  }
  async transcribe(buffer, options = {}) {
    const work = async () => {
      await this.load();
      // Native API expects a standalone ArrayBuffer, not Buffer's shared pool.
      const samples = Uint8Array.from(buffer);
      const job = this.context.transcribeData(samples.buffer, {
        language: options.language || 'zh', maxThreads: this.config.threads,
        temperature: 0.0, translate: false,
      });
      const result = await job.promise;
      return { text: String(result.result || '').trim(), segments: result.segments || [] };
    };
    const pending = this.queue.then(work, work);
    this.queue = pending.catch(() => {});
    return pending;
  }
}
