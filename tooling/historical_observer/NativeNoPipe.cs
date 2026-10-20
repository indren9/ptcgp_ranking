using System;
using System.IO;
using System.Text;
using System.Runtime.InteropServices;

public sealed class MarsR31Native : IDisposable {
 [StructLayout(LayoutKind.Sequential)] public struct SECATTR {public int nLength; public IntPtr lpSecurityDescriptor; [MarshalAs(UnmanagedType.Bool)] public bool bInheritHandle;}
 [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct STARTINFO {
  public int cb; public string lpReserved,lpDesktop,lpTitle; public int dwX,dwY,dwXSize,dwYSize,dwXCountChars,dwYCountChars,dwFillAttribute,dwFlags;
  public short wShowWindow,cbReserved2; public IntPtr lpReserved2,hStdInput,hStdOutput,hStdError;
 }
 [StructLayout(LayoutKind.Sequential)] public struct PROCINFO {public IntPtr hProcess,hThread;public int dwProcessId,dwThreadId;}
 [StructLayout(LayoutKind.Sequential)] public struct BASIC {public long PerProcessUserTimeLimit,PerJobUserTimeLimit; public uint LimitFlags; public UIntPtr MinimumWorkingSetSize,MaximumWorkingSetSize; public uint ActiveProcessLimit; public UIntPtr Affinity;public uint PriorityClass,SchedulingClass;}
 [StructLayout(LayoutKind.Sequential)] public struct IOC {public ulong ReadOperationCount,WriteOperationCount,OtherOperationCount,ReadTransferCount,WriteTransferCount,OtherTransferCount;}
 [StructLayout(LayoutKind.Sequential)] public struct EXT {public BASIC BasicLimitInformation;public IOC IoInfo;public UIntPtr ProcessMemoryLimit,JobMemoryLimit,PeakProcessMemoryUsed,PeakJobMemoryUsed;}
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateFileW(string n,uint access,uint share,ref SECATTR attr,uint how,uint flags,IntPtr template);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern bool CreateProcessW(string app,StringBuilder cmd,IntPtr pa,IntPtr ta,[MarshalAs(UnmanagedType.Bool)] bool inherit,uint flags,IntPtr env,string cwd,ref STARTINFO si,out PROCINFO pi);
 [DllImport("kernel32.dll",CharSet=CharSet.Unicode,SetLastError=true)] static extern IntPtr CreateJobObjectW(IntPtr attr,string name);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool SetInformationJobObject(IntPtr j,int cls,IntPtr info,uint len);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr j,IntPtr p);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool QueryInformationJobObject(IntPtr j,int cls,IntPtr buf,uint len,out uint result);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateJobObject(IntPtr j,uint exit);
 [DllImport("kernel32.dll",SetLastError=true)] static extern uint ResumeThread(IntPtr th);
 [DllImport("kernel32.dll",SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr p,out uint exit);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool TerminateProcess(IntPtr p,uint exit);
 [DllImport("kernel32.dll",SetLastError=true)] static extern bool CloseHandle(IntPtr h);
 const int STD=0x100;
 IntPtr job=IntPtr.Zero,proc=IntPtr.Zero,thread=IntPtr.Zero;
 public int Pid {get;private set;}
 public string StreamMode {get {return "WIN32_CREATEPROCESSW_INHERITABLE_FILE_HANDLES_NUL_NO_PIPES";}}
 public static string Quote(string s) {
  if(s==null)throw new ArgumentNullException("s");
  var b=new StringBuilder("\"");int back=0;
  foreach(char c in s) {
   if(c=='\\'){back++;continue;}
   if(c=='"'){b.Append('\\',back*2+1);b.Append('"');back=0;continue;}
   b.Append('\\',back);back=0;b.Append(c);
  }
  b.Append('\\',back*2);b.Append('"');return b.ToString();
 }
 static void Ensure(bool ok,string stage){if(!ok)throw new InvalidOperationException(stage+" win32_error="+Marshal.GetLastWin32Error());}
 public void Start(string app,string[] args,string cwd,string stdin,string stdout,string stderr){
  Start(app,args,cwd,stdin,stdout,stderr,true);
 }
 public void Start(string app,string[] args,string cwd,string stdin,string stdout,string stderr,bool killOnJobClose){
  if(Pid!=0)throw new InvalidOperationException("One launch per native instance");
  if(!Path.IsPathRooted(app)||!Path.IsPathRooted(cwd)||!File.Exists(app)||!Directory.Exists(cwd))throw new ArgumentException("Invalid executable or working directory");
  SECATTR a=new SECATTR();a.nLength=Marshal.SizeOf(typeof(SECATTR));a.bInheritHandle=true;
  IntPtr sin=IntPtr.Zero,sout=IntPtr.Zero,serr=IntPtr.Zero;
  bool launched=false;
  try{
   sin=CreateFileW(stdin,0x80000000,3,ref a,3,0x80,IntPtr.Zero);
   Ensure(sin!=new IntPtr(-1),"stdin NUL");
   sout=CreateFileW(stdout,0x40000000,1,ref a,1,0x80,IntPtr.Zero);
   Ensure(sout!=new IntPtr(-1),"stdout create_new");
   serr=CreateFileW(stderr,0x40000000,1,ref a,1,0x80,IntPtr.Zero);
   Ensure(serr!=new IntPtr(-1),"stderr create_new");
   job=CreateJobObjectW(IntPtr.Zero,null);Ensure(job!=IntPtr.Zero,"CreateJobObjectW");
   EXT e=new EXT();e.BasicLimitInformation.LimitFlags=killOnJobClose ? 0x2000u : 0u; // Git/probe: close terminates; worker: no automatic job-close termination
   int sz=Marshal.SizeOf(typeof(EXT));IntPtr buf=Marshal.AllocHGlobal(sz);
   try{Marshal.StructureToPtr(e,buf,false);Ensure(SetInformationJobObject(job,9,buf,(uint)sz),"SetInformationJobObject");}
   finally{Marshal.FreeHGlobal(buf);}
   STARTINFO si=new STARTINFO();si.cb=Marshal.SizeOf(typeof(STARTINFO));si.dwFlags=STD;
   si.hStdInput=sin;si.hStdOutput=sout;si.hStdError=serr;
   var cmd=new StringBuilder(Quote(app));
   foreach(string arg in args)cmd.Append(' ').Append(Quote(arg));
   PROCINFO pi;
   Ensure(CreateProcessW(app,cmd,IntPtr.Zero,IntPtr.Zero,true,0x08000004,IntPtr.Zero,cwd,ref si,out pi),"CreateProcessW");
   launched=true;proc=pi.hProcess;thread=pi.hThread;Pid=pi.dwProcessId;
   Ensure(AssignProcessToJobObject(job,proc),"AssignProcessToJobObject (nested job may be disallowed)");
   if(ResumeThread(thread)==0xffffffff)throw new InvalidOperationException("ResumeThread failed "+Marshal.GetLastWin32Error());
  }catch{
   if(launched && proc!=IntPtr.Zero)TerminateProcess(proc,0xDEAD);
   Dispose();throw;
  }finally{
   if(sin!=IntPtr.Zero&&sin!=new IntPtr(-1))CloseHandle(sin);
   if(sout!=IntPtr.Zero&&sout!=new IntPtr(-1))CloseHandle(sout);
   if(serr!=IntPtr.Zero&&serr!=new IntPtr(-1))CloseHandle(serr);
  }
 }
 public string Wait(uint milliseconds) {
  if(proc==IntPtr.Zero)throw new InvalidOperationException("Not started");
  uint rc=WaitForSingleObject(proc,milliseconds);
  if(rc==0)return "EXITED";
  if(rc==258)return "TIMEOUT";
  throw new InvalidOperationException("WaitForSingleObject "+rc+" error "+Marshal.GetLastWin32Error());
 }
 public long ExitCode(){uint code;Ensure(GetExitCodeProcess(proc,out code),"GetExitCodeProcess");return (long)(int)code;}
 public int[] ActivePids(){
  if(job==IntPtr.Zero)return new int[0];
  uint len=65536,got=0;IntPtr b=Marshal.AllocHGlobal((int)len);
  try{Ensure(QueryInformationJobObject(job,3,b,len,out got),"QueryInformationJobObject");
   int count=Marshal.ReadInt32(b,4);if(count<0||count>2000)throw new InvalidOperationException("Invalid PID count");
   int[] ids=new int[count];for(int i=0;i<count;i++)ids[i]=Marshal.ReadIntPtr(b,8+i*IntPtr.Size).ToInt32();return ids;
  }finally{Marshal.FreeHGlobal(b);}
 }
 public void KillOwnedTree(){if(job==IntPtr.Zero)throw new InvalidOperationException("No owned Job Object");Ensure(TerminateJobObject(job,0xBAD),"TerminateJobObject");}
 public void Dispose(){
  if(thread!=IntPtr.Zero){CloseHandle(thread);thread=IntPtr.Zero;}
  if(proc!=IntPtr.Zero){CloseHandle(proc);proc=IntPtr.Zero;}
  if(job!=IntPtr.Zero){CloseHandle(job);job=IntPtr.Zero;}
 }
}