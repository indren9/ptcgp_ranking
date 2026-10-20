# R3.2: observe the whole owned worker Job Object; no global production timeout.
[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][ValidateSet('Synthetic','InspectRun')][string]$Mode,
 [string[]]$WorkerArguments,
 [string]$EvidenceDir,
 [string]$RunScopePath,
 [Parameter(Mandatory=$true)][string]$RepoRoot,
 [Parameter(Mandatory=$true)][string]$PythonExe,
 [ValidateSet('normal','nonzero','child','inactivity')][string]$SyntheticCase='normal',
 [ValidateRange(0,120)][int]$KillSyntheticAtSec=0,
 [ValidateRange(1,30)][int]$SampleIntervalSec=2
)
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$base=$PSScriptRoot
if(-not [IO.Path]::IsPathRooted($RepoRoot) -or -not [IO.Directory]::Exists($RepoRoot)){throw 'R33 PORTABLE DENIED: absolute existing work directory required'}
$repo=[IO.Path]::GetFullPath($RepoRoot).TrimEnd('\')
if(-not [IO.Path]::IsPathRooted($PythonExe) -or -not [IO.File]::Exists($PythonExe)){throw 'R33 PORTABLE DENIED: absolute existing Python executable required'}
$python=[IO.Path]::GetFullPath($PythonExe)
if(-not [IO.File]::Exists($python)){throw 'R3.2: pinned Python missing'}
if($Mode -eq 'InspectRun'){
 # Read-only run-scoped guard; exits BEFORE creating evidence or worker processes.
 . (Join-Path $base 'RunScopeGuard.ps1')
 Test-R33CRunScope -RunScopePath $RunScopePath -RepoRoot $repo -PythonExe $python
 return
}
if($Mode -cne 'Synthetic'){throw 'R3.3-C: productive worker modes unavailable in this candidate'}
if([string]::IsNullOrWhiteSpace($EvidenceDir) -or -not [IO.Path]::IsPathRooted($EvidenceDir)){throw 'R3.3-C: synthetic evidence must use an absolute path'}
if($null -ne $WorkerArguments -and @($WorkerArguments).Count -gt 0){throw 'R33 PORTABLE DENIED: caller cannot supply executable arguments'}
 $fixture=Join-Path $base 'tests\fixtures\synthetic_worker.py'
 if(-not [IO.File]::Exists($fixture)){throw 'R33 PORTABLE DENIED: missing fixed synthetic fixture'}
 $WorkerArguments=@('-u',$fixture,'--case',$SyntheticCase)
if(-not [string]::IsNullOrWhiteSpace($RunScopePath)){throw 'R3.3-C: run scope not accepted for synthetic workers'}
$rootWithSlash=$repo.TrimEnd('\')+'\'
$fullEvidence=[IO.Path]::GetFullPath($EvidenceDir)
if($fullEvidence -ieq $repo -or $fullEvidence.StartsWith($rootWithSlash,[StringComparison]::OrdinalIgnoreCase)){
 throw 'R33 PORTABLE DENIED: evidence must remain outside the supplied repository root'
}
[void][IO.Directory]::CreateDirectory($fullEvidence)
$EvidenceDir=$fullEvidence
if(-not ('MarsR31Native' -as [type])){Add-Type -Path (Join-Path $base 'NativeNoPipe.cs')}
. (Join-Path $base 'TreeWatch.ps1')
. (Join-Path $base 'StatePersistence.ps1')
$out=Join-Path $EvidenceDir 'stdout.log'
$err=Join-Path $EvidenceDir 'stderr_importtime.log'
$runner=[MarsR31Native]::new();$registry=@{}
$started=[DateTime]::UtcNow
$rec=[ordered]@{
 schema='mars-r3.3a-worker-v1';start_utc=$started.ToString('o')
 observer_pid=$PID;root_pid=$null;root_ppid=$null;root_creation_utc=$null
 executable=$python;cwd=$repo;arguments=$WorkerArguments;stdout=$out;stderr=$err
 mode=$Mode;job_limit_kill_on_close=$false;global_timeout=$null
 sample_interval_sec=$SampleIntervalSec;frames=0;max_seen=0;telemetry_spills=0
 evidence_degraded=$false;launch_state_persistence=$null;worker_status_before_evidence_degradation=$null
 capture_events=@();synthetic_kill_requested=$KillSyntheticAtSec
 synthetic_terminated=$false;exit_code=$null;root_exited=$false
 job_empty=$false;final_job_pids=@();status='STARTING';error=$null
}
$rootExited=$false;$lastCpu=$null;$lastOut=0;$idleFrames=0;$captureSyntheticDone=$false
try{
 $runner.Start($python,[string[]]$WorkerArguments,$repo,'NUL',$out,$err,$false)
 $rec.root_pid=$runner.Pid
 $idn=Get-R32Identity $runner.Pid
 if($null -ne $idn){$rec.root_ppid=$idn.ppid;$rec.root_creation_utc=$idn.created_utc}
 $launchSaved=Write-R33StateJson (Join-Path $EvidenceDir 'worker_launch.json') $rec
 $rec.launch_state_persistence=([ordered]@{mode=$launchSaved.mode;path=$launchSaved.path;sha256=$launchSaved.sha256})
 if($launchSaved.mode -eq 'recovery'){$rec.evidence_degraded=$true}
 while($true){
  $frame=Get-R32Frame $runner $registry $runner.Pid $EvidenceDir
  $rec.frames++
  $rec.max_seen=[Math]::Max([int]$rec.max_seen,$registry.Count)
  $elapsed=([DateTime]::UtcNow-$started).TotalSeconds
  $rootExited=($runner.Wait([uint32]0) -eq 'EXITED')
  $rec.root_exited=$rootExited
  $cpu=0.0
  foreach($p in @($frame.processes)){if($null -ne $p.cpu_seconds){$cpu+=[double]$p.cpu_seconds}}
  $bytes=if([IO.File]::Exists($out)){([IO.FileInfo]::new($out)).Length}else{0}
  $progress=($null -eq $lastCpu -or $cpu -gt ($lastCpu+0.0001) -or $bytes -gt $lastOut)
  $idleFrames=if($progress){0}else{$idleFrames+1}
  $state=if($frame.job_pids.Count -eq 0 -and $rootExited){'COMPLETED'}elseif($progress){'ACTIVE_PROGRESS_OBSERVED'}elseif($idleFrames -ge 3){'SUSPECTED_INACTIVITY_NOT_CONFIRMED'}else{'APPARENTLY_IDLE'}
  $obs=[ordered]@{utc=[DateTime]::UtcNow.ToString('o');root_pid=$runner.Pid;root_exited=$rootExited;active_pids=$frame.job_pids;cpu_total=$cpu;stdout_bytes=$bytes;inactivity_samples=$idleFrames;classification=$state}
  $writeResult=Write-R32JsonLine (Join-Path $EvidenceDir 'worker_observation.jsonl') ($obs|ConvertTo-Json -Compress)
  if($writeResult.target -eq 'spill'){$rec.telemetry_spills++}
  $lastCpu=$cpu;$lastOut=$bytes
  if($state -eq 'COMPLETED'){$rec.status='COMPLETED';break}
  $request=Join-Path $EvidenceDir 'capture.request'
  $syntheticCapture=($Mode -eq 'Synthetic' -and $KillSyntheticAtSec -gt 0 -and $elapsed -ge 3 -and -not $captureSyntheticDone)
  if($syntheticCapture -or [IO.File]::Exists($request)){
   $cap=Invoke-R32OwnedCapture $runner $registry $EvidenceDir $repo
   $rec.capture_events+=([ordered]@{utc=[DateTime]::UtcNow.ToString('o');cause=$(if($syntheticCapture){'authorized_synthetic'}else{'operator_request'});result=$cap})
   if($syntheticCapture){$captureSyntheticDone=$true}
   if([IO.File]::Exists($request)){
    Move-Item -LiteralPath $request -Destination (Join-Path $EvidenceDir ('capture.handled.'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ'))) -ErrorAction Stop
   }
  }
  if($Mode -eq 'Synthetic' -and $KillSyntheticAtSec -gt 0 -and $elapsed -ge $KillSyntheticAtSec -and $captureSyntheticDone){
   $rec.capture_before_termination=[DateTime]::UtcNow.ToString('o')
   $runner.KillOwnedTree()
   $rec.synthetic_terminated=$true
   [void]$runner.Wait([uint32]5000)
   $rec.status='SYNTHETIC_TERMINATED'
   break
  }
  [Threading.Thread]::Sleep($SampleIntervalSec*1000)
 }
 $rec.root_exited=($runner.Wait([uint32]0) -eq 'EXITED')
 if($rec.root_exited){$rec.exit_code=$runner.ExitCode()}
 $rec.final_job_pids=@($runner.ActivePids())
 $rec.job_empty=($rec.final_job_pids.Count -eq 0)
 if($rec.status -eq 'COMPLETED' -and ($rec.exit_code -ne 0 -or -not $rec.job_empty)){$rec.status='WORKER_NONZERO_OR_RESIDUAL'}
 if($rec.status -eq 'SYNTHETIC_TERMINATED' -and -not $rec.job_empty){$rec.status='SYNTHETIC_RESIDUAL'}
}catch{
 $rec.error=$_.Exception.ToString();$rec.status='OBSERVER_ERROR'
 # Intentionally NO automatic production termination or retry.
 if($Mode -eq 'Synthetic' -and $null -ne $rec.root_pid){
  try{
   if(@($runner.ActivePids()).Count -gt 0){
    $cap=Invoke-R32OwnedCapture $runner $registry $EvidenceDir $repo
    $rec.capture_events+=([ordered]@{utc=[DateTime]::UtcNow.ToString('o');cause='synthetic_error_recovery';result=$cap})
    $rec.capture_before_termination=[DateTime]::UtcNow.ToString('o')
    $runner.KillOwnedTree();$rec.synthetic_terminated=$true
   }
  }catch{$rec.error+=('; synthetic recovery: '+$_.Exception.Message)}
 }
}finally{
 $rec.end_utc=[DateTime]::UtcNow.ToString('o')
 $rec.final_job_pids=@($runner.ActivePids())
 $rec.job_empty=($rec.final_job_pids.Count -eq 0)
 $rec.registry=@($registry.Values)
 $finalPersistenceError=$null
 try{
  if($rec.evidence_degraded){
   $rec.worker_status_before_evidence_degradation=$rec.status
   $rec.status='OBSERVER_EVIDENCE_DEGRADED'
  }
  $exitSaved=Write-R33StateJson (Join-Path $EvidenceDir 'worker_exit.json') $rec
  if($exitSaved.mode -eq 'recovery'){
   if(-not $rec.evidence_degraded){$rec.worker_status_before_evidence_degradation=$rec.status}
   $rec.status='OBSERVER_EVIDENCE_DEGRADED'
   $rec.evidence_degraded=$true
  }
 }catch{
  $finalPersistenceError=$_.Exception.ToString()
  $rec.status='OBSERVER_EVIDENCE_PERSISTENCE_ERROR'
  $rec.error=($rec.error+'; final evidence: '+$finalPersistenceError)
 }finally{$runner.Dispose()}
 if($null -ne $finalPersistenceError){throw ('R3.3-A durable state failure: '+$finalPersistenceError)}
}
[pscustomobject]$rec
if($rec.status -notin @('COMPLETED','SYNTHETIC_TERMINATED')){
 throw ('R3.3-A observer status: '+$rec.status+'; '+$EvidenceDir)
}
