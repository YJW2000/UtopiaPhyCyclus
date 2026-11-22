const { test } = require('node:test')
const assert = require('node:assert/strict')
const { spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { bootstrapWinget } = require('../desktop/winget-bootstrap.cjs')

// Exercise the Windows PowerShell control flow with stand-ins for all commands
// that could register an app, access the network or modify the user profile.
function simulate(stage, gallery = 'https://www.powershellgallery.com/api/v2') {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'utopia-winget-test-'))
  const alias = path.join(root, 'Microsoft', 'WindowsApps', 'winget.exe')
  const shell = path.join(process.env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
  const mocks = `
    function Set-TestAlias { Copy-Item -LiteralPath $env:TEST_NODE_EXE -Destination $winget }
    function Add-AppxPackage { Write-Output 'step-register'; if ($env:TEST_STAGE -eq 'register') { Set-TestAlias } }
    function Get-Module { return $null }
    function Get-PSRepository { return @{ SourceLocation = $env:TEST_GALLERY } }
    function Register-PSRepository { throw 'unexpected repository registration' }
    function Get-PackageProvider { return @() }
    function Install-PackageProvider { [Console]::WriteLine('step-nuget') }
    function Install-Module { Write-Output 'step-module' }
    function Import-Module { Write-Output 'step-import' }
    function Repair-WinGetPackageManager { Write-Output 'step-repair'; if ($env:TEST_STAGE -eq 'repair') { Set-TestAlias } }
  `
  try {
    fs.mkdirSync(path.dirname(alias), { recursive: true })
    return bootstrapWinget(shell, (exe, args) => {
      const script = mocks + args.at(-1)
      return spawnSync(exe, [...args.slice(0, -1), script], {
        env: { ...process.env, LOCALAPPDATA: root, TEST_NODE_EXE: process.execPath, TEST_STAGE: stage, TEST_GALLERY: gallery },
        timeout: 30_000, encoding: 'utf8', windowsHide: true,
      })
    })
  } finally {
    fs.rmSync(root, { recursive: true, force: true })
  }
}

const windows = { skip: process.platform !== 'win32' }

test('an existing App Installer registration is sufficient', windows, () => {
  const result = simulate('register')
  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /step-register/)
  assert.doesNotMatch(result.stdout, /step-repair|step-module/)
})

test('a fresh install checks the official gallery and verifies the recovered CLI', windows, () => {
  const result = simulate('repair')
  assert.equal(result.status, 0, result.stderr)
  assert.match(result.stdout, /step-register[\s\S]*step-nuget[\s\S]*step-module[\s\S]*step-repair/)
  const missing = simulate('broken-alias')
  assert.notEqual(missing.status, 0)
  assert.match(missing.stderr, /unavailable/)
})

test('a substituted gallery cannot supply the installer', windows, () => {
  const result = simulate('repair', 'https://example.invalid/api/v2')
  assert.notEqual(result.status, 0)
  assert.doesNotMatch(result.stdout, /step-module|step-repair/)
})
