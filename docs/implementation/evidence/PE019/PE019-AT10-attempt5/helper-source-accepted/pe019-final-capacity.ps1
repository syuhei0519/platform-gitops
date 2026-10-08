param([string]$EvidenceDirectory='output/core-platform-v1-phase1-phase2-20261002/evidence')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
. './output/core-platform-v1-phase1-phase2-20261002/acceptance/private-pe019-kubectl.ps1'
$root=(Resolve-Path 'output/core-platform-v1-phase1-phase2-20261002').Path;$ev=(Resolve-Path $EvidenceDirectory).Path
$baseEvidence=Join-Path $root 'evidence'
if($ev -ne $baseEvidence -and -not $ev.StartsWith($baseEvidence+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Owned PE019 evidence directory required'}
if(Test-Path "$ev/PE019-AT10-window.json"){throw 'Final capacity window already started; inspect saved state instead of repeating'}
$profile=Get-Content -Raw "$ev/PE019-final-profile.json"|ConvertFrom-Json
$shortPreflight=($profile.purpose -eq 'auxiliary-measurement-preflight')
$duration=if($shortPreflight){[int]$profile.durationSec}else{900}
if($shortPreflight -and $duration -notin @(60,120)){throw 'Short preflight must be sixty or120 seconds'}
if(-not $shortPreflight){
 $gate=Join-Path $root 'evidence/PE019-AUX-PREFLIGHT-ACCEPTED.json'
 if(-not(Test-Path $gate)){throw 'User requires all short preflight conditions accepted before fifteen-minute trial'}
 $accepted=Get-Content -Raw $gate|ConvertFrom-Json
 if(-not $accepted.allConditionsAccepted){throw 'Short preflight acceptance incomplete; full trial prohibited'}
 foreach($hash in $accepted.helperHashes){if((Get-FileHash (Join-Path $root ('acceptance/'+$hash.file))).Hash.ToLower() -ne $hash.sha256){throw 'Accepted short-preflight scripts changed; full trial prohibited'}}
}
$ci=Get-Content -Raw "$ev/PE019-capacity-native-state.json"|ConvertFrom-Json
$build=@($ci.jobs|Where-Object {$_.name -eq 'container-build-oci' -and ($_.status -eq 'running' -or ($shortPreflight -and $_.status -eq 'success'))})
$hold=Get-Content -Raw "$root/evidence/USAGE-DEVELOPMENT-HOLD.json"|ConvertFrom-Json
if($hold.active -or $profile.testStarted -or (-not $shortPreflight -and $profile.durationSec -ne 900) -or $profile.requestsPerSecond -ne 1 -or $profile.httpTimeoutSec -ne 5 -or $ci.source -ne $profile.sourceBackend -or $build.Count -ne 1 -or ([DateTime]::UtcNow-[DateTime]$ci.utc).TotalSeconds -gt 30){throw 'Frozen profile and actual current source-bound OCI evidence required'}
foreach($hash in $profile.workerSourceHashes){if((Get-FileHash (Join-Path $root ('acceptance/'+$hash.file))).Hash.ToLower() -ne $hash.sha256){throw 'Predeclared measurement scripts changed'}}
$app=Invoke-PrivateFinalKubectl @('-n','argocd','get','application','lab-local-backend','-o','json')|ConvertFrom-Json
$config=Invoke-PrivateFinalKubectl @('-n','account','get','configmap','backend-config','-o','json')|ConvertFrom-Json
$deployment=Invoke-PrivateFinalKubectl @('-n','account','get','deployment','backend','-o','json')|ConvertFrom-Json
$endpoint=@($deployment.spec.template.spec.containers[0].env|Where-Object name -eq OTEL_EXPORTER_OTLP_ENDPOINT)
if($app.status.sync.revision -ne $profile.manifest -or $app.status.sync.status -ne 'Synced' -or $app.status.health.status -ne 'Healthy' -or $config.data.'telemetry-enabled' -ne 'true' -or $endpoint.Count -ne 1 -or $endpoint[0].value -ne 'http://otel-collector.observability.svc.cluster.local:4318' -or $deployment.spec.template.spec.containers[0].image -notmatch (':'+[regex]::Escape($profile.sourceBackend)+'@sha256:[0-9a-f]{64}$')){throw 'Actual frozen manifest/image with declarative enabled telemetry required before another window'}
$k=(Resolve-Path 'tmp/tooling/kubectl.exe').Path;$cfg=(Resolve-Path 'tmp/pe019-private/kubeconfig').Path
$pwsh=(Get-Process -Id $PID).Path
& './output/core-platform-v1-phase1-phase2-20261002/acceptance/read-pe019-resource-preflight.ps1' -ProfilePath "$ev/PE019-final-profile.json" -Output "$ev/PE019-resource-preflight.json"
$preflight=Get-Content -Raw "$ev/PE019-resource-preflight.json"|ConvertFrom-Json
if(-not $preflight.readyToStart){throw 'Actual resources must be sufficient before starting final capacity window'}
foreach($port in @(18049,18050)){if(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue){throw 'Owned capacity ports must be unused'}}
$forwards=@();$workers=@();$rows=[Collections.Generic.List[object]]::new();$owned=$null;$email=$null;$completed=$false
$prefix='pe019-load-'+[guid]::NewGuid().ToString('N')
try{
 foreach($route in @(@{ns='account';service='frontend';port='18049:80'},@{ns='observability';service='prometheus-server';port='18050:80'})){
  $forwards+=Start-Process -FilePath $k -ArgumentList @('--kubeconfig',$cfg,'--context','kind-core-platform-final-019','-n',$route.ns,'port-forward',('service/'+$route.service),$route.port,'--address=127.0.0.1') -WindowStyle Hidden -PassThru -RedirectStandardOutput "$ev/PE019-AT10-$($route.service)-forward.txt" -RedirectStandardError "$ev/PE019-AT10-$($route.service)-forward-error.txt"
 }
 $deadline=[DateTime]::UtcNow.AddSeconds(15)
 while(@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue|Where-Object {$_.LocalPort -in @(18049,18050)}).Count -ne 2){if(@($forwards|Where-Object HasExited).Count -or [DateTime]::UtcNow -gt $deadline){throw 'Final frontend/Prometheus forwards not ready'};Start-Sleep -Milliseconds 200}
 $start=[DateTime]::UtcNow;$end=$start.AddSeconds($duration)
 $trace=[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(16)).ToLower()
 Copy-Item -LiteralPath "$ev/PE019-final-profile.json" -Destination "$ev/PE019-AT10-profile-frozen.json"
 @{startUtc=$start.ToString('o');endUtc=$end.ToString('o');pipeline=$ci.pipeline;buildJob=$build[0].id;source=$ci.source;profileSha256=(Get-FileHash "$ev/PE019-AT10-profile-frozen.json").Hash.ToLower();traceId=$trace;ownedEmailPrefix=$prefix;durationSec=$duration;requests=$duration;frontendPort=18049;prometheusPort=18050;purpose=$profile.purpose;safeSimulatedError=[bool]$profile.safeSimulatedError;resourceIntervalSec=15}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-window.json"
 $profile.testStarted=$true;$profile|ConvertTo-Json -Depth 12|Set-Content "$ev/PE019-final-profile.json"
 foreach($worker in @('resource','fault')){
  $file=Join-Path $root "acceptance/pe019-capacity-$worker-worker.ps1"
  $workers+=Start-Process -FilePath $pwsh -ArgumentList @('-NoProfile','-File',$file,'-EvidenceDirectory',$ev) -WorkingDirectory 'C:\work\CorePlatform' -WindowStyle Hidden -PassThru -RedirectStandardOutput "$ev/PE019-AT10-$worker-worker-output.txt" -RedirectStandardError "$ev/PE019-AT10-$worker-worker-error.txt"
 }
 @{utc=[DateTime]::UtcNow.ToString('o');driverPID=$PID;workingDirectory='C:\work\CorePlatform';hostExecutable=$pwsh;noProfile=$true;workerPIDs=@($workers.Id);forwardPIDs=@($forwards.Id)}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-worker-processes.json"
 $watch=[Diagnostics.Stopwatch]::StartNew()
 for($i=0;$i -lt $duration;$i++){
  $wait=1000*$i-$watch.Elapsed.TotalMilliseconds;if($wait -gt 0){Start-Sleep -Milliseconds ([int]$wait)}
  foreach($workerResult in @('resource','fault')){if(Test-Path "$ev/PE019-AT10-$workerResult-worker.json"){$result=Get-Content -Raw "$ev/PE019-AT10-$workerResult-worker.json"|ConvertFrom-Json;if(($workerResult -eq 'resource' -and -not $result.thresholdsPassed) -or ($workerResult -eq 'fault' -and -not $result.pass)){throw 'Frozen worker measurement failed'}}}
  $step=$i%4;$method=@('POST','GET','PUT','DELETE')[$step];$expected=@(201,200,200,204)[$step]
  if($step -eq 0){$email=$prefix+'-'+$i+'@example.test';$path='/api/accounts'}elseif($owned){$path='/api/accounts/'+$owned}else{$path='/api/accounts';$method='GET';$expected=200}
  $span=[Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(8)).ToLower()
  $request=@{Uri=('http://127.0.0.1:18049'+$path);Method=$method;TimeoutSec=5;SkipHttpErrorCheck=$true;Headers=@{traceparent=('00-'+$trace+'-'+$span+'-01')}}
  if($method -in @('POST','PUT')){$request.ContentType='application/json';$request.Body=(@{name='PE019 capacity';email=$email}|ConvertTo-Json -Compress)}
  $status=0;$latency=[Diagnostics.Stopwatch]::StartNew()
  try{$r=Invoke-WebRequest @request;$status=[int]$r.StatusCode;if($method -eq 'POST' -and $status -eq 201){$owned=[int]($r.Content|ConvertFrom-Json).id};if($method -eq 'DELETE' -and $status -eq 204){$owned=$null}}catch{}
  $rows.Add(@{i=$i;utc=[DateTime]::UtcNow.ToString('o');method=$method;status=$status;expected=$expected;elapsedMs=$latency.Elapsed.TotalMilliseconds;scheduleLagMs=[Math]::Max(0,$watch.Elapsed.TotalMilliseconds-1000*$i);success=($status -eq $expected);traceId=$trace;spanId=$span})
  if($i%30 -eq 0){@{utc=[DateTime]::UtcNow.ToString('o');request=$i;elapsedSec=$watch.Elapsed.TotalSeconds;phase='running';pipeline=$ci.pipeline}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-live.json"}
 }
 $wait=1000*$duration-$watch.Elapsed.TotalMilliseconds;if($wait -gt 0){Start-Sleep -Milliseconds ([int]$wait)}
 foreach($worker in $workers){if(-not $worker.WaitForExit(30000)){throw 'Final worker did not reach terminal boundary'};if($worker.ExitCode -ne 0){throw 'Final worker failed'}}
 $resource=Get-Content -Raw "$ev/PE019-AT10-resource-worker.json"|ConvertFrom-Json
 $fault=Get-Content -Raw "$ev/PE019-AT10-fault-worker.json"|ConvertFrom-Json
 $success=100*@($rows|Where-Object success).Count/$duration
 $requiredSamples=if($shortPreflight){[int]($duration/15)}else{50}
 $pass=$success -ge 99 -and $rows.Count -eq $duration -and $resource.thresholdsPassed -and $resource.samples -ge $requiredSamples -and $fault.pass
 if(-not $shortPreflight){$pass=$pass -and $fault.faultSec -ge 60 -and $fault.recoverySec -le 120}
 @{utc=[DateTime]::UtcNow.ToString('o');capacityRuntimePassed=$pass;requests=$rows.Count;crudSuccessPercent=$success;elapsedSec=$watch.Elapsed.TotalSeconds;pipeline=$ci.pipeline;fault=$fault;resource=$resource;actualOCITrivySBOMOverlapAuditStillRequired=$true;currentNRStoredTraceStillRequiresEvidence=$true;fullAT10Accepted=$false}|ConvertTo-Json -Depth 12|Set-Content "$ev/PE019-AT10-runtime-result.json"
 if(-not $pass){throw 'Actual fixed capacity threshold failed; do not relax or replay'}
 $completed=$true
}catch{
 @{utc=[DateTime]::UtcNow.ToString('o');stage='capacity-driver';type=$_.Exception.GetType().FullName;id=$_.FullyQualifiedErrorId;scriptStack=$_.ScriptStackTrace;exitCode=1}|ConvertTo-Json -Depth 6|Set-Content "$ev/PE019-AT10-driver-failure-location.json"
 throw 'Capacity driver failed; preserve safe failure metadata'
}finally{
 if(-not $completed){@{utc=[DateTime]::UtcNow.ToString('o');phase='aborted';requests=$rows.Count;thresholdsNotAccepted=$true}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-abort.json"}
 $cleanup=Invoke-PrivateFinalKubectl @('-n','account','exec','-i','postgresql-0','--','psql','-U','postgres','-d','account','-Atq','-v','ON_ERROR_STOP=1') "DELETE FROM accounts WHERE email LIKE '$prefix-%@example.test'; SELECT count(*) FROM accounts WHERE email LIKE '$prefix-%@example.test';"
 @{utc=[DateTime]::UtcNow.ToString('o');ownedEmailPrefix=$prefix;remainingOwnedRows=[int]$cleanup.Trim();onlyOwnedSyntheticDataTouched=$true}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-owned-cleanup.json"
 $workerExit=@()
 foreach($worker in $workers){$forced=$false;if(-not $worker.WaitForExit(20000)){Stop-Process -Id $worker.Id;$forced=$true;$null=$worker.WaitForExit(5000)};$worker.Refresh();$workerExit+=@{processId=$worker.Id;exitCode=$worker.ExitCode;forcedTermination=$forced;hasExited=$worker.HasExited}}
 foreach($forward in $forwards){if(-not $forward.HasExited){Stop-Process -Id $forward.Id};$null=$forward.WaitForExit(5000)}
 @{utc=[DateTime]::UtcNow.ToString('o');workers=$workerExit;driverResultSaved=$true;driverExitCodeExpected=if($completed){0}else{1};forwardPIDs=@($forwards.Id);allOwnedProcessesExited=(@($workers|Where-Object {-not $_.HasExited}).Count -eq 0 -and @($forwards|Where-Object {-not $_.HasExited}).Count -eq 0)}|ConvertTo-Json -Depth 6|Set-Content "$ev/PE019-AT10-process-cleanup.json"
 $rows.ToArray()|ConvertTo-Json -Depth 8|Set-Content "$ev/PE019-AT10-crud-rows.json"
 @{utc=[DateTime]::UtcNow.ToString('o');ports=@(18049,18050);remainingListeners=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue|Where-Object {$_.LocalPort -in @(18049,18050)}|Select-Object LocalPort);completed=$completed}|ConvertTo-Json|Set-Content "$ev/PE019-AT10-forwards-closed.json"
}
