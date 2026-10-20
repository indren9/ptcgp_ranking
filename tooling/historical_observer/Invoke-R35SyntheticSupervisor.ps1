# R3.5 — Synthetic-only supervision and read-only recovery.
# No productive worker command, scheduler, automatic retry, or historical state mutation.
[CmdletBinding()]
param(
 [Parameter(Mandatory=$true)][ValidateSet('StartSynthetic','RecoverReadOnly')][string]$Mode,
 [Parameter(Mandatory=$true)][string]$EvidenceDir,
 [string]$RepoRoot,
 [string]$PythonExe,
 [ValidateSet('normal','nonzero','child','inactivity')][string]$SyntheticCase='normal'
)
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$base=$PSScriptRoot
. (Join-Path $base 'PathConfinement.ps1')

function Get-R35Hash([string]$value) {
 $sha=[Security.Cryptography.SHA256]::Create()
 try {[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($value))).Replace('-','').ToLowerInvariant()}
 finally {$sha.Dispose()}
}
function New-R35Result([string]$status,[string]$detail,$record,[object]$workerPid=$null) {
 [pscustomobject]@{
  schema='mars-r35-synthetic-recovery-v1';status=$status;detail=$detail
  intent_id=$(if($null -ne $record){[string]$record.intent_id}else{$null})
  worker_pid=$workerPid;can_execute=$false;may_retry_automatically=$false
  production_success=$false;canonical_artifacts_validated=$false;artifacts_modified=$false
 }
}
function Read-R35Intent([string]$path) {
 $envl=Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
 if($envl.schema -cne 'mars-r35-intent-envelope-v1' -or
    [string]$envl.sha256 -cnotmatch '^[a-f0-9]{64}$' -or
    (Get-R35Hash ([string]$envl.payload_json)) -cne [string]$envl.sha256){
  throw 'R35 INTENT CORRUPT: envelope/hash mismatch'
 }
 $rec=[string]$envl.payload_json | ConvertFrom-Json -ErrorAction Stop
 if($rec.schema -cne 'mars-r35-synthetic-intent-v1' -or
    $rec.operation -cne 'Synthetic' -or
    [string]$rec.intent_id -cnotmatch '^[a-f0-9]{32}$' -or
    [string]$rec.synthetic_case -cnotin @('normal','nonzero','child','inactivity') -or
    $rec.production_authorization -ne $false -or
    -not [IO.Path]::IsPathRooted([string]$rec.repo_root) -or
    -not [IO.Path]::IsPathRooted([string]$rec.python_exe)){
  throw 'R35 INTENT CORRUPT: unsupported or malformed operation'
 }
 $rec
}
function Assert-R35Evidence([string]$dir,[string]$repo) {
 $full=Assert-R33NoReparsePath -Path $dir -Label 'R35 EvidenceDir'
 if(-not [string]::IsNullOrWhiteSpace($repo)){
  $root=Assert-R33NoReparsePath -Path $repo -Label 'R35 RepoRoot'
  if($full -ieq $root -or
     $full.StartsWith(($root.TrimEnd('\')+'\'),[StringComparison]::OrdinalIgnoreCase) -or
     $root.StartsWith(($full.TrimEnd('\')+'\'),[StringComparison]::OrdinalIgnoreCase)){
   throw 'R35 DENIED: repository and evidence directories overlap'
  }
 }
 $full
}
function Get-R35Recovery([string]$dir) {
 $intentPath=Join-Path $dir 'r35_intent.json'
 if(-not [IO.File]::Exists($intentPath)){
  return New-R35Result 'RECOVERY_REQUIRED' 'No durable intent; no conclusion or retry' $null
 }
 try {
  $intent=Read-R35Intent $intentPath
  $launchPath=Join-Path $dir 'worker_launch.json'
  $exitPath=Join-Path $dir 'worker_exit.json'
  $hasLaunch=[IO.File]::Exists($launchPath)
  $hasExit=[IO.File]::Exists($exitPath)
  if(-not $hasLaunch -and -not $hasExit){
   return New-R35Result 'RECOVERY_REQUIRED' 'Registered intent but no worker evidence' $intent
  }
  if(-not $hasLaunch){
   return New-R35Result 'EVIDENCE_DEGRADED' 'Worker exit exists without launch' $intent
  }
  $start=Get-Content -LiteralPath $launchPath -Raw | ConvertFrom-Json -ErrorAction Stop
  $fixture=Join-Path $base 'tests\fixtures\synthetic_worker.py'
  $argsList=@($start.arguments)
  if($start.schema -cne 'mars-r3.3a-worker-v1' -or $start.mode -cne 'Synthetic' -or
     [string]$start.cwd -ine [string]$intent.repo_root -or
     [string]$start.executable -ine [string]$intent.python_exe -or
     $argsList.Count -ne 4 -or [string]$argsList[0] -cne '-u' -or
     [string]$argsList[1] -ine $fixture -or [string]$argsList[2] -cne '--case' -or
     [string]$argsList[3] -cne [string]$intent.synthetic_case -or $null -eq $start.root_pid){
   return New-R35Result 'EVIDENCE_DEGRADED' 'Worker launch contradicts intent' $intent
  }
  $workerPid=[int]$start.root_pid
  if(-not $hasExit){
   # PID alone is insufficient; account for reuse and mismatched executables.
   if([string]::IsNullOrWhiteSpace([string]$start.root_creation_utc)){
    return New-R35Result 'RECOVERY_REQUIRED' 'Worker identity timestamp absent' $intent $workerPid
   }
   $live=Get-CimInstance Win32_Process -Filter ("ProcessId = "+$workerPid) -ErrorAction Stop
   if($null -ne $live){
    $created=([datetime]$live.CreationDate).ToUniversalTime().ToString('o')
    if($created -ceq [string]$start.root_creation_utc -and
       [string]$live.ExecutablePath -ieq [string]$intent.python_exe){
     return New-R35Result 'WORKER_ACTIVE' 'Matching PID, creation time and Python executable; no restart' $intent $workerPid
    }
    return New-R35Result 'RECOVERY_REQUIRED' 'PID reused or executable/creation mismatch' $intent $workerPid
   }
   return New-R35Result 'RECOVERY_REQUIRED' 'Worker absent and exit record missing' $intent $workerPid
  }
  $done=Get-Content -LiteralPath $exitPath -Raw | ConvertFrom-Json -ErrorAction Stop
  if($done.schema -cne 'mars-r3.3a-worker-v1' -or $done.mode -cne 'Synthetic' -or
     [int]$done.root_pid -ne $workerPid -or
     [string]$done.cwd -ine [string]$intent.repo_root -or
     [string]$done.executable -ine [string]$intent.python_exe -or
     [string]$done.root_creation_utc -cne [string]$start.root_creation_utc){
   return New-R35Result 'EVIDENCE_DEGRADED' 'Worker exit contradicts launch identity' $intent $workerPid
  }
  if($done.evidence_degraded -eq $true -or
     [string]$done.status -match '^OBSERVER_' -or $null -ne $done.error){
   return New-R35Result 'EVIDENCE_DEGRADED' 'Observer error or degraded state' $intent $workerPid
  }
  if($done.status -ceq 'COMPLETED' -and $done.exit_code -eq 0 -and
     $done.root_exited -eq $true -and $done.job_empty -eq $true -and
     @($done.final_job_pids).Count -eq 0){
   return New-R35Result 'SYNTHETIC_SUCCESS_VERIFIED' 'Synthetic exit zero and job drained; not Runner validation' $intent $workerPid
  }
  if($done.status -ceq 'WORKER_NONZERO_OR_RESIDUAL' -and
     $done.exit_code -ne 0 -and $done.job_empty -eq $true){
   return New-R35Result 'SYNTHETIC_FAILED' 'Nonzero synthetic worker exit' $intent $workerPid
  }
  if($done.status -ceq 'SYNTHETIC_TERMINATED' -and $done.job_empty -eq $true){
   return New-R35Result 'SYNTHETIC_TERMINATED' 'Synthetic-only controlled termination' $intent $workerPid
  }
  return New-R35Result 'RECOVERY_REQUIRED' 'Incomplete or ambiguous terminal evidence' $intent $workerPid
 }catch {
  return New-R35Result 'EVIDENCE_DEGRADED' ('Evidence readback failed: '+$_.Exception.Message) $null
 }
}
if([string]::IsNullOrWhiteSpace($EvidenceDir) -or -not [IO.Path]::IsPathRooted($EvidenceDir)){
 throw 'R35 DENIED: absolute evidence path required'
}
if($Mode -ceq 'RecoverReadOnly'){
 $evidence=Assert-R35Evidence $EvidenceDir ''
 Get-R35Recovery $evidence
 return
}
if([string]::IsNullOrWhiteSpace($RepoRoot) -or -not [IO.Path]::IsPathRooted($RepoRoot) -or
   -not [IO.Directory]::Exists($RepoRoot) -or
   [string]::IsNullOrWhiteSpace($PythonExe) -or -not [IO.Path]::IsPathRooted($PythonExe) -or
   -not [IO.File]::Exists($PythonExe)){
 throw 'R35 DENIED: existing absolute RepoRoot and PythonExe required'
}
$repo=(Assert-R33NoReparsePath -Path $RepoRoot -Label 'R35 RepoRoot').TrimEnd('\')
$python=Assert-R33NoReparsePath -Path $PythonExe -Label 'R35 PythonExe'
$evidence=Assert-R35Evidence $EvidenceDir $repo
if([IO.Directory]::Exists($evidence) -and
   @([IO.Directory]::EnumerateFileSystemEntries($evidence)).Count -gt 0){
 throw 'R35 DENIED: cannot reuse a prior attempt directory'
}
[void][IO.Directory]::CreateDirectory($evidence)
[void](Assert-R35Evidence $evidence $repo)
$intent=[ordered]@{
 schema='mars-r35-synthetic-intent-v1'
 intent_id=[guid]::NewGuid().ToString('N')
 operation='Synthetic';synthetic_case=$SyntheticCase
 repo_root=$repo;python_exe=$python
 python_sha256=(Get-FileHash -LiteralPath $python -Algorithm SHA256).Hash.ToLowerInvariant()
 created_utc=[DateTime]::UtcNow.ToString('o')
 production_authorization=$false;auto_retry=$false
}
$payload=$intent|ConvertTo-Json -Depth 6 -Compress
$envl=[ordered]@{schema='mars-r35-intent-envelope-v1';payload_json=$payload;sha256=(Get-R35Hash $payload)}
$raw=[Text.UTF8Encoding]::new($false).GetBytes(($envl|ConvertTo-Json -Depth 6 -Compress)+[Environment]::NewLine)
$intentPath=Join-Path $evidence 'r35_intent.json'
# Exclusive CreateNew acts as a per-directory claim. It is never reset or deleted.
$stream=[IO.FileStream]::new($intentPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
try{$stream.Write($raw,0,$raw.Length);$stream.Flush($true)}finally{$stream.Dispose()}
[void](Read-R35Intent $intentPath)
$observerError=$null
try {
 $null=& (Join-Path $base 'Invoke-WorkerObserved.ps1') -Mode Synthetic -RepoRoot $repo -PythonExe $python -EvidenceDir $evidence -SyntheticCase $SyntheticCase -SampleIntervalSec 1
}catch{$observerError=$_.Exception.Message}
$result=Get-R35Recovery $evidence
if($null -ne $observerError -and $result.status -ceq 'SYNTHETIC_SUCCESS_VERIFIED'){
 throw ('R35 unexpected observer error: '+$observerError)
}
if($result.status -cne 'SYNTHETIC_SUCCESS_VERIFIED'){
 throw ('R35 NONSUCCESS '+$result.status+': '+$result.detail+'; use RecoverReadOnly')
}
$result
