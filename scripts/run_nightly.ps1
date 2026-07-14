# PRELUDE data pipeline — nightly runner (invoked by Windows Task Scheduler).
# Builds the feed, validates, publishes to GitHub Pages via git push.
# On validation failure the last-good feed stays published and this exits 1.

$ErrorActionPreference = "Stop"
$repo = "C:\Dev\prelude-data"
Set-Location $repo

# Pull first so the nightly commit never conflicts (overlay edits may have
# been pushed from elsewhere).
git pull --rebase --quiet 2>&1 | Out-Null

& "$repo\.venv\Scripts\python.exe" -m prelude_data.pipeline
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host "prelude-data pipeline FAILED with exit code $code — see logs\ for details"
}
exit $code
