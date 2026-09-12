param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -c "import yaml" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error '缺少 PyYAML。先运行 python -m pip install -r requirements.txt'
    exit 1
}
python -X utf8 -m doctree serve --port $Port
