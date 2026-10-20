# R3.3-C Windows path confinement. Reject symlink/junction/mount reparse components.
# Lexical GetFullPath alone does NOT resolve Windows reparse targets.
Set-StrictMode -Version Latest
function Assert-R33NoReparsePath {
 param([Parameter(Mandatory=$true)][string]$Path,[string]$Label='path')
 if([string]::IsNullOrWhiteSpace($Path) -or $Path -cnotmatch '^[A-Za-z]:[\\/]'){
  throw ('R33 PATH DENIED: fully qualified local Windows path required for '+$Label)
 }
 $full=[IO.Path]::GetFullPath($Path)
 $root=[IO.Path]::GetPathRoot($full)
 $cursor=$root
 foreach($part in @($full.Substring($root.Length) -split '[\\/]')){
  if([string]::IsNullOrWhiteSpace($part)){continue}
  $cursor=[IO.Path]::Combine($cursor,$part)
  try {
   $attrs=[IO.File]::GetAttributes($cursor)
   if(($attrs -band [IO.FileAttributes]::ReparsePoint) -ne 0){
    throw ('R33 PATH DENIED: REPARSE component for '+$Label+': '+$cursor)
   }
  }catch [IO.FileNotFoundException]{
   # Missing leaf permitted until creation; existing ancestors validated.
  }catch [IO.DirectoryNotFoundException]{
   # Missing ancestors checked again after creation.
  }
 }
 return $full
}
