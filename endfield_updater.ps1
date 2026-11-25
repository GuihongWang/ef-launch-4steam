# Steam Endfield Updater - CLI launcher
#
# NOTE: keep this file ASCII-only.
# Windows PowerShell 5.1 reads .ps1 as ANSI (GBK) unless there is a BOM,
# so non-ASCII source text corrupts string parsing. Chinese output is
# produced by endfield_updater.py, which handles UTF-8 correctly.
#
# Usage:  .\endfield_updater.ps1 status | check | apply | convert | find | ...

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$py = $null
if ($env:ENDFIELD_PYTHON) {
    $py = $env:ENDFIELD_PYTHON
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $py = "python"
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $py = "py"
}

if (-not $py) {
    Write-Host "[ERROR] Python not found. Please install Python 3.10+." -ForegroundColor Red
    exit 1
}

& $py "$PSScriptRoot\endfield_updater.py" @Rest
exit $LASTEXITCODE
