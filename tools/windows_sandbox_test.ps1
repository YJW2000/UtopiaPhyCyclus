param([Parameter(Mandatory=$true)][string]$AppPath)
$ErrorActionPreference = 'Stop'
$resolvedApp = (Resolve-Path -LiteralPath $AppPath).Path
if (-not (Test-Path -LiteralPath (Join-Path $resolvedApp 'UtopiaPhyCyclus.exe'))) { throw 'The complete portable application directory is required.' }
$sandbox = Join-Path $env:WINDIR 'System32/WindowsSandbox.exe'
if (-not (Test-Path -LiteralPath $sandbox)) { throw 'Windows Sandbox is not installed on this host. This script has not run a clean-Windows test.' }
$escapedPath = [System.Security.SecurityElement]::Escape($resolvedApp)
$configuration = @"
<Configuration>
  <Networking>Disable</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <MappedFolders><MappedFolder><HostFolder>$escapedPath</HostFolder><SandboxFolder>C:\UtopiaTest</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder></MappedFolders>
  <LogonCommand><Command>powershell.exe -NoProfile -Command "`$env:PATH='C:\Windows\System32;C:\Windows'; Set-Location C:\UtopiaTest; &amp; .\UtopiaPhyCyclus.exe --self-test; Read-Host 'Self-test finished. Check .runtime/selftest/report.json'"</Command></LogonCommand>
</Configuration>
"@
$configurationPath = Join-Path $env:TEMP 'UtopiaPhyCyclus-clean-windows.wsb'
[System.IO.File]::WriteAllText($configurationPath, $configuration, [System.Text.UTF8Encoding]::new($false))
# The user explicitly runs this script to see and control the interactive test VM.
Start-Process -FilePath $sandbox -ArgumentList @($configurationPath)
