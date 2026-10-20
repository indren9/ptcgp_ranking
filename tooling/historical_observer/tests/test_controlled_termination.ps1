$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$base=Split-Path -Parent $PSScriptRoot
$python=if($env:MARS_TEST_PYTHON){$env:MARS_TEST_PYTHON}else{(Get-Command python -ErrorAction Stop).Source}
$work=Join-Path ([IO.Path]::GetTempPath()) ('MARS R33 termination '+[guid]::NewGuid().ToString('N'))
$repo=Join-Path $work 'isolated repo with spaces'
$evidence=Join-Path $work 'synthetic evidence'
[void][IO.Directory]::CreateDirectory($repo)
$r=& (Join-Path $base 'Invoke-WorkerObserved.ps1') -Mode Synthetic -RepoRoot $repo -PythonExe $python -SyntheticCase inactivity -KillSyntheticAtSec 3 -EvidenceDir $evidence -SampleIntervalSec 1
$x=Get-Content -LiteralPath (Join-Path $evidence 'worker_exit.json') -Raw|ConvertFrom-Json
if($r.status -cne 'SYNTHETIC_TERMINATED' -or -not $r.job_empty -or $x.status -cne 'SYNTHETIC_TERMINATED' -or -not $x.synthetic_terminated){throw 'TERMINATION_NOT_RECORDED'}
$capturesPath=Join-Path $evidence 'capture_runs.jsonl'
if(-not [IO.File]::Exists($capturesPath)){throw 'CAPTURE_REQUEST_NOT_RECORDED'}
$captures=@(Get-Content -LiteralPath $capturesPath|ForEach-Object{$_|ConvertFrom-Json})
if($captures.Count -lt 1 -or @($captures|Where-Object{$_.count -ge 1}).Count -lt 1){throw 'CAPTURE_RESULTS_MISSING'}
$successful=@($captures|ForEach-Object{@($_.results)}|Where-Object{$_.success -eq $true -and $_.dump_success -eq $true -and $_.dump_bytes -gt 0 -and $_.wct_success_count -gt 0})
if($successful.Count -lt 1){throw 'CAPTURE_WCT_OR_MINIDUMP_NOT_SUCCESSFUL'}
foreach($item in $successful){
 if(-not [IO.File]::Exists([string]$item.dump_path) -or ([IO.FileInfo]::new([string]$item.dump_path)).Length -ne [long]$item.dump_bytes){
  throw 'CAPTURE_MINIDUMP_READBACK_MISMATCH'
 }
}
if([IO.Directory]::Exists((Join-Path $repo 'outputs'))){throw 'UNEXPECTED_CANONICAL_OUTPUT'}
Write-Output 'TEST_PASS CONTROLLED_TERMINATION_EMPTY_JOB'
Write-Output 'TEST_PASS CAPTURE_VALID_WCT_AND_MINIDUMP'
Write-Output 'TEST_PASS CAPTURE_MINIDUMP_READBACK'
Write-Output 'TEST_PASS NO_CANONICAL_OUTPUT'
Write-Output ('EVIDENCE_ROOT='+$evidence)