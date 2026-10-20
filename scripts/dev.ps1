#requires -Version 5.1
[CmdletBinding()]
param([Parameter(ValueFromRemainingArguments=$true)][string[]]$DevArguments)
$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'dev.py') @DevArguments
exit $LASTEXITCODE
