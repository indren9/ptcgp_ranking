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
if(-not [IO.File]::Exists((Join-Path $evidence 'capture_runs.jsonl'))){throw 'CAPTURE_REQUEST_NOT_RECORDED'}
if([IO.Directory]::Exists((Join-Path $repo 'outputs'))){throw 'UNEXPECTED_CANONICAL_OUTPUT'}
Write-Output 'TEST_PASS CONTROLLED_TERMINATION_EMPTY_JOB'
Write-Output 'TEST_PASS CAPTURE_DIAGNOSTIC_EVIDENCE'
Write-Output 'TEST_PASS NO_CANONICAL_OUTPUT'
Write-Output ('EVIDENCE_ROOT='+$evidence)