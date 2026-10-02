param(
    [string]$Distro = "Ubuntu-24.04",
    [switch]$Build
)

# WSL may emit a harmless localized networking warning on stderr even when the
# command succeeds. Check native exit codes explicitly instead of treating any
# stderr line as a terminating PowerShell error.
$ErrorActionPreference = "Continue"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($projectRoot -notmatch "^(?<drive>[A-Za-z]):\\(?<rest>.*)$") {
    throw "The demo launcher requires a local Windows drive path: $projectRoot"
}
$drive = $Matches["drive"].ToLowerInvariant()
$rest = $Matches["rest"].Replace("\", "/")
$wslProjectRoot = "/mnt/$drive/$rest"

& wsl.exe -d $Distro -u root -- systemctl start docker
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start the WSL Docker Engine"
}

& wsl.exe -d $Distro -u root -- pgrep -f "^sleep infinity$" *> $null
if ($LASTEXITCODE -ne 0) {
    Start-Process -FilePath "wsl.exe" -WindowStyle Hidden -ArgumentList @(
        "-d", $Distro, "-u", "root", "--", "sleep", "infinity"
    ) | Out-Null
}

$composeArguments = @(
    "-d", $Distro, "-u", "root", "--cd", $wslProjectRoot,
    "--", "docker", "compose", "--profile", "demo-target", "up", "-d"
)
if ($Build) {
    $composeArguments += "--build"
}
& wsl.exe @composeArguments
if ($LASTEXITCODE -ne 0) {
    throw "Failed to start the Mini-Drop Compose stack"
}

for ($attempt = 1; $attempt -le 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "http://localhost/api/healthz" -TimeoutSec 2
        if ($health) {
            Write-Host "Mini-Drop is ready: http://localhost"
            Write-Host "Demo workload: http://localhost:8081/health"
            exit 0
        }
    }
    catch {
        if ($attempt -eq 30) {
            throw "Services started, but health checks did not pass within 60 seconds"
        }
    }
    Start-Sleep -Seconds 2
}
