$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$base=Split-Path -Parent $PSScriptRoot
$obs=Join-Path $base 'Invoke-WorkerObserved.ps1'
$python=if($env:MARS_TEST_PYTHON){$env:MARS_TEST_PYTHON}else{(Get-Command python -ErrorAction Stop).Source}
$work=Join-Path ([IO.Path]::GetTempPath()) ('MARS R33 scope '+[guid]::NewGuid().ToString('N'))
$repo=Join-Path $work 'repo with spaces'
[void][IO.Directory]::CreateDirectory($repo)
$bound=Join-Path $repo 'fixtures\boundaries.json'
$config=Join-Path $repo 'config\rebuild.yaml'
[void][IO.Directory]::CreateDirectory((Split-Path -Parent $bound))
[void][IO.Directory]::CreateDirectory((Split-Path -Parent $config))
[IO.File]::WriteAllText($bound,'{"reviewed":true,"boundaries":[{"event_id":"TEST_WINDOW"}]}',[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText($config,'source: synthetic-only',[Text.UTF8Encoding]::new($false))
& git -C $repo init -b main|Out-Null
if($LASTEXITCODE -ne 0){throw 'GIT_INIT_FAIL'}
& git -C $repo add .|Out-Null
if($LASTEXITCODE -ne 0){throw 'GIT_ADD_FAIL'}
& git -C $repo -c user.email=synthetic@example.invalid -c user.name=MARS-Synthetic commit -m 'synthetic reviewed boundaries'|Out-Null
if($LASTEXITCODE -ne 0){throw 'GIT_COMMIT_FAIL'}
$head=((& git -C $repo rev-parse HEAD)|Out-String).Trim()
$checks=[System.Collections.Generic.List[string]]::new()
function Check([bool]$yes,[string]$label){
 if(-not $yes){throw ('PREVIEW_TEST_FAILURE '+$label)}
 $script:checks.Add($label);Write-Output ('TEST_PASS '+$label)
}
function MustReject([scriptblock]$run,[string]$name){
 $thrown=$false
 try{& $run|Out-Null}catch{$thrown=$true}
 Check $thrown $name
}
function Sha([string]$p){(Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()}
$basic=[ordered]@{
 schema='mars-r33c-run-scope-preview-v1';intent='PREVIEW_ONLY'
 operation='window';window_id='TEST_WINDOW'
 boundaries_path=$bound;boundaries_sha256=(Sha $bound)
 config_path=$config;config_sha256=(Sha $config);expected_head=$head
}
$scope=Join-Path $work 'scope.json'
function WriteScope($r,[string]$name){
 $p=Join-Path $work $name
 [IO.File]::WriteAllText($p,($r|ConvertTo-Json -Depth 7),[Text.UTF8Encoding]::new($false))
 return $p
}
$scope=WriteScope $basic 'scope.json'
$view=& $obs -Mode InspectRun -RepoRoot $repo -PythonExe $python -RunScopePath $scope
Check ($view.status -ceq 'PREVIEW_ONLY_NOT_AUTHORIZED' -and -not $view.can_execute) 'INSPECT_READ_ONLY'
Check ($view.scope_window -ceq 'TEST_WINDOW' -and $view.head -ceq $head -and $view.observed_checkpoint_status -ceq 'ABSENT') 'CUSTOM_REPO_AND_WINDOW'
Check ($view.command_executable -ceq $python -and $view.command_arguments[-1] -ceq 'TEST_WINDOW') 'CUSTOM_PYTHON_AND_COMMAND'
$wrong=Get-Content -LiteralPath $scope -Raw|ConvertFrom-Json
$wrong.config_sha256='0'*64
$corrupt=WriteScope $wrong 'wrong_config.json'
MustReject {& $obs -Mode InspectRun -RepoRoot $repo -PythonExe $python -RunScopePath $corrupt} 'CONFIG_SHA_DRIFT_REJECTED'
$wrong=Get-Content -LiteralPath $scope -Raw|ConvertFrom-Json
$wrong.expected_head='0'*40
$corrupt=WriteScope $wrong 'wrong_head.json'
MustReject {& $obs -Mode InspectRun -RepoRoot $repo -PythonExe $python -RunScopePath $corrupt} 'GIT_HEAD_DRIFT_REJECTED'
$wrong=Get-Content -LiteralPath $scope -Raw|ConvertFrom-Json
$wrong.intent='HUMAN_APPROVED_SINGLE_WINDOW'
$corrupt=WriteScope $wrong 'wrong_intent.json'
MustReject {& $obs -Mode InspectRun -RepoRoot $repo -PythonExe $python -RunScopePath $corrupt} 'PRODUCTIVE_INTENT_REJECTED'
$wrong=Get-Content -LiteralPath $scope -Raw|ConvertFrom-Json
$wrong.window_id='ANOTHER_WINDOW'
$corrupt=WriteScope $wrong 'wrong_window.json'
MustReject {& $obs -Mode InspectRun -RepoRoot $repo -PythonExe $python -RunScopePath $corrupt} 'UNKNOWN_WINDOW_REJECTED'
$wrong=Get-Content -LiteralPath $scope -Raw|ConvertFrom-Json
$wrong.boundaries_path=Join-Path $work 'scope.json'
$corrupt=WriteScope $wrong 'outside_repo.json'
MustReject {& $obs -Mode InspectRun -RepoRoot $repo -PythonExe $python -RunScopePath $corrupt} 'OUTSIDE_REPO_BOUNDARY_REJECTED'
$fakeRepo=Join-Path $work 'preview_junction_repo'
[void][IO.Directory]::CreateDirectory($fakeRepo)
$external=Join-Path $work 'external_config'
[void][IO.Directory]::CreateDirectory($external)
$externalYaml=Join-Path $external 'external.yaml'
[IO.File]::WriteAllText($externalYaml,'source: redirected',[Text.UTF8Encoding]::new($false))
$fakeBound=Join-Path $fakeRepo 'boundaries.json'
[IO.File]::WriteAllText($fakeBound,([IO.File]::ReadAllText($bound)),[Text.UTF8Encoding]::new($false))
$junction=Join-Path $fakeRepo 'junction_inputs'
[void](New-Item -ItemType Junction -Path $junction -Target $external -ErrorAction Stop)
$wrong=Get-Content -LiteralPath $scope -Raw|ConvertFrom-Json
$wrong.boundaries_path=$fakeBound
$wrong.boundaries_sha256=Sha $fakeBound
$wrong.config_path=Join-Path $junction 'external.yaml'
$wrong.config_sha256=Sha $externalYaml
$redirectedScope=WriteScope $wrong 'junction_input.json'
$junctionRejected=$false
try{& $obs -Mode InspectRun -RepoRoot $fakeRepo -PythonExe $python -RunScopePath $redirectedScope|Out-Null}
catch{$junctionRejected=($_.Exception.Message -match 'REPARSE')}
Check $junctionRejected 'JUNCTION_PREVIEW_INPUT_REJECTED'
Check (-not [IO.Directory]::Exists((Join-Path $repo 'outputs'))) 'NO_HISTORICAL_OUTPUT_WRITTEN'
Check (@(& git -C $repo status --porcelain=v1).Count -eq 0) 'GIT_REPO_REMAINS_CLEAN'
$report=[ordered]@{schema='mars-r33-preview-portability-v1';status='PASS';assertions=$checks.Count;checks=@($checks);work=$work;production_runs=0}
[IO.File]::WriteAllText((Join-Path $work 'test_report.json'),($report|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
Write-Output ('TEST_FINAL_PASS='+$checks.Count)
Write-Output ('EVIDENCE_ROOT='+$work)