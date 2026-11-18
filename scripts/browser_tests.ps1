$ErrorActionPreference = "Stop"
$Project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Root = Join-Path $env:LOCALAPPDATA "jobfilter\playwright"
$Venv = Join-Path $Root "venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$Browsers = Join-Path $Root "browsers"

try {
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    python -m venv $Venv
    & $Python -m pip install --quiet --disable-pip-version-check -e "$Project[dev]" playwright
    $env:PLAYWRIGHT_BROWSERS_PATH = $Browsers
    & $Python -m playwright install chromium
    & $Python -m pytest -q (Join-Path $Project "tests\test_extension_filler.py") (Join-Path $Project "tests\test_survey.py")
    if ($LASTEXITCODE -ne 0) { throw "Browser tests failed with exit code $LASTEXITCODE" }
}
finally {
    if (Test-Path -LiteralPath $Root) {
        $resolved = (Resolve-Path -LiteralPath $Root).Path
        $allowed = (Join-Path $env:LOCALAPPDATA "jobfilter")
        if (-not $resolved.StartsWith($allowed, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove unexpected path: $resolved"
        }
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
