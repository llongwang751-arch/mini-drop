$ErrorActionPreference = "Stop"

Write-Host "== Python compile =="
python -m compileall minidrop

Write-Host "== Backend tests =="
python -m unittest discover -s tests -v

Write-Host "== Coverage gate =="
coverage run --source=minidrop -m unittest discover -s tests
coverage report --fail-under=50

Write-Host "== Frontend install =="
Push-Location frontend
try {
    npm install
    Write-Host "== Frontend typecheck =="
    npm run typecheck
    Write-Host "== Frontend build =="
    npm run build
}
finally {
    Pop-Location
}

Write-Host "All checks passed."
