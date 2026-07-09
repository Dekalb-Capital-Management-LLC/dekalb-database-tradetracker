/**
 * Start the FastAPI dev server using trade-tracker/api/.venv.
 * Creates the venv and pip-installs requirements on first run (or if deps are missing).
 */
const { spawn } = require('child_process')
const { existsSync } = require('fs')
const path = require('path')

const apiDir = path.resolve(__dirname, '../../api')
const venvDir = path.join(apiDir, '.venv')
const python =
  process.platform === 'win32'
    ? path.join(venvDir, 'Scripts', 'python.exe')
    : path.join(venvDir, 'bin', 'python')

function run(cmd, args, opts = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { stdio: 'inherit', shell: false, cwd: apiDir, ...opts })
    child.on('error', reject)
    child.on('exit', (code) =>
      code === 0 ? resolve() : reject(new Error(`${cmd} ${args.join(' ')} exited with code ${code}`)),
    )
  })
}

async function ensureVenv() {
  if (existsSync(python)) return

  const py = process.env.PYTHON || 'python'
  console.log('[dev:api] Creating Python venv at trade-tracker/api/.venv ...')
  await run(py, ['-m', 'venv', '.venv'])
  console.log('[dev:api] Installing API dependencies (first run only) ...')
  await run(python, ['-m', 'pip', 'install', '-r', 'requirements.txt'])
}

async function ensureDeps() {
  try {
    await run(python, ['-c', 'import asyncpg'])
  } catch {
    console.log('[dev:api] Installing API dependencies ...')
    await run(python, ['-m', 'pip', 'install', '-r', 'requirements.txt'])
  }
}

;(async () => {
  const setupOnly = process.argv.includes('--setup-only')
  try {
    await ensureVenv()
    await ensureDeps()
    if (setupOnly) {
      console.log('[dev:api] API venv ready.')
      return
    }
    await run(python, ['-m', 'uvicorn', 'main:app', '--reload', '--port', '8000'])
  } catch (err) {
    console.error('[dev:api]', err.message)
    process.exit(1)
  }
})()
