# R3.2 tree observation helpers. Called only for PIDs assigned to THIS Job Object.
Set-StrictMode -Version Latest
function Write-R32JsonLine([string]$Path,[string]$Line) {
 # Concurrent readers must not kill the observer. Retry transient Windows share
 # violations, then preserve the complete line in an independently created spill.
 # Other I/O errors (disk full, invalid path, etc.) still fail visibly.
 $payload=[Text.UTF8Encoding]::new($false).GetBytes($Line+[Environment]::NewLine)
 for($attempt=0;$attempt -lt 10;$attempt++){
  $handle=$null
  try{
   $handle=[IO.FileStream]::new($Path,[IO.FileMode]::Append,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
   $handle.Write($payload,0,$payload.Length);$handle.Flush()
   return [pscustomobject]@{target='primary';attempts=($attempt+1);path=$Path}
  }catch [IO.IOException]{
   $code=[int]($_.Exception.HResult -band 0xFFFF)
   if($code -notin @(32,33)){throw}
   if($attempt -lt 9){[Threading.Thread]::Sleep(120)}
  }finally{if($null -ne $handle){$handle.Dispose()}}
 }
 $spillDir=Join-Path ([IO.Path]::GetDirectoryName($Path)) ('telemetry_spill\'+[IO.Path]::GetFileName($Path))
 [void][IO.Directory]::CreateDirectory($spillDir)
 $unique=[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ')+'-'+[guid]::NewGuid().ToString('N')+'.jsonl'
 $spillPath=Join-Path $spillDir $unique
 $spill=[IO.FileStream]::new($spillPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::Read)
 try{$spill.Write($payload,0,$payload.Length);$spill.Flush()}finally{$spill.Dispose()}
 return [pscustomobject]@{target='spill';attempts=10;path=$spillPath}
}
function Save-R32Json([string]$Path,$Value) {
 $Value | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath $Path -Encoding UTF8
}
function Get-R32Identity([int]$Id) {
 $ci=Get-CimInstance Win32_Process -Filter ("ProcessId = "+$Id) -ErrorAction SilentlyContinue
 if($null -eq $ci){return $null}
 $stamp=([datetime]$ci.CreationDate).ToUniversalTime().ToString('o')
 return [pscustomobject]@{
   pid=[int]$ci.ProcessId;ppid=[int]$ci.ParentProcessId;created_utc=$stamp
   key=([string]$ci.ProcessId+'@'+$stamp)
   executable=$ci.ExecutablePath;command_line=$ci.CommandLine
 }
}
function Get-R32Frame($Native,[hashtable]$Registry,[int]$RootPid,[string]$EvidenceDir) {
 $now=[DateTime]::UtcNow.ToString('o')
 $ids=@($Native.ActivePids())
 $rows=@()
 foreach($pidNow in $ids) {
  $idn=Get-R32Identity ([int]$pidNow)
  if($null -eq $idn){continue}
  if(@($Native.ActivePids()) -notcontains $pidNow){continue}
  $kind=if($idn.pid -eq $RootPid){'launcher'}elseif($idn.executable -match '(?i)conhost\.exe$'){'console_host'}elseif($idn.executable -match '(?i)python(w)?\.exe$'){'effective_python_or_descendant'}else{'owned_descendant'}
  $proc=$null
  $cpu=$null;$mem=$null;$threads=@();$modules=@();$errors=@()
  try{
   $proc=Get-Process -Id $pidNow -ErrorAction Stop
   $cpu=$proc.CPU;$mem=$proc.WorkingSet64
   foreach($th in @($proc.Threads)){
    $reason=$null
    if($th.ThreadState.ToString() -eq 'Wait'){try{$reason=$th.WaitReason.ToString()}catch{}}
    $threads+=([ordered]@{tid=$th.Id;state=$th.ThreadState.ToString();wait_reason=$reason})
   }
   try{$modules=@($proc.Modules|Select-Object -First 40|ForEach-Object{$_.ModuleName})}catch{$errors+=('modules: '+$_.Exception.Message)}
  }catch{$errors+=('process: '+$_.Exception.Message)}
  $entry=[ordered]@{
   utc=$now;pid=$idn.pid;ppid=$idn.ppid;created_utc=$idn.created_utc
   identity=$idn.key;executable=$idn.executable;command_line=$idn.command_line
   role=$kind;cpu_seconds=$cpu;working_set=$mem;threads=$threads;modules=$modules
   errors=$errors;state='LIVE_JOB_MEMBER'
  }
  $rows+=([pscustomobject]$entry)
  if(-not $Registry.ContainsKey($idn.key)){
   $Registry[$idn.key]=[ordered]@{identity=$idn.key;pid=$idn.pid;ppid=$idn.ppid;created_utc=$idn.created_utc;executable=$idn.executable;command_line=$idn.command_line;role=$kind;first_seen_utc=$now;last_seen_utc=$now;last_state='LIVE'}
  }else{
   $Registry[$idn.key]['last_seen_utc']=$now
   $Registry[$idn.key]['last_state']='LIVE'
  }
 }
 $activeKeys=@($rows|ForEach-Object{$_.identity})
 foreach($k in @($Registry.Keys)){
  if($k -notin $activeKeys -and $Registry[$k]['last_state'] -eq 'LIVE'){
   $Registry[$k]['last_state']='NO_LONGER_IN_JOB'
   $Registry[$k]['last_seen_utc']=$now
  }
 }
 $frame=[ordered]@{utc=$now;root_pid=$RootPid;job_pids=$ids;processes=$rows;registry_count=$Registry.Count}
 [void](Write-R32JsonLine (Join-Path $EvidenceDir 'tree_frames.jsonl') ($frame|ConvertTo-Json -Depth 12 -Compress))
 return [pscustomobject]$frame
}
function Invoke-R32OwnedCapture($Native,[hashtable]$Registry,[string]$EvidenceDir,[string]$RepoRoot) {
 $now=[DateTime]::UtcNow.ToString('o')
 $results=@()
 foreach($pidNow in @($Native.ActivePids())){
  $idn=Get-R32Identity ([int]$pidNow)
  if($null -eq $idn){$results+=([pscustomobject]@{pid=$pidNow;success=$false;error='identity_missing'});continue}
  if(@($Native.ActivePids()) -notcontains $pidNow){continue}
  $known=@($Registry.Values|Where-Object {$_.pid -eq $pidNow})
  if($known.Count -gt 0 -and @($known|Where-Object {$_.identity -eq $idn.key}).Count -eq 0){
   $results+=([pscustomobject]@{pid=$pidNow;success=$false;error='pid_reuse_identity_mismatch'});continue
  }
  $res=[ordered]@{pid=$pidNow;identity=$idn.key;start_utc=$now;end_utc=$null;wct_count=0;wct_success_count=0;dump_path=$null;dump_bytes=$null;dump_success=$false;success=$false;error=$null}
  try {
   $cap= & (Join-Path $PSScriptRoot 'r2_capture.ps1') -TargetPid ([int]$pidNow) -EvidenceDir $EvidenceDir -RepoRoot $RepoRoot -WaitChain -MiniDump | ConvertFrom-Json
   $again=Get-R32Identity ([int]$pidNow)
   $same=($null -ne $again -and $again.key -ceq $idn.key)
   $res.wct_count=@($cap.wait_chains).Count
   $res.wct_success_count=@($cap.wait_chains|Where-Object{[string]$_.result -match '^node_count=[1-9][0-9]*'}).Count
   if($null -ne $cap.dump){$res.dump_success=($cap.dump.success -eq $true);$res.dump_path=$cap.dump.path;$res.dump_bytes=$cap.dump.bytes}
   $res.success=($same -and $cap.present -and $res.dump_success -and [long]$res.dump_bytes -gt 0 -and $res.wct_success_count -gt 0)
   if(-not $res.success){$res.error='missing valid WCT chain, nonempty dump or stable identity'}
  }catch{$res.error=$_.Exception.Message}
  $res.end_utc=[DateTime]::UtcNow.ToString('o')
  $results+=([pscustomobject]$res)
 }
 $summary=[ordered]@{start_utc=$now;end_utc=[DateTime]::UtcNow.ToString('o');count=$results.Count;results=$results;all_success=($results.Count -gt 0 -and @($results|Where-Object{-not $_.success}).Count -eq 0)}
 [void](Write-R32JsonLine (Join-Path $EvidenceDir 'capture_runs.jsonl') ($summary|ConvertTo-Json -Depth 9 -Compress))
 return [pscustomobject]$summary
}
