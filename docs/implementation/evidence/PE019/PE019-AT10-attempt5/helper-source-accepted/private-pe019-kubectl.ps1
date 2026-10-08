$PE019Kubectl=(Resolve-Path 'tmp/tooling/kubectl.exe').Path
$PE019Kubeconfig=(Resolve-Path 'tmp/pe019-private/kubeconfig').Path
function Invoke-PrivateFinalKubectl([string[]]$Arguments,[string]$InputText=''){
 $pi=[Diagnostics.ProcessStartInfo]::new($PE019Kubectl)
 $pi.UseShellExecute=$false;$pi.CreateNoWindow=$true
 $pi.RedirectStandardInput=$true;$pi.RedirectStandardOutput=$true;$pi.RedirectStandardError=$true
 foreach($argument in @('--kubeconfig',$PE019Kubeconfig,'--context','kind-core-platform-final-019','--request-timeout=15s')+$Arguments){$pi.ArgumentList.Add($argument)}
 $process=[Diagnostics.Process]::Start($pi)
 try{
  $stdout=$process.StandardOutput.ReadToEndAsync();$stderr=$process.StandardError.ReadToEndAsync()
  $process.StandardInput.Write($InputText);$process.StandardInput.Close()
  if(-not $process.WaitForExit(45000)){$process.Kill($true);throw 'Final private Kubernetes timeout'}
  $result=$stdout.GetAwaiter().GetResult();$null=$stderr.GetAwaiter().GetResult()
  if($process.ExitCode -ne 0){throw 'Final Kubernetes operation failed; private diagnostics suppressed'}
  return $result
 }finally{$process.Dispose()}
}
