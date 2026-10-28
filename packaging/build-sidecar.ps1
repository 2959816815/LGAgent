$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = "D:\Anaconda3\envs\rag\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到打包用 Python: $python"
}

& $python -m PyInstaller `
  --noconfirm --clean --onedir `
  --name langgraph-sidecar `
  --distpath "$root\dist-sidecar" `
  --workpath "$root\build\sidecar" `
  --specpath "$root\build" `
  --collect-submodules uvicorn `
  --collect-submodules langgraph `
  --collect-submodules langchain_core `
  --collect-submodules langchain_openai `
  --collect-data certifi `
  --exclude-module torch `
  --exclude-module torchvision `
  --exclude-module torchaudio `
  --exclude-module tensorflow `
  --exclude-module transformers `
  --exclude-module sklearn `
  --exclude-module scipy `
  --exclude-module pandas `
  --exclude-module matplotlib `
  --exclude-module IPython `
  --exclude-module notebook `
  --exclude-module tkinter `
  --exclude-module zmq `
  --exclude-module onnxruntime `
  desktop_api.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 失败，退出码 $LASTEXITCODE"
}

$exe = Join-Path $root "dist-sidecar\langgraph-sidecar\langgraph-sidecar.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "未生成 sidecar: $exe"
}
Write-Output "SIDECAR_OK $exe"
