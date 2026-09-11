<#
.SYNOPSIS
    Universal AI Agent Skill Discovery, Retrieval & Cross-Harness Manager (PowerShell)

.DESCRIPTION
    Launches the blazing-fast native Rust binary (with Python fallback) to discover,
    search, inspect, retrieve, and cross-link agent skills across all harnesses
    (.claude, .gemini, .agents, .cursor, .windsurf, .roo, .cline, .continue, .goose, etc.).

.EXAMPLE
    .\skills.ps1 harnesses
    .\skills.ps1 list
    .\skills.ps1 search "audit"
    .\skills.ps1 preview humanizer
    .\skills.ps1 get ponytail --file
    .\skills.ps1 link humanizer --to gemini
#>

[CmdletBinding()]
param(
    [Parameter(Position=0)]
    [string]$Command = "list",

    [Parameter(Position=1, ValueFromRemainingArguments=$true)]
    [string[]]$Arguments
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RustExe = Join-Path $ScriptDir "skills.exe"
$PyScript = Join-Path $ScriptDir "skill_discovery.py"

if (Test-Path $RustExe) {
    $AllArgs = @($Command) + $Arguments
    & $RustExe @AllArgs
    exit $LASTEXITCODE
} elseif (Test-Path $PyScript) {
    $AllArgs = @($PyScript, $Command) + $Arguments
    & python @AllArgs
    exit $LASTEXITCODE
} else {
    Write-Error "Neither skills.exe nor skill_discovery.py found in: $ScriptDir"
    exit 1
}
