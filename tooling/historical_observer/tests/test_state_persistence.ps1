$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$base=Split-Path -Parent $PSScriptRoot
. (Join-Path $base 'StatePersistence.ps1')
$work=Join-Path ([IO.Path]::GetTempPath()) ('mars-r33a-windows-'+[guid]::NewGuid().ToString('N'))
if([IO.Directory]::Exists($work)){throw 'STOP: test outputs already exist'}
[void][IO.Directory]::CreateDirectory($work)
$passed=[System.Collections.Generic.List[string]]::new()
function Assert([bool]$condition,[string]$name){
 if(-not $condition){throw ('UNIT_FAILURE '+$name)}
 $script:passed.Add($name);Write-Output ('UNIT_PASS '+$name)
}
$launch=Join-Path $work 'worker_launch.json'
$rec=[ordered]@{schema='synthetic-test';status='STARTING';start_utc='2026-10-19T00:00:00Z';id='A'}
$a=Write-R33StateJson $launch $rec
Assert ($a.mode -eq 'canonical' -and $a.verified) 'CANONICAL_MODE'
Assert ([IO.File]::Exists($launch)) 'CANONICAL_EXISTS'
Assert ((Get-Content -Raw -LiteralPath $launch|ConvertFrom-Json).status -ceq 'STARTING') 'CANONICAL_JSON'
$initial=[IO.File]::ReadAllBytes($launch)
$b=Write-R33StateJson $launch ([ordered]@{status='COMPLETED';id='B'})
Assert ($b.mode -eq 'recovery' -and $b.verified) 'IMMUTABLE_COLLISION_SPILL'
Assert ([Convert]::ToBase64String([IO.File]::ReadAllBytes($launch)) -ceq [Convert]::ToBase64String($initial)) 'COLLISION_NEVER_OVERWRITES'
$recover=Get-Content -Raw -LiteralPath $b.path|ConvertFrom-Json
Assert ($recover.status -ceq 'OBSERVER_EVIDENCE_DEGRADED') 'RECOVERY_NOT_FALSE_PASS'
Assert ($recover.worker_status_before_evidence_degradation -ceq 'COMPLETED') 'RECOVERY_PRESERVES_WORKER_STATUS'
Assert ($recover.persistence_recovery.canonical_path -ceq $launch) 'RECOVERY_CANONICAL_REFERENCE'
Assert (@(Get-ChildItem -LiteralPath $work -File -Filter '*.staging' -Force).Count -eq 0) 'NO_ORPHAN_STAGING_AFTER_SUCCESS'
$lockpath=Join-Path $work 'worker_exit.json'
[IO.File]::WriteAllText($lockpath,'{"reserved":true}',[Text.UTF8Encoding]::new($false))
$locked=[IO.FileStream]::new($lockpath,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
try{$c=Write-R33StateJson $lockpath ([ordered]@{status='COMPLETED';code=0})}finally{$locked.Dispose()}
Assert ($c.mode -eq 'recovery') 'LOCKED_EXISTING_TARGET_RECOVERED'
Assert ([IO.File]::ReadAllText($lockpath) -ceq '{"reserved":true}') 'LOCKED_TARGET_UNTOUCHED'
$thrown=$false
try{[void](Write-R33StateJson (Join-Path $work 'other.json') ([ordered]@{status='VALID'}))}catch{$thrown=$true}
Assert $thrown 'UNSUPPORTED_NAME_FAIL_CLOSED'
$thrown=$false
try{[void](Write-R33StateJson 'worker_exit.json' ([ordered]@{status='VALID'}))}catch{$thrown=$true}
Assert $thrown 'RELATIVE_PATH_REJECTED'
$noDir=Join-Path $work 'nonexistent\worker_exit.json'
$thrown=$false
try{[void](Write-R33StateJson $noDir ([ordered]@{status='VALID'}))}catch{$thrown=$true}
Assert $thrown 'MISSING_EVIDENCE_DIR_FAIL_CLOSED'
$broken=Join-Path $work 'blocked_recovery'
[void][IO.Directory]::CreateDirectory($broken)
$blocking=Join-Path $broken 'worker_exit.json'
[IO.File]::WriteAllText($blocking,'{"reserved":true}',[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $broken 'state_recovery'),'blocking filename',[Text.UTF8Encoding]::new($false))
$thrown=$false
try{[void](Write-R33StateJson $blocking ([ordered]@{status='COMPLETED'}))}catch{$thrown=$true}
Assert $thrown 'RECOVERY_IO_FAILURE_NON_SUCCESS'
Assert ([IO.File]::ReadAllText($blocking) -ceq '{"reserved":true}') 'RECOVERY_FAILURE_CANONICAL_UNTOUCHED'
Assert (@(Get-ChildItem -LiteralPath $broken -File -Filter '*.staging' -Force).Count -ge 1) 'FAILED_RECOVERY_STAGING_PRESERVED'
$out=[ordered]@{schema='r33a-unit-test-v1';status='PASS';unit_pass=$passed.Count;tests=@($passed);source='synthetic_only';no_prod=$true}
$report=Join-Path $work 'unit_state_result_v2.json'
[IO.File]::WriteAllText($report,($out|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
Write-Output ('UNIT_ALL_PASS='+$passed.Count)