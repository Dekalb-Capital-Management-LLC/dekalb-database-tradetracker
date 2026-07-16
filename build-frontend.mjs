import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import process from 'node:process'

const frontendDirectory = new URL('./trade-tracker/frontend/', import.meta.url)
const vitePackage = new URL('node_modules/vite/package.json', frontendDirectory)

function run(args) {
  const command = process.platform === 'win32' ? (process.env.ComSpec ?? 'cmd.exe') : 'npm'
  const commandArgs = process.platform === 'win32'
    ? ['/d', '/s', '/c', 'npm', ...args]
    : args
  const result = spawnSync(command, commandArgs, {
    cwd: frontendDirectory,
    stdio: 'inherit',
  })

  if (result.error) throw result.error
  if (result.status !== 0) process.exit(result.status ?? 1)
}

if (process.argv.includes('--clean') || !existsSync(vitePackage)) {
  run(['ci', '--no-audit', '--no-fund'])
}

run(['run', 'build'])
