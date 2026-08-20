[CmdletBinding()]
param(
    [string]$HostAddress = "47.112.10.137",
    [string]$RemoteUser = "root",
    [string]$HostAlias = "mini-drop-control",

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^SHA256:[A-Za-z0-9+/]{43}$')]
    [string]$ExpectedEd25519Fingerprint,

    [string]$IdentityFile,
    [switch]$WriteSshConfig
)

$ErrorActionPreference = "Stop"

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "缺少命令 $Name。请先在 Windows 可选功能中安装 OpenSSH Client。"
    }
}

function Normalize-PathForSshConfig {
    param([Parameter(Mandatory = $true)][string]$Path)
    return ([System.IO.Path]::GetFullPath($Path) -replace '\\', '/')
}

Require-Command ssh
Require-Command ssh-keygen
Require-Command ssh-keyscan

$sshDirectory = Join-Path $HOME ".ssh"
$knownHostsPath = Join-Path $sshDirectory "known_hosts"
$configPath = Join-Path $sshDirectory "config"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$temporaryKeyFile = Join-Path ([System.IO.Path]::GetTempPath()) "mini-drop-control-$timestamp.key"

Write-Host "[1/6] 正在读取 Control 提供的 ED25519 公钥（此结果本身不构成信任依据）..." -ForegroundColor Cyan
$scanOutput = & ssh-keyscan -T 8 -t ed25519 $HostAddress 2>$null
if ($LASTEXITCODE -ne 0 -or -not $scanOutput) {
    throw "未能从 ${HostAddress}:22 读取 ED25519 公钥。请检查网络、安全组和 sshd。"
}

$ed25519Line = $scanOutput |
    Where-Object { $_ -match '^[^#]\S+\s+ssh-ed25519\s+\S+' } |
    Select-Object -First 1
if (-not $ed25519Line) {
    throw "Control 没有返回可用的 ssh-ed25519 主机公钥。"
}

Set-Content -LiteralPath $temporaryKeyFile -Value $ed25519Line -Encoding ascii
try {
    $fingerprintOutput = (& ssh-keygen -lf $temporaryKeyFile -E sha256 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "ssh-keygen 无法计算 Control 主机指纹：$fingerprintOutput"
    }
    if ($fingerprintOutput -notmatch '(SHA256:[A-Za-z0-9+/]{43})') {
        throw "无法从 ssh-keygen 输出中识别 SHA-256 指纹：$fingerprintOutput"
    }
    $observedFingerprint = $Matches[1]

    Write-Host "可信渠道提供：$ExpectedEd25519Fingerprint"
    Write-Host "本次网络观察：$observedFingerprint"
    if ($observedFingerprint -cne $ExpectedEd25519Fingerprint) {
        throw "主机指纹不一致。脚本已经停止，没有写入 known_hosts。请在云厂商控制台重新核对。"
    }

    Write-Host "[2/6] 指纹一致，准备写入当前 Windows 用户 known_hosts..." -ForegroundColor Green
    New-Item -ItemType Directory -Path $sshDirectory -Force | Out-Null

    if (Test-Path -LiteralPath $knownHostsPath) {
        Copy-Item -LiteralPath $knownHostsPath -Destination "$knownHostsPath.backup-$timestamp" -Force
        $existing = (& ssh-keygen -F $HostAddress -f $knownHostsPath 2>$null | Out-String)
        if ($existing) {
            $existingKeyFile = Join-Path ([System.IO.Path]::GetTempPath()) "mini-drop-existing-$timestamp.key"
            try {
                $existingKeyLines = $existing -split "`r?`n" |
                    Where-Object { $_ -match '^[^#].*\s+ssh-ed25519\s+' }
                if ($existingKeyLines) {
                    Set-Content -LiteralPath $existingKeyFile -Value $existingKeyLines -Encoding ascii
                    $existingFingerprintText = (& ssh-keygen -lf $existingKeyFile -E sha256 2>&1 | Out-String)
                    if ($existingFingerprintText -notmatch [regex]::Escape($ExpectedEd25519Fingerprint)) {
                        throw "known_hosts 已存在该地址，但指纹与可信值不一致。已停止，备份位于 $knownHostsPath.backup-$timestamp"
                    }
                }
            }
            finally {
                Remove-Item -LiteralPath $existingKeyFile -Force -ErrorAction SilentlyContinue
            }
        }
        else {
            Add-Content -LiteralPath $knownHostsPath -Value $ed25519Line -Encoding ascii
        }
    }
    else {
        Set-Content -LiteralPath $knownHostsPath -Value $ed25519Line -Encoding ascii
    }

    Write-Host "[3/6] 使用 StrictHostKeyChecking 复核 known_hosts..." -ForegroundColor Cyan
    $knownHostCheck = (& ssh-keygen -F $HostAddress -f $knownHostsPath 2>&1 | Out-String).Trim()
    if (-not $knownHostCheck) {
        throw "known_hosts 写入后仍无法找到 $HostAddress。"
    }

    $sshTarget = "${RemoteUser}@${HostAddress}"
    $sshArguments = @(
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=8"
    )

    if ($IdentityFile) {
        $resolvedIdentity = [System.IO.Path]::GetFullPath($IdentityFile)
        if (-not (Test-Path -LiteralPath $resolvedIdentity -PathType Leaf)) {
            throw "指定的私钥文件不存在：$resolvedIdentity"
        }
        $sshArguments += @("-o", "IdentitiesOnly=yes", "-i", $resolvedIdentity)
    }

    if ($WriteSshConfig) {
        if (-not $IdentityFile) {
            throw "使用 -WriteSshConfig 时必须通过 -IdentityFile 指定已有私钥。"
        }

        Write-Host "[4/6] 写入 SSH 别名配置（先备份原配置）..." -ForegroundColor Cyan
        if (Test-Path -LiteralPath $configPath) {
            Copy-Item -LiteralPath $configPath -Destination "$configPath.backup-$timestamp" -Force
            $currentConfig = Get-Content -LiteralPath $configPath -Raw
            if ($currentConfig -match "(?mi)^Host\s+$([regex]::Escape($HostAlias))\s*$") {
                throw "SSH config 已存在 Host $HostAlias。为避免覆盖，脚本停止；请人工检查 $configPath。"
            }
        }

        $identityForConfig = Normalize-PathForSshConfig -Path $IdentityFile
        $configBlock = @"

Host $HostAlias
    HostName $HostAddress
    User $RemoteUser
    IdentityFile $identityForConfig
    IdentitiesOnly yes
    StrictHostKeyChecking yes
    UserKnownHostsFile $($knownHostsPath -replace '\\', '/')
"@
        Add-Content -LiteralPath $configPath -Value $configBlock -Encoding utf8
        $sshTarget = $HostAlias
        $sshArguments = @(
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=8"
        )
    }
    else {
        Write-Host "[4/6] 未请求写入 SSH config，跳过别名配置。"
    }

    Write-Host "[5/6] 检查当前 ssh-agent 状态..." -ForegroundColor Cyan
    & ssh-add -l 2>$null
    if ($LASTEXITCODE -ne 0 -and -not $IdentityFile) {
        Write-Warning "当前没有可用 ssh-agent 身份，也没有通过 -IdentityFile 指定私钥。后续 BatchMode 很可能因无身份失败。"
    }

    Write-Host "[6/6] 执行严格的非交互登录测试..." -ForegroundColor Cyan
    & ssh @sshArguments $sshTarget "printf 'control-connection-ok\n'"
    if ($LASTEXITCODE -ne 0) {
        throw "主机密钥已经可信写入，但非交互认证尚未成功。请配置现有私钥或将当前本机公钥加入服务器 authorized_keys 后重试。"
    }

    Write-Host "Control SSH 已准备好" -ForegroundColor Green
}
finally {
    Remove-Item -LiteralPath $temporaryKeyFile -Force -ErrorAction SilentlyContinue
}
