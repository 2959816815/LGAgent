# One-shot helper: waits for DSH Desktop to fully quit, then runs the DSH CLI to
# add dshmarket@1.67.0 into the desktop profile, verifies it, and relaunches DSH.
# Written by the agent on 2026-10-22 as "plan A" for the dshmarket update failure.

$ErrorActionPreference = 'Continue'
$log = 'D:\Vibecoding\Langgraph\tools\market-update.log'

function L([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
}

L '=== start ==='

# 1. wait for every DSH Desktop process to exit (up to 10 minutes)
$deadline = (Get-Date).AddMinutes(10)
while ((Get-Date) -lt $deadline) {
    $procs = @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue)
    if ($procs.Count -eq 0) { break }
    Start-Sleep -Seconds 3
}

$left = @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue)
if ($left.Count -gt 0) {
    L ("ABORT: DSH still running after 10 minutes (" + $left.Count + " processes)")
    exit 1
}
L 'DSH fully closed'

# 2. run the DSH CLI: dsh plugin --profile desktop add dshmarket@1.67.0
$cli = 'D:\deepseekharnessdesktop\resources\runtime\cli\bin\dsh.cmd'
$pnpmArgs = @('plugin', '--profile', 'desktop', 'add', 'dshmarket@1.67.0', '--config.minimum-release-age=0')
L ('running: dsh ' + ($pnpmArgs -join ' '))

$output = & $cli @pnpmArgs 2>&1 | Out-String
$code = $LASTEXITCODE
L ('exit=' + $code)
L $output

# 3. verify the installed version
$profileDir = Join-Path $env:USERPROFILE '.dsh\profiles\desktop'
$pkg = Join-Path $profileDir 'node_modules\dshmarket\package.json'
if (Test-Path $pkg) {
    try {
        $v = (Get-Content $pkg -Raw | ConvertFrom-Json).version
        L ('installed dshmarket version = ' + $v)
    } catch {
        L ('could not parse installed version: ' + $_.Exception.Message)
    }
} else {
    L 'MISSING: node_modules\dshmarket\package.json'
}

$manifest = Join-Path $profileDir 'package.json'
if (Test-Path $manifest) {
    $spec = (Select-String -Path $manifest -Pattern '"dshmarket"' | Select-Object -First 1).Line
    if ($spec) { L ('manifest entry: ' + $spec.Trim()) }
}

# 4. relaunch DSH Desktop
L 'relaunching DSH Desktop'
Start-Process 'D:\deepseekharnessdesktop\DeepSeek Harness.exe'
L '=== done ==='
