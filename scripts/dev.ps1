#requires -Version 5.1
# Windows入口只转交参数给dev.py；环境隔离、状态目录与资源归属都由统一Python启动器处理。
# 不在此读取生产.env，也不另写一套会与Linux流程分叉的启动逻辑。
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$DevArguments)
$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'dev.py') @DevArguments
exit $LASTEXITCODE
