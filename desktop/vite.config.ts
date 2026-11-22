import { createReadStream } from 'node:fs'
import { cp, mkdir, stat } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

const pdfRoot = fileURLToPath(new URL('./node_modules/pdfjs-dist/', import.meta.url))
const pdfDirectories = ['cmaps', 'standard_fonts', 'wasm']

function pdfSupportFiles(): Plugin {
  return {
    name: 'pdfjs-support-files',
    configureServer(server) {
      server.middlewares.use('/pdfjs', (request, response, next) => {
        const match = /^\/(cmaps|standard_fonts|wasm)\/[a-zA-Z0-9_.-]+$/.exec(request.url ?? '')
        if (!match || ['.', '..'].includes(match[0].split('/').at(-1) ?? '')) {
          next()
          return
        }
        const file = path.join(pdfRoot, match[0])
        void stat(file).then(info => {
          if (!info.isFile()) {
            next()
            return
          }
          response.setHeader('Content-Length', info.size)
          response.setHeader('Content-Type', file.endsWith('.wasm') ? 'application/wasm' : 'application/octet-stream')
          createReadStream(file).on('error', error => response.destroy(error)).pipe(response)
        }, () => next())
      })
    },
    async writeBundle({ dir }) {
      if (!dir) return
      const destination = path.join(dir, 'pdfjs')
      await mkdir(destination, { recursive: true })
      await Promise.all([...pdfDirectories, 'LICENSE'].map(name =>
        cp(path.join(pdfRoot, name), path.join(destination, name), { recursive: true }),
      ))
    },
  }
}

export default defineConfig({
  base: './',
  plugins: [react(), pdfSupportFiles()],
  server: { host: '127.0.0.1', strictPort: true },
})
