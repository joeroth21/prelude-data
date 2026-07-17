# The Brief — twice-weekly gather + draft (Task Scheduler: Mon & Thu).
# Drafts land in briefs_drafts\YYYY-MM-DD\ with reviewed: false. The review
# console server is started (idle, localhost-only) and a Windows toast is
# fired — clicking it opens the console. PUBLISHING IS ALWAYS MANUAL: the
# console's PUBLISH button, or the CLI fallback:
#   .venv\Scripts\python -m prelude_data.briefs_cli publish

$ErrorActionPreference = "Stop"
$repo = "C:\Dev\prelude-data"
Set-Location $repo

git pull --rebase --quiet 2>&1 | Out-Null

& "$repo\.venv\Scripts\python.exe" -m prelude_data.briefs_cli gather-draft
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host "The Brief gather/draft FAILED with exit code $code - see logs\"
    exit $code
}

# Count today's drafts for the toast
$today = Get-Date -Format "yyyy-MM-dd"
$cycleDir = Join-Path $repo "briefs_drafts\$today"
$count = 0
if (Test-Path $cycleDir) {
    $count = (Get-ChildItem $cycleDir -Filter *.md | Measure-Object).Count
}

if ($count -gt 0) {
    # Start the review console (no browser pop at 07:00; idle until visited).
    Start-Process -FilePath "$repo\.venv\Scripts\pythonw.exe" `
        -ArgumentList "$repo\scripts\briefs_review.py", "--no-browser" `
        -WindowStyle Hidden
    Start-Sleep -Seconds 2
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$repo\scripts\notify_review.ps1" `
        -Message "$count draft$(if ($count -ne 1) {'s'}) ready for review"
}
exit 0
