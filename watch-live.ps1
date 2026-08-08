$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
Write-Host "LIVE MODE will move files. Press Ctrl+C now to cancel."
$confirmation = Read-Host "Type LIVE to continue"
if ($confirmation -cne "LIVE") {
    Write-Host "Cancelled."
    exit 0
}
python -m smart_sorter watch --apply
