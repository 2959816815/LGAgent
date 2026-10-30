# 构建 sidecar 的 PowerShell 入口。
# 实际逻辑在 build-sidecar.mjs（与 pnpm dist:win 共用同一份配置），
# 这里只做转发，避免两份参数各自漂移。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

& node (Join-Path $root "packaging\build-sidecar.mjs")
if ($LASTEXITCODE -ne 0) {
    throw "sidecar 构建失败，退出码 $LASTEXITCODE"
}
