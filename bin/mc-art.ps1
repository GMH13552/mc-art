# mc-art: the deterministic engine of this skill. No model, no credentials.
#
#   .\bin\mc-art.ps1 list-groups --source <jar> [--filter bow]
#   .\bin\mc-art.ps1 render      --plan my.plan.json --out outputs\mine
#
# The PowerShell-native entry point. bin\mc-art.cmd is the cmd one, bin/mc-art
# the POSIX one. All three find Python the same way: a candidate counts only if
# it actually RUNS and prints exactly 1. "The command exists" is not evidence --
# on Windows `python3` is often a 0-byte Microsoft Store stub that exists, is
# found by Get-Command, and exits 9009 without running anything.

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Push-Location $here
try {
    function Get-McArtRest {
        # PowerShell's 1..0 is a DESCENDING range, not empty, so a single-element
        # candidate must not be sliced blindly.
        param([string[]]$Candidate)
        if ($Candidate.Length -gt 1) { return $Candidate[1..($Candidate.Length - 1)] }
        return @()
    }

    function Test-McArtPython {
        param([string[]]$Candidate)
        try {
            $output = & $Candidate[0] @(Get-McArtRest $Candidate) -c 'print(1)' 2>$null
            if ($LASTEXITCODE -ne 0) { return $false }
            return ("$output".Trim() -eq '1')
        } catch {
            return $false
        }
    }

    $candidates = New-Object System.Collections.ArrayList
    if ($env:MC_ART_PYTHON) { [void]$candidates.Add(@($env:MC_ART_PYTHON)) }
    [void]$candidates.Add(@('python'))
    [void]$candidates.Add(@('python3'))
    [void]$candidates.Add(@('py', '-3'))
    [void]$candidates.Add(@('py'))

    $python = $null
    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate[0] -ErrorAction SilentlyContinue)) { continue }
        if (Test-McArtPython -Candidate $candidate) { $python = $candidate; break }
    }

    if ($null -eq $python) {
        [Console]::Error.WriteLine("mc-art: no working Python found. Tried `$MC_ART_PYTHON, python, python3, py -3, py.")
        [Console]::Error.WriteLine("mc-art: each candidate must run -c `"print(1)`" and print 1. Set MC_ART_PYTHON to a real interpreter.")
        exit 127
    }

    & $python[0] @(Get-McArtRest $python) -m mc_art @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
