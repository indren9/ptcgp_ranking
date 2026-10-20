# R3.5 synthetic-only regression, no historical_rebuild or network.
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$base=Split-Path -Parent $PSScriptRoot
$supervisor=Join-Path $base 'Invoke-R35SyntheticSupervisor.ps1'
$python=if($env:MARS_TEST_PYTHON){$env:MARS_TEST_PYTHON}else{(Get-Command python -ErrorAction Stop).Source}
$work=Join-Path ([IO.Path]::GetTempPath()) ('MARS R35 QA '+[guid]::NewGuid().ToString('N'))
$repo=Join-Path $work 'synthetic repo'
[void][IO.Directory]::CreateDirectory($repo)
$checks=0
function Check([bool]$value,[string]$name){
 if(-not $value){throw ('R35_TEST_FAILURE '+$name)}
 $script:checks++
 Write-Output ('R35_TEST_PASS '+$name)
}
function ExpectFail([scriptblock]$action,[string]$name){
 $failed=$false
 try {& $action | Out-Null} catch {$failed=$true}
 Check $failed $name
}
$normal=Join-Path $work 'normal evidence'
$success=& $supervisor -Mode StartSynthetic -EvidenceDir $normal -RepoRoot $repo -PythonExe $python -SyntheticCase normal
Check ($success.status -ceq 'SYNTHETIC_SUCCESS_VERIFIED' -and -not $success.production_success -and -not $success.can_execute) 'SYNTHETIC_ONLY_SUCCESS'
Check ([IO.File]::Exists((Join-Path $normal 'r35_intent.json')) -and [IO.File]::Exists((Join-Path $normal 'worker_launch.json')) -and [IO.File]::Exists((Join-Path $normal 'worker_exit.json'))) 'DURABLE_INTENT_AND_WORKER_EVIDENCE'
$envl=Get-Content -LiteralPath (Join-Path $normal 'r35_intent.json') -Raw | ConvertFrom-Json
$intent=$envl.payload_json|ConvertFrom-Json
$launch=Get-Content -LiteralPath (Join-Path $normal 'worker_launch.json') -Raw|ConvertFrom-Json
Check (([datetime]$intent.created_utc) -le ([datetime]$launch.start_utc) -and $intent.production_authorization -eq $false) 'INTENT_RECORDED_BEFORE_OBSERVER_START'
$before=@(Get-ChildItem -LiteralPath $normal -File -Recurse|Sort-Object FullName|ForEach-Object{$_.FullName+'='+((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)})
$read=& $supervisor -Mode RecoverReadOnly -EvidenceDir $normal
$after=@(Get-ChildItem -LiteralPath $normal -File -Recurse|Sort-Object FullName|ForEach-Object{$_.FullName+'='+((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)})
Check ($read.status -ceq $success.status -and (@($before) -join ';') -ceq (@($after) -join ';')) 'RECOVERY_ZERO_WRITES'
ExpectFail {& $supervisor -Mode StartSynthetic -EvidenceDir $normal -RepoRoot $repo -PythonExe $python} 'NO_DUPLICATE_AFTER_SUCCESS'
$child=Join-Path $work 'child evidence'
$c=& $supervisor -Mode StartSynthetic -EvidenceDir $child -RepoRoot $repo -PythonExe $python -SyntheticCase child
Check ($c.status -ceq 'SYNTHETIC_SUCCESS_VERIFIED') 'JOB_CHILD_DRAINED'
$failedDir=Join-Path $work 'nonzero evidence'
ExpectFail {& $supervisor -Mode StartSynthetic -EvidenceDir $failedDir -RepoRoot $repo -PythonExe $python -SyntheticCase nonzero} 'NONZERO_NOT_SUCCESS'
$f=& $supervisor -Mode RecoverReadOnly -EvidenceDir $failedDir
Check ($f.status -ceq 'SYNTHETIC_FAILED' -and -not $f.may_retry_automatically) 'NONZERO_FAIL_CLOSED'
ExpectFail {& $supervisor -Mode StartSynthetic -EvidenceDir $failedDir -RepoRoot $repo -PythonExe $python} 'NO_AUTORETRY_FAILURE'
$intentOnly=Join-Path $work 'intent_only'
[void][IO.Directory]::CreateDirectory($intentOnly)
Copy-Item -LiteralPath (Join-Path $normal 'r35_intent.json') -Destination $intentOnly
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $intentOnly).status -ceq 'RECOVERY_REQUIRED') 'INTENT_ONLY_IS_UNKNOWN'
ExpectFail {& $supervisor -Mode StartSynthetic -EvidenceDir $intentOnly -RepoRoot $repo -PythonExe $python} 'UNKNOWN_ATTEMPT_NO_RELAUNCH'
$missingExit=Join-Path $work 'missing_exit'
[void][IO.Directory]::CreateDirectory($missingExit)
foreach($n in @('r35_intent.json','worker_launch.json')){Copy-Item -LiteralPath (Join-Path $normal $n) -Destination $missingExit}
$partial=& $supervisor -Mode RecoverReadOnly -EvidenceDir $missingExit
Check ($partial.status -ceq 'RECOVERY_REQUIRED' -and -not $partial.production_success) 'EXIT_LOSS_NEVER_SUCCESS'
$badIntent=Join-Path $work 'corrupt_intent'
[void][IO.Directory]::CreateDirectory($badIntent)
[IO.File]::WriteAllText((Join-Path $badIntent 'r35_intent.json'),'{"schema":"tampered"}')
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $badIntent).status -ceq 'EVIDENCE_DEGRADED') 'CORRUPT_INTENT_FAIL_CLOSED'
$badExit=Join-Path $work 'bad_exit'
[void][IO.Directory]::CreateDirectory($badExit)
foreach($n in @('r35_intent.json','worker_launch.json','worker_exit.json')){Copy-Item -LiteralPath (Join-Path $normal $n) -Destination $badExit}
$path=Join-Path $badExit 'worker_exit.json'
$e=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
$e.root_pid=[int]$e.root_pid+1000
[IO.File]::WriteAllText($path,($e|ConvertTo-Json -Depth 16))
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $badExit).status -ceq 'EVIDENCE_DEGRADED') 'MISMATCHED_PROCESS_ID_FAIL_CLOSED'
# An independently running, owned synthetic Python process must never be relaunched or killed by recovery.
$liveDir=Join-Path $work 'live_process'
[void][IO.Directory]::CreateDirectory($liveDir)
foreach($n in @('r35_intent.json','worker_launch.json')){Copy-Item -LiteralPath (Join-Path $normal $n) -Destination $liveDir}
$liveProcess=Start-Process -FilePath $python -ArgumentList '-u -c "import time;time.sleep(15)"' -PassThru -WindowStyle Hidden
try {
 $ci=Get-CimInstance Win32_Process -Filter ("ProcessId = "+$liveProcess.Id) -ErrorAction Stop
 if($null -eq $ci){throw 'R35_TEST_NO_SYNTHETIC_LIVE_PROCESS'}
 $liveLaunchPath=Join-Path $liveDir 'worker_launch.json'
 $liveLaunch=Get-Content -LiteralPath $liveLaunchPath -Raw|ConvertFrom-Json
 $liveLaunch.root_pid=[int]$liveProcess.Id
 $liveLaunch.root_creation_utc=([datetime]$ci.CreationDate).ToUniversalTime().ToString('o')
 [IO.File]::WriteAllText($liveLaunchPath,($liveLaunch|ConvertTo-Json -Depth 16))
 $active=& $supervisor -Mode RecoverReadOnly -EvidenceDir $liveDir
 Check ($active.status -ceq 'WORKER_ACTIVE' -and -not $active.can_execute -and -not $liveProcess.HasExited) 'LIVE_WORKER_IDENTITY_MATCHES_NO_ACTION'
 $liveLaunch.root_creation_utc=([datetime]$ci.CreationDate).ToUniversalTime().AddMinutes(-1).ToString('o')
 [IO.File]::WriteAllText($liveLaunchPath,($liveLaunch|ConvertTo-Json -Depth 16))
 Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $liveDir).status -ceq 'RECOVERY_REQUIRED') 'PID_REUSE_OR_CREATION_DRIFT_DENIED'
} finally {
 if($null -ne $liveProcess -and -not $liveProcess.HasExited){Stop-Process -Id $liveProcess.Id -Force -ErrorAction SilentlyContinue}
 $liveProcess.Dispose()
}
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir (Join-Path $work 'does-not-exist')).status -ceq 'RECOVERY_REQUIRED') 'MISSING_EVIDENCE_UNKNOWN'
ExpectFail {& $supervisor -Mode AuthorizedRun -EvidenceDir (Join-Path $work 'prod') -RepoRoot $repo -PythonExe $python} 'PRODUCTION_MODE_UNAVAILABLE'
ExpectFail {& $supervisor -Mode StartSynthetic -EvidenceDir (Join-Path $repo 'outputs\fake') -RepoRoot $repo -PythonExe $python} 'REPO_WRITE_PATH_REJECTED'
Check (-not [IO.Directory]::Exists((Join-Path $repo 'outputs'))) 'NO_CANONICAL_OUTPUTS'
Write-Output ('R35_TEST_FINAL_PASS='+$checks)
Write-Output ('R35_TEST_TEMP='+$work)
