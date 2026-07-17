# Windows toast: "The Brief: N drafts ready for review" — clicking it opens
# the review console (protocol activation to localhost; the scheduled job
# starts the console server before firing this).
param([string]$Message = "Drafts ready for review")

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$xml = @"
<toast activationType="protocol" launch="http://localhost:8377/">
  <visual>
    <binding template="ToastGeneric">
      <text>The Brief</text>
      <text>$Message</text>
      <text placement="attribution">PRELUDE editorial — click to open the review console</text>
    </binding>
  </visual>
</toast>
"@

$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = New-Object Windows.UI.Notifications.ToastNotification($doc)
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
