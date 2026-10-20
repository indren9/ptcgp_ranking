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
Check ([IO.File]::Exists((Join-Path $normal 'r35_intent.json')) -and [IO.File]::Exists((Join-Path $normal 'worker_launch.json')) -and [IO.File]::Exists((Join-Path $normal 'worker_exit.json')) -and [IO.File]::Exists((Join-Path $normal 'r35_attempt_link.json'))) 'DURABLE_INTENT_AND_WORKER_EVIDENCE'
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
# Reconcile a real finite fixture process, not an unrelated Python command.
$liveIntentPath=Join-Path $liveDir 'r35_intent.json'
$intentEnvelope=Get-Content -LiteralPath $liveIntentPath -Raw|ConvertFrom-Json
$intentValue=$intentEnvelope.payload_json|ConvertFrom-Json
$intentValue.synthetic_case='inactivity'
$intentEnvelope.payload_json=$intentValue|ConvertTo-Json -Depth 8 -Compress
$hash=[Security.Cryptography.SHA256]::Create()
try{
 $intentEnvelope.sha256=[BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$intentEnvelope.payload_json))).Replace('-','').ToLowerInvariant()
}finally{$hash.Dispose()}
[IO.File]::WriteAllText($liveIntentPath,($intentEnvelope|ConvertTo-Json -Depth 8 -Compress))
$fixture=Join-Path $base 'tests\fixtures\synthetic_worker.py'
$liveProcess=Start-Process -FilePath $python -ArgumentList @('-u',('"'+$fixture+'"'),'--case','inactivity') -PassThru -WindowStyle Hidden
try {
 $ci=Get-CimInstance Win32_Process -Filter ("ProcessId = "+$liveProcess.Id) -ErrorAction Stop
 if($null -eq $ci){throw 'R35_TEST_NO_SYNTHETIC_LIVE_PROCESS'}
 $liveLaunchPath=Join-Path $liveDir 'worker_launch.json'
 $liveLaunch=Get-Content -LiteralPath $liveLaunchPath -Raw|ConvertFrom-Json
 $liveLaunch.root_pid=[int]$liveProcess.Id
 $liveLaunch.root_creation_utc=([datetime]$ci.CreationDate).ToUniversalTime().ToString('o')
 $liveLaunch.arguments[3]='inactivity'
 [IO.File]::WriteAllText($liveLaunchPath,($liveLaunch|ConvertTo-Json -Depth 16))
 $active=& $supervisor -Mode RecoverReadOnly -EvidenceDir $liveDir
 $probeLine=[string]$ci.CommandLine
 $probePattern='(?i)--case"?\s+"?inactivity(?:"|\s|$)'
 Write-Output ('R35_LIVE_FLAGS timestamp='+((([datetime]$ci.CreationDate).ToUniversalTime().ToString('o')) -ceq $liveLaunch.root_creation_utc)+' exe='+([string]$ci.ExecutablePath -ieq [string]$intentValue.python_exe)+' fixture='+($probeLine.IndexOf($fixture,[StringComparison]::OrdinalIgnoreCase) -ge 0)+' case='+[regex]::IsMatch($probeLine,$probePattern)+' hasExited='+$liveProcess.HasExited)
 Write-Output ('R35_LIVE_PROBE status='+$active.status+' detail='+$active.detail)
 Check ($active.status -ceq 'WORKER_ACTIVE' -and -not $active.can_execute -and -not $liveProcess.HasExited) 'LIVE_WORKER_IDENTITY_MATCHES_NO_ACTION'
 $liveLaunch.root_creation_utc=([datetime]$ci.CreationDate).ToUniversalTime().AddMinutes(-1).ToString('o')
 [IO.File]::WriteAllText($liveLaunchPath,($liveLaunch|ConvertTo-Json -Depth 16))
 Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $liveDir).status -ceq 'EVIDENCE_DEGRADED') 'PID_REUSE_OR_CREATION_DRIFT_DENIED'
} finally {
 if($null -ne $liveProcess -and -not $liveProcess.HasExited){Stop-Process -Id $liveProcess.Id -Force -ErrorAction SilentlyContinue}
 $liveProcess.Dispose()
}
# R35-A: even when a link has correct hashes, missing/malformed/misordered
# process timestamps must never be accepted as terminal success.
function Copy-R35Bundle([string]$source,[string]$target) {
 [void][IO.Directory]::CreateDirectory($target)
 foreach($n in @('r35_intent.json','worker_launch.json','worker_exit.json','r35_attempt_link.json')){
  Copy-Item -LiteralPath (Join-Path $source $n) -Destination $target -ErrorAction Stop
 }
}
function Refresh-R35TestLink([string]$target) {
 $path=Join-Path $target 'r35_attempt_link.json'
 $link=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json
 $link.evidence_dir=$target
 $link.intent_sha256=(Get-FileHash -LiteralPath (Join-Path $target 'r35_intent.json') -Algorithm SHA256).Hash.ToLowerInvariant()
 $link.launch_sha256=(Get-FileHash -LiteralPath (Join-Path $target 'worker_launch.json') -Algorithm SHA256).Hash.ToLowerInvariant()
 $link.exit_sha256=(Get-FileHash -LiteralPath (Join-Path $target 'worker_exit.json') -Algorithm SHA256).Hash.ToLowerInvariant()
 [IO.File]::WriteAllText($path,($link|ConvertTo-Json -Compress))
}
$noCreated=Join-Path $work 'missing_creation'
Copy-R35Bundle $normal $noCreated
foreach($n in @('worker_launch.json','worker_exit.json')){
 $p=Join-Path $noCreated $n
 $v=Get-Content -LiteralPath $p -Raw|ConvertFrom-Json
 $v.root_creation_utc=$null
 [IO.File]::WriteAllText($p,($v|ConvertTo-Json -Depth 16))
}
Refresh-R35TestLink $noCreated
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $noCreated).status -ceq 'EVIDENCE_DEGRADED') 'R35A_NULL_CREATION_TIMESTAMP_FAIL_CLOSED'
$invalidCreated=Join-Path $work 'malformed_creation'
Copy-R35Bundle $normal $invalidCreated
foreach($n in @('worker_launch.json','worker_exit.json')){
 $p=Join-Path $invalidCreated $n
 $v=Get-Content -LiteralPath $p -Raw|ConvertFrom-Json
 $v.root_creation_utc='not-a-timestamp'
 [IO.File]::WriteAllText($p,($v|ConvertTo-Json -Depth 16))
}
Refresh-R35TestLink $invalidCreated
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $invalidCreated).status -ceq 'EVIDENCE_DEGRADED') 'R35A_MALFORMED_CREATION_FAIL_CLOSED'
$emptyCreated=Join-Path $work 'empty_creation'
Copy-R35Bundle $normal $emptyCreated
foreach($n in @('worker_launch.json','worker_exit.json')){
 $p=Join-Path $emptyCreated $n
 $v=Get-Content -LiteralPath $p -Raw|ConvertFrom-Json
 $v.root_creation_utc=''
 [IO.File]::WriteAllText($p,($v|ConvertTo-Json -Depth 16))
}
Refresh-R35TestLink $emptyCreated
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $emptyCreated).status -ceq 'EVIDENCE_DEGRADED') 'R35A_EMPTY_CREATION_FAIL_CLOSED'
$reversed=Join-Path $work 'reversed_intent_time'
Copy-R35Bundle $normal $reversed
$intentPath=Join-Path $reversed 'r35_intent.json'
$envelope=Get-Content -LiteralPath $intentPath -Raw|ConvertFrom-Json
$payload=$envelope.payload_json|ConvertFrom-Json
$payload.created_utc=[DateTime]::UtcNow.AddDays(1).ToString('o')
$envelope.payload_json=$payload|ConvertTo-Json -Depth 8 -Compress
$hasher=[Security.Cryptography.SHA256]::Create()
try{$envelope.sha256=[BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes([string]$envelope.payload_json))).Replace('-','').ToLowerInvariant()}
finally{$hasher.Dispose()}
[IO.File]::WriteAllText($intentPath,($envelope|ConvertTo-Json -Compress))
Refresh-R35TestLink $reversed
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $reversed).status -ceq 'EVIDENCE_DEGRADED') 'R35A_INTENT_AFTER_LAUNCH_REJECTED'
# R35-B: replacing either source evidence or the intent must invalidate the
# independent post-observation receipt (without accepting another run's files).
$other=Join-Path $work 'second_normal'
$otherOk=& $supervisor -Mode StartSynthetic -EvidenceDir $other -RepoRoot $repo -PythonExe $python -SyntheticCase normal
Check ($otherOk.status -ceq 'SYNTHETIC_SUCCESS_VERIFIED' -and $otherOk.intent_id -cne $success.intent_id) 'R35B_DISTINCT_ATTEMPT_IDENTITIES'
$otherIntent=Join-Path $work 'mixed_intent'
Copy-R35Bundle $other $otherIntent
Copy-Item -LiteralPath (Join-Path $normal 'r35_intent.json') -Destination (Join-Path $otherIntent 'r35_intent.json') -Force
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $otherIntent).status -ceq 'EVIDENCE_DEGRADED') 'R35B_FOREIGN_INTENT_REPLAY_REJECTED'
$otherWorkers=Join-Path $work 'mixed_workers'
Copy-R35Bundle $other $otherWorkers
Copy-Item -LiteralPath (Join-Path $normal 'worker_launch.json') -Destination (Join-Path $otherWorkers 'worker_launch.json') -Force
Copy-Item -LiteralPath (Join-Path $normal 'worker_exit.json') -Destination (Join-Path $otherWorkers 'worker_exit.json') -Force
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $otherWorkers).status -ceq 'EVIDENCE_DEGRADED') 'R35B_FOREIGN_LAUNCH_EXIT_REPLAY_REJECTED'
$unlinked=Join-Path $work 'missing_link'
Copy-R35Bundle $normal $unlinked
Remove-Item -LiteralPath (Join-Path $unlinked 'r35_attempt_link.json')
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $unlinked).status -ceq 'EVIDENCE_DEGRADED') 'R35B_MISSING_CORRELATION_REJECTED'
$copyAll=Join-Path $work 'copied_bundle'
Copy-R35Bundle $normal $copyAll
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir $copyAll).status -ceq 'EVIDENCE_DEGRADED') 'R35B_OTHER_EVIDENCE_DIRECTORY_REJECTED'
Check ((& $supervisor -Mode RecoverReadOnly -EvidenceDir (Join-Path $work 'does-not-exist')).status -ceq 'RECOVERY_REQUIRED') 'MISSING_EVIDENCE_UNKNOWN'
ExpectFail {& $supervisor -Mode AuthorizedRun -EvidenceDir (Join-Path $work 'prod') -RepoRoot $repo -PythonExe $python} 'PRODUCTION_MODE_UNAVAILABLE'
ExpectFail {& $supervisor -Mode StartSynthetic -EvidenceDir (Join-Path $repo 'outputs\fake') -RepoRoot $repo -PythonExe $python} 'REPO_WRITE_PATH_REJECTED'
Check (-not [IO.Directory]::Exists((Join-Path $repo 'outputs'))) 'NO_CANONICAL_OUTPUTS'
Write-Output ('R35_TEST_FINAL_PASS='+$checks)
Write-Output ('R35_TEST_TEMP='+$work)
