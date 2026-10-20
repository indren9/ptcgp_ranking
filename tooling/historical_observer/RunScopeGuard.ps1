# R3.3-C reusable run-scoped PREVIEW guard.
# Does not launch, authorize, modify status, or write any evidence.
Set-StrictMode -Version Latest
function Test-R33CRunScope([string]$RunScopePath,[string]$RepoRoot,[string]$PythonExe) {
 if([string]::IsNullOrWhiteSpace($RunScopePath) -or -not [IO.Path]::IsPathRooted($RunScopePath)){throw 'R33C DENIED: absolute run-scope file required'}
 if(-not [IO.File]::Exists($RunScopePath)){throw 'R33C DENIED: missing run-scope file'}
 $s=Get-Content -LiteralPath $RunScopePath -Raw|ConvertFrom-Json
 if($s.schema -cne 'mars-r33c-run-scope-preview-v1' -or $s.intent -cne 'PREVIEW_ONLY'){
  throw 'R33C DENIED: unsupported schema or non-preview intent'
 }
 if($s.operation -cne 'window' -or [string]$s.window_id -cnotmatch '^[A-Za-z0-9_-]+$'){
  throw 'R33C DENIED: this candidate supports explicit window only'
 }
 foreach($sha in @($s.expected_head,$s.boundaries_sha256,$s.config_sha256)){
  if([string]$sha -cnotmatch '^[a-f0-9]{40}$|^[a-f0-9]{64}$'){throw 'R33C DENIED: unpinned or malformed digest'}
 }
 if([string]$s.expected_head -cnotmatch '^[a-f0-9]{40}$' -or [string]$s.boundaries_sha256 -cnotmatch '^[a-f0-9]{64}$' -or [string]$s.config_sha256 -cnotmatch '^[a-f0-9]{64}$'){
  throw 'R33C DENIED: wrong digest size or uppercase digest'
 }
 if(-not [IO.Path]::IsPathRooted($RepoRoot) -or -not [IO.Directory]::Exists($RepoRoot)){throw 'R33C DENIED: repository root absent'}
 $repo=[IO.Path]::GetFullPath($RepoRoot).TrimEnd('\')
 $canonicalRepo=[IO.Path]::GetFullPath($repo).TrimEnd('\')+'\'
 $inputPaths=@([string]$s.boundaries_path,[string]$s.config_path)
 foreach($p in $inputPaths){
  if([string]::IsNullOrWhiteSpace($p) -or -not [IO.Path]::IsPathRooted($p)){throw 'R33C DENIED: absolute input paths required'}
  $full=[IO.Path]::GetFullPath($p)
  if(-not $full.StartsWith($canonicalRepo,[StringComparison]::OrdinalIgnoreCase) -or -not [IO.File]::Exists($full)){
   throw 'R33C DENIED: missing or outside-worktree input'
  }
  $attrs=[IO.File]::GetAttributes($full)
  if(($attrs -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'R33C DENIED: input reparse point'}
 }
 function LocalHash([string]$path){
  $hash=[Security.Cryptography.SHA256]::Create();$handle=[IO.File]::OpenRead($path)
  try{[BitConverter]::ToString($hash.ComputeHash($handle)).Replace('-','').ToLowerInvariant()}
  finally{$handle.Dispose();$hash.Dispose()}
 }
 $b=[IO.Path]::GetFullPath([string]$s.boundaries_path)
 $c=[IO.Path]::GetFullPath([string]$s.config_path)
 if((LocalHash $b) -cne [string]$s.boundaries_sha256 -or (LocalHash $c) -cne [string]$s.config_sha256){
  throw 'R33C DENIED: boundaries or YAML SHA-256 drift'
 }
 $gitRoot=((& git -C $repo rev-parse --show-toplevel)|Out-String).Trim()
 $branch=((& git -C $repo branch --show-current)|Out-String).Trim()
 $head=((& git -C $repo rev-parse HEAD)|Out-String).Trim()
 $dirty=@(& git -C $repo status --porcelain=v1)
 if([IO.Path]::GetFullPath($gitRoot).TrimEnd('\') -ine [IO.Path]::GetFullPath($repo).TrimEnd('\') -or $branch -cne 'main' -or $head -cne [string]$s.expected_head -or $dirty.Count -ne 0){
  throw 'R33C DENIED: Git worktree/branch/HEAD/status mismatch'
 }
 $workers=@(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='py.exe'" -ErrorAction Stop |
  Where-Object {[string]$_.CommandLine -match '(?i)historical[_-]rebuild'})
 if($workers.Count -ne 0 -or [IO.File]::Exists((Join-Path $repo 'outputs\TCG\Rebuild\writer.lock'))){
  throw 'R33C DENIED: historical worker or writer lock present'
 }
 $boundaries=Get-Content -LiteralPath $b -Raw|ConvertFrom-Json
 if($boundaries.reviewed -ne $true -or @($boundaries.boundaries|Where-Object{$_.event_id -ceq [string]$s.window_id}).Count -ne 1){
  throw 'R33C DENIED: window absent from explicitly reviewed boundaries'
 }
 $state=Join-Path $repo ('outputs\TCG\Rebuild\'+[string]$s.window_id+'\state\current.json')
 $observed='ABSENT'
 if([IO.File]::Exists($state)){
  $wrapper=Get-Content -LiteralPath $state -Raw|ConvertFrom-Json
  if($null -eq $wrapper.state -or $null -eq $wrapper.sha256 -or $wrapper.state.window_id -cne [string]$s.window_id){
   throw 'R33C DENIED: malformed existing state; no silent reset'
  }
  $observed=[string]$wrapper.state.status
 }
 $python=[IO.Path]::GetFullPath($PythonExe)
 if(-not [IO.File]::Exists($python)){throw 'R33C DENIED: missing canonical Python'}
 $arguments=@('-u','-X','importtime','-m','historical_rebuild','--boundaries',$b,'--config',$c,'window',[string]$s.window_id)
 return [pscustomobject]@{
  schema='mars-r33c-guard-inspection-v1';status=$(if($observed -ceq 'DONE'){'BLOCKED_ALREADY_DONE'}else{'PREVIEW_ONLY_NOT_AUTHORIZED'})
  scope_window=[string]$s.window_id;operation='window';observed_checkpoint_status=$observed
  can_execute=$false;authorization=$false;artifacts_modified=$false
  boundaries_sha256=[string]$s.boundaries_sha256;config_sha256=[string]$s.config_sha256;head=$head
  command_executable=$python;command_arguments=$arguments
  next_gate='Separate run-specific approval and production implementation review'
 }
}