# R3.3-A isolated: immutable state JSON, atomic publish or explicit recovery.
Set-StrictMode -Version Latest

function Write-R33StateJson([string]$Path,$Value) {
 if(-not [IO.Path]::IsPathRooted($Path)){throw 'R3.3-A: absolute state path required'}
 $dir=[IO.Path]::GetDirectoryName($Path)
 if(-not [IO.Directory]::Exists($dir)){throw ('R3.3-A: evidence directory absent: '+$dir)}
 $name=[IO.Path]::GetFileName($Path)
 if($name -notin @('worker_launch.json','worker_exit.json')){throw ('R3.3-A: unsupported immutable state name: '+$name)}
 $utc=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')
 $nonce=[guid]::NewGuid().ToString('N')
 $staging=Join-Path $dir ('.'+$name+'.'+$nonce+'.staging')
 $json=$Value|ConvertTo-Json -Depth 14
 $payload=[Text.UTF8Encoding]::new($false).GetBytes($json+[Environment]::NewLine)
 $fs=[IO.FileStream]::new($staging,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
 try{$fs.Write($payload,0,$payload.Length);$fs.Flush($true)}finally{$fs.Dispose()}
 $sha=[Security.Cryptography.SHA256]::Create()
 try{$want=[BitConverter]::ToString($sha.ComputeHash($payload)).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 $reason=$null;$mode=$null;$final=$null
 if([IO.File]::Exists($Path) -or [IO.Directory]::Exists($Path)){
  $reason='canonical_destination_preexists_fail_closed'
 }else{
  for($attempt=1;$attempt -le 5;$attempt++){
   try{
    [IO.File]::Move($staging,$Path)
    $final=$Path;$mode='canonical';break
   }catch [IO.IOException]{
    $reason='canonical_move_io_'+([int]($_.Exception.HResult -band 0xFFFF))
    if([IO.File]::Exists($Path) -or [IO.Directory]::Exists($Path)){break}
    if($attempt -lt 5){[Threading.Thread]::Sleep(80)}
   }catch [UnauthorizedAccessException]{
    $reason='canonical_move_access_denied';break
   }
  }
 }
 if($null -eq $final){
  # Preserve an independently readable *degraded* state if canonical publish fails.
  # The original staging file is deliberately retained until recovery readback.
  $recoveryDir=Join-Path (Join-Path $dir 'state_recovery') $name
  [void][IO.Directory]::CreateDirectory($recoveryDir)
  $recoverObj=$json|ConvertFrom-Json
  if($recoverObj.PSObject.Properties.Name -contains 'status'){
   $prior=[string]$recoverObj.status
   $recoverObj.status='OBSERVER_EVIDENCE_DEGRADED'
   if(($recoverObj.PSObject.Properties.Name -notcontains 'worker_status_before_evidence_degradation') -or [string]::IsNullOrEmpty([string]$recoverObj.worker_status_before_evidence_degradation)){
    $recoverObj|Add-Member -NotePropertyName 'worker_status_before_evidence_degradation' -NotePropertyValue $prior -Force
   }
  }
  $recoverObj|Add-Member -NotePropertyName 'persistence_recovery' -NotePropertyValue ([ordered]@{
   schema='mars-r33a-state-recovery-v1';canonical_path=$Path;reason=$reason
   recorded_utc=[DateTime]::UtcNow.ToString('o')}) -Force
  $recoverName=$utc+'-'+$nonce+'.json'
  $final=Join-Path $recoveryDir $recoverName
  $recoverStage=$final+'.staging'
  $recoverPayload=[Text.UTF8Encoding]::new($false).GetBytes(($recoverObj|ConvertTo-Json -Depth 14)+[Environment]::NewLine)
  $recoverStream=[IO.FileStream]::new($recoverStage,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
  try{$recoverStream.Write($recoverPayload,0,$recoverPayload.Length);$recoverStream.Flush($true)}finally{$recoverStream.Dispose()}
  [IO.File]::Move($recoverStage,$final)
  $payload=$recoverPayload;$mode='recovery'
 }
 # Validate the durable bytes and JSON before reporting persistence success.
 $readBytes=[IO.File]::ReadAllBytes($final)
 $actualSha=[Security.Cryptography.SHA256]::Create()
 try{$actual=[BitConverter]::ToString($actualSha.ComputeHash($readBytes)).Replace('-','').ToLowerInvariant()}finally{$actualSha.Dispose()}
 $payloadSha=[Security.Cryptography.SHA256]::Create()
 try{$expected=[BitConverter]::ToString($payloadSha.ComputeHash($payload)).Replace('-','').ToLowerInvariant()}finally{$payloadSha.Dispose()}
 if($actual -cne $expected){throw ('R3.3-A: state readback SHA mismatch: '+$final)}
 $check=[Text.UTF8Encoding]::new($false).GetString($readBytes)|ConvertFrom-Json
 if($null -eq $check){throw ('R3.3-A: state JSON parse/readback failed: '+$final)}
 if($mode -eq 'recovery' -and [IO.File]::Exists($staging)){[IO.File]::Delete($staging)}
 return [pscustomobject]@{mode=$mode;path=$final;bytes=$readBytes.Length;sha256=$actual;reason=$reason;verified=$true}
}