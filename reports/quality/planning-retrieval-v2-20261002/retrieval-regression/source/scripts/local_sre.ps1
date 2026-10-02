param([ValidateSet('Start','Build','Status','Stop','Index')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
$sreRoot = Split-Path -Parent $PSScriptRoot
Push-Location $sreRoot
try {
    if (!(Test-Path -LiteralPath '.env.local-sre')) { throw 'Run python scripts/setup_local_sre.py first.' }
    $sreCompose = @('compose','--env-file','.env.local-sre','-p','mini-drop-local-sre','-f','docker-compose.yml','-f','docker-compose.local-sre.yml')
    switch ($Action) {
        'Build' {
            $sreOldRayon = $env:RAYON_NUM_THREADS
            $env:RAYON_NUM_THREADS = '1'
            Push-Location web
            try { & npm.cmd run build:check }
            finally { Pop-Location; $env:RAYON_NUM_THREADS = $sreOldRayon }
            if ($LASTEXITCODE -ne 0) { throw 'Web build failed' }
            $sreOldGoos = $env:GOOS
            $sreOldCgo = $env:CGO_ENABLED
            try {
                $env:GOOS = 'linux'; $env:CGO_ENABLED = '0'
                New-Item -ItemType Directory -Force -Path output/local-sre-api | Out-Null
                Push-Location apiserver
                try { & go build -trimpath -o ../output/local-sre-api/mini-drop-apiserver ./cmd/apiserver }
                finally { Pop-Location }
                if ($LASTEXITCODE -ne 0) { throw 'Go API build failed' }
            } finally { $env:GOOS = $sreOldGoos; $env:CGO_ENABLED = $sreOldCgo }
            foreach ($sreService in @('migrate','web','apiserver','python-hotspot')) {
                & docker @sreCompose build $sreService
                if ($LASTEXITCODE -ne 0) { throw "Build failed: $sreService" }
            }
        }
        'Start' { & docker @sreCompose up -d --no-build postgres minio migrate chroma control-plane diagnosis-worker analyzer apiserver native-agent python-hotspot web }
        'Index' { & docker @sreCompose exec -T diagnosis-worker python scripts/build_knowledge_index.py }
        'Status' { & docker @sreCompose ps -a }
        'Stop' { & docker @sreCompose stop }
    }
    if ($LASTEXITCODE -ne 0) { throw "Local SRE action failed: $Action" }
} finally { Pop-Location }
