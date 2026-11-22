// Microsoft's current-user recovery procedure for App Installer. Run only
// when an explicit tool install needs WinGet and its executable is missing.
const bootstrap = String.raw`
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$winget = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'

function Test-WinGet {
  if (Test-Path -LiteralPath $winget -PathType Leaf) {
    try {
      & $winget --version 2>$null | Out-Null
      return ($LASTEXITCODE -eq 0)
    } catch {}
  }
  return $false
}

if (Test-WinGet) { exit 0 }
try {
  Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop
} catch {
  # Fresh Windows installations may not have App Installer to register yet.
}
if (Test-WinGet) { exit 0 }

[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
if (-not (Get-Module -ListAvailable -Name Microsoft.WinGet.Client)) {
  $gallery = Get-PSRepository -Name PSGallery -ErrorAction SilentlyContinue
  if (-not $gallery) {
    Register-PSRepository -Default
    $gallery = Get-PSRepository -Name PSGallery
  }
  if ($gallery.SourceLocation.TrimEnd('/') -ne 'https://www.powershellgallery.com/api/v2') {
    throw 'The registered PSGallery URL does not match the official PowerShell Gallery.'
  }
  if (-not (Get-PackageProvider -ListAvailable | Where-Object { $_.Name -eq 'NuGet' -and $_.Version -ge [version]'2.8.5.201' })) {
    Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force -Confirm:$false | Out-Null
  }
  Install-Module -Name Microsoft.WinGet.Client -Repository PSGallery -Scope CurrentUser -Force -Confirm:$false
}
Import-Module Microsoft.WinGet.Client -ErrorAction Stop
Repair-WinGetPackageManager -Latest
if (-not (Test-WinGet)) {
  throw 'App Installer finished, but the winget.exe app execution alias is unavailable.'
}
`

function bootstrapWinget(powershell, execute, options) {
  return execute(powershell, ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command', bootstrap], options)
}

module.exports = { bootstrapWinget }
