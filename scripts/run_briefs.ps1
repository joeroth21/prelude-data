# The Brief — twice-weekly gather + draft (Task Scheduler: Mon & Thu).
# Drafts land in briefs_drafts\YYYY-MM-DD\ with reviewed: false and a
# marker file appears on the Desktop. PUBLISHING IS ALWAYS MANUAL:
#   .venv\Scripts\python -m prelude_data.briefs_cli publish

$ErrorActionPreference = "Stop"
$repo = "C:\Dev\prelude-data"
Set-Location $repo

git pull --rebase --quiet 2>&1 | Out-Null

& "$repo\.venv\Scripts\python.exe" -m prelude_data.briefs_cli gather-draft
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host "The Brief gather/draft FAILED with exit code $code — see logs\"
}
exit $code
