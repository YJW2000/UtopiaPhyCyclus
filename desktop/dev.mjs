import { spawn } from 'node:child_process'
import electron from 'electron'
import { createServer } from 'vite'

const server = await createServer({ server: { port: 5173 } })
await server.listen()
server.printUrls()
const child = spawn(electron, ['.', ...process.argv.slice(2)], {
  env: { ...process.env, UTOPIA_DESKTOP_VITE_URL: 'http://127.0.0.1:5173/' },
  stdio: 'inherit',
})
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.once(signal, () => child.kill(signal))
}
child.once('error', async error => {
  console.error(error.message)
  await server.close()
  process.exitCode = 1
})
child.once('exit', async code => {
  await server.close()
  process.exitCode = code ?? 1
})
