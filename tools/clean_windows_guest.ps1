param(
    [Parameter(Mandatory=$true)][string]$PackagePath,
    [string]$ExpectedSha256,
    [string]$OutputRoot='C:\UtopiaValidation',
    [string]$ApiConfigFile,
    [switch]$SerialProgress,
    [switch]$ShowBrowser,
    [switch]$ShutdownWhenFinished
)
$ErrorActionPreference='Stop'
$utf8=[Text.UTF8Encoding]::new($false)
[IO.Directory]::CreateDirectory($OutputRoot) | Out-Null
$serial=$null
if($SerialProgress){
    try { $serial=[IO.Ports.SerialPort]::new('COM1',115200); $serial.Encoding=[Text.Encoding]::UTF8; $serial.Open() } catch {}
}
function Write-Status([string]$Text){
    $line=(Get-Date -Format o)+' '+$Text
    [IO.File]::AppendAllText((Join-Path $OutputRoot 'progress.log'),$line+"`r`n",$utf8)
    Write-Host $line
    if($serial -and $serial.IsOpen){$serial.WriteLine($line)}
}
function Write-Json($Value,[string]$Name){
    [IO.File]::WriteAllText((Join-Path $OutputRoot $Name),($Value|ConvertTo-Json -Depth 15),$utf8)
}
function Invoke-Application([string]$App,[string]$Arguments,[string]$Name){
    Write-Status ('START '+$Name)
    $p=Start-Process -FilePath (Join-Path $App 'UtopiaPhyCyclus.exe') -ArgumentList $Arguments -WorkingDirectory $App -WindowStyle Hidden -PassThru -Wait -RedirectStandardOutput (Join-Path $OutputRoot ($Name+'.stdout.txt')) -RedirectStandardError (Join-Path $OutputRoot ($Name+'.stderr.txt'))
    if($p.ExitCode -ne 0){throw "$Name exited with code $($p.ExitCode)"}
    Write-Status ('PASS '+$Name)
}
$report=[ordered]@{fresh_windows_vm_tested=$true;passed=$false;checks=[ordered]@{}}
try{
    Write-Status 'BEGIN fresh Windows portable validation'
    $os=Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $report.os=[ordered]@{edition=$os.EditionID;display_version=$os.DisplayVersion;build=$os.CurrentBuild;ubr=$os.UBR;architecture=$env:PROCESSOR_ARCHITECTURE}
    $report.elevated=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $external=[ordered]@{}
    foreach($name in @('python','python3','node','xelatex','pdflatex','whisper')){
        $commands=@(Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Where-Object {$_.Source -notlike '*WindowsApps*'})
        $external[$name]=($commands.Count -gt 0)
        if($commands.Count -gt 0){throw "External runtime on original PATH: $name"}
    }
    $installed=@(Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object {$_.DisplayName -match 'Python|Conda|TeX Live|MiKTeX|Node.js|Whisper|VMware Tools'} | Select-Object -ExpandProperty DisplayName)
    if($installed.Count){throw 'Guest contains separately installed application runtimes or VMware Tools'}
    $report.external_commands=$external
    $report.extra_runtime_installations=$installed
    $report.checks.clean_inventory=$true
    $hash=(Get-FileHash -LiteralPath $PackagePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if($ExpectedSha256 -and $hash -ne $ExpectedSha256.ToLowerInvariant()){throw 'Portable ZIP SHA-256 mismatch'}
    $report.package_sha256=$hash
    $extract=Join-Path $OutputRoot 'ascii-extraction'
    if(Test-Path -LiteralPath $extract){throw 'Use a new output folder for a fresh extraction'}
    Write-Status 'START unzip complete public package'
    Expand-Archive -LiteralPath $PackagePath -DestinationPath $extract
    $app=(Get-ChildItem -LiteralPath $extract -Directory | Where-Object {Test-Path -LiteralPath (Join-Path $_.FullName 'UtopiaPhyCyclus.exe')} | Select-Object -First 1).FullName
    if(-not $app){throw 'Complete application not found in ZIP'}
    Invoke-Application $app '--check' 'ascii-dependencies'
    Invoke-Application $app '--self-test' 'ascii-selftest'
    $report.checks.ascii_selftest=Get-Content -LiteralPath (Join-Path $app '.runtime/selftest/report.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if(-not $report.checks.ascii_selftest.passed){throw 'ASCII self-test did not pass'}
    Write-Status 'START Chinese extraction path'
    # Move only the freshly extracted guest-only folder, within the explicit test root.
    $chinese=Join-Path $OutputRoot '物理题库'
    $resolvedRoot=[IO.Path]::GetFullPath($OutputRoot).TrimEnd('\')+'\'
    foreach($target in @($app,$chinese)){
        if(-not [IO.Path]::GetFullPath($target).StartsWith($resolvedRoot,[StringComparison]::OrdinalIgnoreCase)){throw 'Move target is outside the explicit guest test directory'}
    }
    Move-Item -LiteralPath $app -Destination $chinese
    $app=$chinese
    Invoke-Application $app '--check' 'chinese-dependencies'
    Invoke-Application $app '--self-test' 'chinese-selftest'
    $report.checks.chinese_selftest=Get-Content -LiteralPath (Join-Path $app '.runtime/selftest/report.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if(-not $report.checks.chinese_selftest.passed){throw 'Chinese self-test did not pass'}
    if($ApiConfigFile){
        $config=Get-Content -LiteralPath $ApiConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach($key in @('AI_BASE_URL','AI_API_KEY','AI_MODEL_NAME')){[Environment]::SetEnvironmentVariable($key,[string]$config.$key,'Process')}
        $env:UTOPIA_VALIDATE_API='1'
    }
    [IO.Directory]::CreateDirectory((Join-Path $app '.runtime')) | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'clean_windows_guest.py') -Destination (Join-Path $app '.runtime/clean_windows_guest.py')
    Invoke-Application $app '.runtime/clean_windows_guest.py' 'application-session'
    $report.checks.application=Get-Content -LiteralPath (Join-Path $app '.runtime/clean-windows-app-report.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if(-not $report.checks.application.passed){throw 'Application session checks did not pass'}
    $report.passed=$true
    Write-Status 'PASS ALL fresh Windows checks'
    Write-Json $report 'clean-windows-report.json'
    if($serial -and $serial.IsOpen){$serial.WriteLine('UTOPIA_REPORT '+($report|ConvertTo-Json -Depth 15 -Compress))}
    if($ShowBrowser){
        $server=Start-Process -FilePath (Join-Path $app 'UtopiaPhyCyclus.exe') -ArgumentList '--no-browser --port=18501' -WorkingDirectory $app -WindowStyle Hidden -PassThru
        Start-Sleep -Seconds 12
        $edge=Join-Path ${env:ProgramFiles(x86)} 'Microsoft/Edge/Application/msedge.exe'
        if(Test-Path -LiteralPath $edge){Start-Process -FilePath $edge -ArgumentList '--no-first-run --no-default-browser-check --start-maximized http://127.0.0.1:18501'}
        Write-Status 'BROWSER_READY capture guest screen now'
        Start-Sleep -Seconds 90
        Stop-Process -Id $server.Id -ErrorAction SilentlyContinue
    }
} catch {
    $report.passed=$false
    $report.error=$_.Exception.Message
    Write-Json $report 'clean-windows-report.json'
    Write-Status ('FAIL '+$_.Exception.Message)
    if($serial -and $serial.IsOpen){$serial.WriteLine('UTOPIA_REPORT '+($report|ConvertTo-Json -Depth 15 -Compress))}
} finally {
    if($serial -and $serial.IsOpen){$serial.Close()}
    if($ShutdownWhenFinished){& shutdown.exe /s /t 10}
}
if(-not $report.passed){exit 1}
