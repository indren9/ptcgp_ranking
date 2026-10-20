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
# Strict UTC parse of original JSON text (PS7 converts JSON dates to DateTime).
function Get-R35UtcTicks([string]$json,[string]$field) {
 $pattern='"'+[regex]::Escape($field)+'"\s*:\s*"([^"]*)"'
 $hits=[regex]::Matches($json,$pattern)
 if($hits.Count -ne 1){throw ('R35 UTC: missing/ambiguous '+$field)}
 $stamp=$hits[0].Groups[1].Value
 if($stamp -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z$'){
  throw ('R35 UTC: malformed/non-UTC '+$field)
 }
 try{
  $date=[DateTimeOffset]::ParseExact($stamp,"yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'",
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::AssumeUniversal)
  return $date.UtcDateTime.Ticks
 }catch{throw ('R35 UTC: invalid '+$field)}
}
# Receipt binds intent, directory, launch and exit bytewise. It is NOT an
# independent signature or a production-level trust anchor.
function Test-R35Link([string]$dir,[string]$id) {
 $path=Join-Path $dir 'r35_attempt_link.json'
 if(-not [IO.File]::Exists($path)){return $false}
 try {
  $link=Get-Content -LiteralPath $path -Raw|ConvertFrom-Json -ErrorAction Stop
  if($link.schema -cne 'mars-r35-attempt-link-v1' -or
     [string]$link.intent_id -cne $id -or
     [string]$link.evidence_dir -ine $dir){return $false}
  foreach($name in @('r35_intent.json','worker_launch.json','worker_exit.json')){
   $property=switch($name){
    'r35_intent.json' {'intent_sha256'}
    'worker_launch.json' {'launch_sha256'}
    'worker_exit.json' {'exit_sha256'}
   }
   $want=[string]$link.$property
   if($want -cnotmatch '^[a-f0-9]{64}$' -or
      (Get-FileHash -LiteralPath (Join-Path $dir $name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $want){return $false}
  }
  return $true
 }catch{return $false}
}
function Write-R35Link([string]$dir,[string]$id) {
 $launch=Join-Path $dir 'worker_launch.json'
 $exit=Join-Path $dir 'worker_exit.json'
 if(-not [IO.File]::Exists($launch) -or -not [IO.File]::Exists($exit)){return}
 $link=[ordered]@{
  schema='mars-r35-attempt-link-v1';intent_id=$id;evidence_dir=$dir
  intent_sha256=(Get-FileHash -LiteralPath (Join-Path $dir 'r35_intent.json') -Algorithm SHA256).Hash.ToLowerInvariant()
  launch_sha256=(Get-FileHash -LiteralPath $launch -Algorithm SHA256).Hash.ToLowerInvariant()
  exit_sha256=(Get-FileHash -LiteralPath $exit -Algorithm SHA256).Hash.ToLowerInvariant()
 }
 $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($link|ConvertTo-Json -Compress)+[Environment]::NewLine)
 $path=Join-Path $dir 'r35_attempt_link.json'
 $fs=[IO.FileStream]::new($path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$fs.Write($bytes,0,$bytes.Length);$fs.Flush($true)}finally{$fs.Dispose()}
 if(-not (Test-R35Link $dir $id)){throw 'R35 attempt-link readback failure'}
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
 [void](Get-R35UtcTicks ([string]$envl.payload_json) 'created_utc')
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
  $intentEnvelope=Get-Content -LiteralPath $intentPath -Raw|ConvertFrom-Json -ErrorAction Stop
  $intentTicks=Get-R35UtcTicks ([string]$intentEnvelope.payload_json) 'created_utc'
  $launchRaw=Get-Content -LiteralPath $launchPath -Raw
  $start=$launchRaw|ConvertFrom-Json -ErrorAction Stop
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
  # Chronology is required even if the observer is interrupted.
  $startTicks=Get-R35UtcTicks $launchRaw 'start_utc'
  $createdTicks=Get-R35UtcTicks $launchRaw 'root_creation_utc'
  if($intentTicks -gt $startTicks -or $startTicks -gt $createdTicks){
   return New-R35Result 'EVIDENCE_DEGRADED' 'Intent/launch/process chronology invalid' $intent
  }
  $workerPid=[int]$start.root_pid
  if(-not $hasExit){
   # PID alone is insufficient; account for reuse and mismatched executables.
   $live=Get-CimInstance Win32_Process -Filter ("ProcessId = "+$workerPid) -ErrorAction Stop
   if($null -ne $live){
    $created=([datetime]$live.CreationDate).ToUniversalTime().ToString('o')
    # PS 7 ConvertFrom-Json turns ISO timestamps into DateTime; PS 5.1 keeps strings.
    $expectedCreated=$createdTicks
    $commandLine=[string]$live.CommandLine
    $fixturePresent=($commandLine.IndexOf($fixture,[StringComparison]::OrdinalIgnoreCase) -ge 0)
    $expectedCase='(?i)--case"?\s+"?'+[regex]::Escape([string]$intent.synthetic_case)+'(?:"|\s|$)'
    $casePresent=[regex]::IsMatch($commandLine,$expectedCase)
    if(([datetime]$live.CreationDate).ToUniversalTime().Ticks -eq $expectedCreated -and
       [string]$live.ExecutablePath -ieq [string]$intent.python_exe -and
       $fixturePresent -and $casePresent){
     return New-R35Result 'WORKER_ACTIVE' 'Matching PID, UTC creation, Python and synthetic command; no restart' $intent $workerPid
    }
    return New-R35Result 'RECOVERY_REQUIRED' 'PID reused or executable/creation/command mismatch' $intent $workerPid
   }
   return New-R35Result 'RECOVERY_REQUIRED' 'Worker absent and exit record missing' $intent $workerPid
  }
  $exitRaw=Get-Content -LiteralPath $exitPath -Raw
  $done=$exitRaw|ConvertFrom-Json -ErrorAction Stop
  $exitStartTicks=Get-R35UtcTicks $exitRaw 'start_utc'
  $exitCreatedTicks=Get-R35UtcTicks $exitRaw 'root_creation_utc'
  $exitEndTicks=Get-R35UtcTicks $exitRaw 'end_utc'
  if($exitStartTicks -ne $startTicks -or $exitCreatedTicks -ne $createdTicks -or
     $exitEndTicks -lt $createdTicks){
   return New-R35Result 'EVIDENCE_DEGRADED' 'Launch/exit timestamps missing or contradictory' $intent $workerPid
  }
  if(-not (Test-R35Link $dir ([string]$intent.intent_id))){
   return New-R35Result 'EVIDENCE_DEGRADED' 'Intent/launch/exit link absent or mismatched' $intent $workerPid
  }
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
# Persist a unique receipt only after the synthetic observer invocation.
Write-R35Link $evidence ([string]$intent.intent_id)
$result=Get-R35Recovery $evidence
if($null -ne $observerError -and $result.status -ceq 'SYNTHETIC_SUCCESS_VERIFIED'){
 throw ('R35 unexpected observer error: '+$observerError)
}
if($result.status -cne 'SYNTHETIC_SUCCESS_VERIFIED'){
 throw ('R35 NONSUCCESS '+$result.status+': '+$result.detail+'; use RecoverReadOnly')
}
$result
