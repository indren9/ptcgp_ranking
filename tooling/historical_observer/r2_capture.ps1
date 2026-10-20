param(
 [Parameter(Mandatory=$true)][int]$TargetPid,
 [Parameter(Mandatory=$true)][string]$EvidenceDir,
 [switch]$WaitChain,
 [switch]$MiniDump,
 [string]$RepoRoot
)
$ErrorActionPreference = 'Stop'
[System.IO.Directory]::CreateDirectory($EvidenceDir) | Out-Null
$utc = [DateTime]::UtcNow.ToString('o')
$sample = [ordered]@{ utc=$utc; target_pid=$TargetPid; observer_pid=$PID; present=$false; parent_pid=$null; command_line=$null; executable=$null; cpu_seconds=$null; working_set=$null; thread_count=$null; threads=@(); module_count=$null; module_names=@(); writer_lock_present=$null; errors=@() }
try {
 $ci=Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $TargetPid) -ErrorAction Stop
 if ($null -ne $ci) {
  $sample.present=$true
  $sample.parent_pid=$ci.ParentProcessId
  $sample.command_line=$ci.CommandLine
  $sample.executable=$ci.ExecutablePath
 }
} catch { $sample.errors+=("cim:"+$_.Exception.Message) }
try {
 $proc=Get-Process -Id $TargetPid -ErrorAction Stop
 $sample.present=$true
 $sample.cpu_seconds=$proc.CPU
 $sample.working_set=$proc.WorkingSet64
 $sample.thread_count=$proc.Threads.Count
 $sample.threads=@($proc.Threads | ForEach-Object {
  $tid=$_.Id
  $state=$_.ThreadState.ToString()
  $reason=$null
  if($state -eq 'Wait'){try{$reason=$_.WaitReason.ToString()}catch{}}
  [ordered]@{tid=$tid;state=$state;wait_reason=$reason}
 })
 try {
  $mods=@($proc.Modules | ForEach-Object { $_.ModuleName })
  $sample.module_count=$mods.Count
  $sample.module_names=@($mods | Select-Object -First 40)
 } catch { $sample.errors+=("modules:"+$_.Exception.Message) }
} catch { $sample.errors+=("getprocess:"+$_.Exception.Message) }
$lock=if(-not [string]::IsNullOrWhiteSpace($RepoRoot)){Join-Path $RepoRoot 'outputs\TCG\Rebuild\writer.lock'}else{$null}
$sample.writer_lock_present=($null -ne $lock -and [System.IO.File]::Exists($lock))
if($WaitChain -or $MiniDump) {
 $native=@'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class R2Native {
 [DllImport("advapi32.dll", EntryPoint="OpenThreadWaitChainSession", SetLastError=true)]
 public static extern IntPtr OpenWCT(uint flags, IntPtr callback);
 [DllImport("advapi32.dll", EntryPoint="CloseThreadWaitChainSession")]
 public static extern void CloseWCT(IntPtr h);
 [DllImport("advapi32.dll", EntryPoint="GetThreadWaitChain", SetLastError=true)]
 [return:MarshalAs(UnmanagedType.Bool)]
 public static extern bool GetWCT(IntPtr h, IntPtr ctx, uint flags, uint tid, ref uint count, IntPtr nodes, [MarshalAs(UnmanagedType.Bool)]out bool cycle);
 [DllImport("kernel32.dll", SetLastError=true)]
 public static extern IntPtr OpenProcess(uint rights, [MarshalAs(UnmanagedType.Bool)] bool inherit, uint pid);
 [DllImport("kernel32.dll", SetLastError=true)]
 [return:MarshalAs(UnmanagedType.Bool)]
 public static extern bool CloseHandle(IntPtr h);
 [DllImport("dbghelp.dll", SetLastError=true)]
 [return:MarshalAs(UnmanagedType.Bool)]
 public static extern bool MiniDumpWriteDump(IntPtr hProcess, uint pid, IntPtr hFile, uint type, IntPtr exception, IntPtr streams, IntPtr callbacks);
 public static string Chain(uint tid) {
  IntPtr h=OpenWCT(0,IntPtr.Zero);
  if(h==IntPtr.Zero)return "WCT_SESSION_ERROR="+Marshal.GetLastWin32Error();
  const int nodeSize=280;
  IntPtr mem=Marshal.AllocHGlobal(nodeSize*16);
  try {
   uint count=16; bool cycle=false;
   bool ok=GetWCT(h,IntPtr.Zero,0,tid,ref count,mem,out cycle);
   if(!ok)return "WCT_ERROR="+Marshal.GetLastWin32Error();
   StringBuilder b=new StringBuilder();
   b.Append("node_count=").Append(count).Append(" cycle=").Append(cycle);
   for(int i=0;i<count&&i<16;i++){
    int k=i*nodeSize;
    b.Append(" [type=").Append(Marshal.ReadInt32(mem,k));
    b.Append(" status=").Append(Marshal.ReadInt32(mem,k+4));
    b.Append(" data0=").Append(Marshal.ReadInt32(mem,k+8));
    b.Append(" data1=").Append(Marshal.ReadInt32(mem,k+12)).Append("]");
   }
   return b.ToString();
  } finally {Marshal.FreeHGlobal(mem);CloseWCT(h);}
 }
}
'@
 if(-not ('R2Native' -as [type])) { Add-Type -TypeDefinition $native -ErrorAction Stop }
}
if($WaitChain -and $sample.present) {
 $chains=@()
 foreach($th in @($sample.threads | Select-Object -First 5)) {
  try { $chains += [ordered]@{tid=$th.tid;result=[R2Native]::Chain([uint32]$th.tid)} }
  catch { $chains += [ordered]@{tid=$th.tid;result="exception:$($_.Exception.Message)"} }
 }
 $sample['wait_chains']=$chains
}
if($MiniDump -and $sample.present) {
 $dump=Join-Path $EvidenceDir ("thread_dump_{0}_{1}.dmp" -f $TargetPid,([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')))
 $h=[R2Native]::OpenProcess(0x0410,$false,[uint32]$TargetPid)
 if($h -eq [IntPtr]::Zero) {$sample['dump']=[ordered]@{success=$false;error="OpenProcess:"+[Runtime.InteropServices.Marshal]::GetLastWin32Error()}}
 else {
  try {
   $s=[System.IO.FileStream]::new($dump,[System.IO.FileMode]::CreateNew,[System.IO.FileAccess]::Write,[System.IO.FileShare]::None)
   try {
    $ok=[R2Native]::MiniDumpWriteDump($h,[uint32]$TargetPid,$s.SafeFileHandle.DangerousGetHandle(),0x1000,[IntPtr]::Zero,[IntPtr]::Zero,[IntPtr]::Zero)
    $errno=if($ok){$null}else{[Runtime.InteropServices.Marshal]::GetLastWin32Error()}
   } finally {$s.Dispose()}
   $sample['dump']=[ordered]@{success=$ok;error_code=$errno;path=$dump;bytes=(Get-Item -LiteralPath $dump).Length}
  } catch {$sample['dump']=[ordered]@{success=$false;error=$_.Exception.Message}}
  finally {[void][R2Native]::CloseHandle($h)}
 }
}
$line=$sample | ConvertTo-Json -Depth 8 -Compress
Add-Content -LiteralPath (Join-Path $EvidenceDir 'snapshots.jsonl') -Value $line -Encoding UTF8
Write-Output $line
