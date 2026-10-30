# One-shot helper: waits for DSH Desktop to fully quit, then runs the DSH CLI to
# add one or more plugins at exact versions into the desktop profile, verifies
# them, and relaunches DSH. Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File update-plugin.ps1 -Targets "dsh-context@0.74.0,billion-context@0.1.204"

param(
    [Parameter(Mandatory = $true)][string]$Targets
)

$ErrorActionPreference = 'Continue'
$log = 'D:\Vibecoding\Langgraph\tools\market-update.log'
$specs = @($Targets.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })

function L([string]$m) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $log -Value $line -Encoding UTF8
}

L ("=== start: " + ($specs -join ', ') + " ===")

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

# 2. run the DSH CLI once with every target
$cli = 'D:\deepseekharnessdesktop\resources\runtime\cli\bin\dsh.cmd'
$pnpmArgs = @('plugin', '--profile', 'desktop', 'add') + $specs + @('--config.minimum-release-age=0')
L ('running: dsh ' + ($pnpmArgs -join ' '))

$output = & $cli @pnpmArgs 2>&1 | Out-String
$code = $LASTEXITCODE
L ('exit=' + $code)
L $output

# 3. verify the installed versions and manifest entries
$profileDir = Join-Path $env:USERPROFILE '.dsh\profiles\desktop'
$manifest = Join-Path $profileDir 'package.json'
foreach ($spec in $specs) {
    $name = ($spec -split '@')[0]
    $pkg = Join-Path $profileDir "node_modules\$name\package.json"
    if (Test-Path $pkg) {
        $raw = [System.IO.File]::ReadAllText($pkg)
        if ($raw -match '"version"\s*:\s*"([^"]+)"') { L ('installed ' + $name + ' version = ' + $matches[1]) }
        else { L ('could not find a version field in ' + $name) }
    } else {
        L ('MISSING: node_modules\' + $name + '\package.json')
    }
    if (Test-Path $manifest) {
        $line = (Select-String -Path $manifest -Pattern ('"' + [regex]::Escape($name) + '"\s*:') | Select-Object -First 1).Line
        if ($line) { L ('manifest entry: ' + $line.Trim()) }
    }
}

# 4. relaunch DSH Desktop
L 'relaunching DSH Desktop'
Start-Process 'D:\deepseekharnessdesktop\DeepSeek Harness.exe'
L '=== done ==='
