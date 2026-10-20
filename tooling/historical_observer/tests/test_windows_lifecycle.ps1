$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$base=Split-Path -Parent $PSScriptRoot
$obs=Join-Path $base 'Invoke-WorkerObserved.ps1'
$python=if(-not [string]::IsNullOrWhiteSpace($env:MARS_TEST_PYTHON)){$env:MARS_TEST_PYTHON}else{(Get-Command python -ErrorAction Stop).Source}
if(-not [IO.File]::Exists($python)){throw 'TEST_PYTHON_MISSING'}
$work=Join-Path ([IO.Path]::GetTempPath()) ('MARS R33 lifecycle '+[guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($work)
$repo=Join-Path $work 'synthetic workspace'
[void][IO.Directory]::CreateDirectory($repo)
$checks=[System.Collections.Generic.List[string]]::new()
function Check([bool]$valid,[string]$name){
 if(-not $valid){throw ('LIFECYCLE_TEST_FAILURE '+$name)}
 $script:checks.Add($name)
 Write-Output ('TEST_PASS '+$name)
}
function ExpectFail([scriptblock]$call,[string]$name){
 $rejected=$false
 try{& $call|Out-Null}catch{$rejected=$true}
 Check $rejected $name
}
$evidence=Join-Path $work 'normal'
$r=& $obs -Mode Synthetic -RepoRoot $repo -PythonExe $python -SyntheticCase normal -EvidenceDir $evidence -SampleIntervalSec 1
$j=Get-Content -LiteralPath (Join-Path $evidence 'worker_exit.json') -Raw|ConvertFrom-Json
Check ($r.status -ceq 'COMPLETED' -and $r.exit_code -eq 0 -and $r.job_empty -and -not $r.evidence_degraded) 'EXIT0_COMPLETED'
Check ($j.status -ceq 'COMPLETED' -and $j.exit_code -eq 0 -and $j.cwd -ceq $repo) 'NORMAL_EXIT_STATE_AND_CUSTOM_ROOT'
Check ([IO.File]::Exists((Join-Path $evidence 'worker_launch.json')) -and [IO.File]::Exists((Join-Path $evidence 'tree_frames.jsonl'))) 'NORMAL_EVIDENCE_CREATED'
Check ((Get-Content -LiteralPath (Join-Path $evidence 'stdout.log') -Raw) -match 'SYNTHETIC_NORMAL') 'NORMAL_WORKER_OUTPUT'
$evidence=Join-Path $work 'exit7'
ExpectFail {& $obs -Mode Synthetic -RepoRoot $repo -PythonExe $python -SyntheticCase nonzero -EvidenceDir $evidence -SampleIntervalSec 1} 'NONZERO_EXIT_NOT_SUCCESS'
$j=Get-Content -LiteralPath (Join-Path $evidence 'worker_exit.json') -Raw|ConvertFrom-Json
Check ($j.exit_code -eq 7 -and $j.status -ceq 'WORKER_NONZERO_OR_RESIDUAL' -and $j.job_empty) 'NONZERO_EXIT_PRESERVED'
$evidence=Join-Path $work 'child'
$r=& $obs -Mode Synthetic -RepoRoot $repo -PythonExe $python -SyntheticCase child -EvidenceDir $evidence -SampleIntervalSec 1
$j=Get-Content -LiteralPath (Join-Path $evidence 'worker_exit.json') -Raw|ConvertFrom-Json
Check ($r.status -ceq 'COMPLETED' -and $j.exit_code -eq 0 -and $j.job_empty) 'CHILD_JOB_DRAINED'
$frames=@(Get-Content -LiteralPath (Join-Path $evidence 'tree_frames.jsonl')|ForEach-Object{$_|ConvertFrom-Json})
Check (@($frames|Where-Object{@($_.job_pids).Count -ge 2}).Count -ge 1) 'OWNED_CHILD_OBSERVED_IN_JOB'
$log=Get-Content -LiteralPath (Join-Path $evidence 'stdout.log') -Raw
Check ($log -match 'SYNTHETIC_CHILD_PID=') 'CHILD_PID_REPORTED'
$childPid=[int]([regex]::Match($log,'SYNTHETIC_CHILD_PID=(\d+)').Groups[1].Value)
Check ($null -eq (Get-Process -Id $childPid -ErrorAction SilentlyContinue)) 'CHILD_NO_ORPHAN'
$evidence=Join-Path $work 'collision'
[void][IO.Directory]::CreateDirectory($evidence)
$marker='{"synthetic_existing":true}'
[IO.File]::WriteAllText((Join-Path $evidence 'worker_launch.json'),$marker,[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $evidence 'worker_exit.json'),$marker,[Text.UTF8Encoding]::new($false))
ExpectFail {& $obs -Mode Synthetic -RepoRoot $repo -PythonExe $python -SyntheticCase normal -EvidenceDir $evidence -SampleIntervalSec 1} 'COLLISION_NOT_SUCCESS'
Check ([IO.File]::ReadAllText((Join-Path $evidence 'worker_launch.json')) -ceq $marker -and [IO.File]::ReadAllText((Join-Path $evidence 'worker_exit.json')) -ceq $marker) 'COLLISION_CANONICAL_IMMUTABLE'
$recovered=@(Get-ChildItem -LiteralPath (Join-Path $evidence 'state_recovery\worker_exit.json') -File)
Check ($recovered.Count -eq 1) 'COLLISION_RECOVERY_SINGLE'
$recover=Get-Content -LiteralPath $recovered[0].FullName -Raw|ConvertFrom-Json
Check ($recover.status -ceq 'OBSERVER_EVIDENCE_DEGRADED' -and $recover.worker_status_before_evidence_degradation -ceq 'COMPLETED' -and $recover.exit_code -eq 0) 'COLLISION_RECOVERS_REAL_WORKER_OUTCOME'
ExpectFail {& $obs -Mode AuthorizedRun -RepoRoot $repo -PythonExe $python -EvidenceDir (Join-Path $work 'not_created')} 'PRODUCTIVE_MODE_NOT_SUPPORTED'
ExpectFail {& $obs -Mode Synthetic -RepoRoot $repo -PythonExe $python -EvidenceDir (Join-Path $work 'not_created_2') -WorkerArguments @('-m','historical_rebuild')} 'ARBITRARY_WORKER_ARGS_REJECTED'
ExpectFail {& $obs -Mode Synthetic -RepoRoot $repo -PythonExe $python -EvidenceDir (Join-Path $repo 'outputs\fake')} 'CANONICAL_REPO_EVIDENCE_REJECTED'
ExpectFail {& $obs -Mode Synthetic -RepoRoot 'relative' -PythonExe $python -EvidenceDir (Join-Path $work 'bad_root')} 'NONABSOLUTE_REPO_REJECTED'
Check (-not [IO.Directory]::Exists((Join-Path $repo 'outputs'))) 'NO_OUTPUTS_IN_WORKSPACE'
$report=[ordered]@{schema='mars-r33-portable-lifecycle-v1';status='PASS';assertions=$checks.Count;labels=@($checks);productive_launches=0;root=$work}
[IO.File]::WriteAllText((Join-Path $work 'test_report.json'),($report|ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
Write-Output ('TEST_FINAL_PASS='+$checks.Count)
Write-Output ('EVIDENCE_ROOT='+$work)