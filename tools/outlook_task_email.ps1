<#
.SYNOPSIS
    Creates or sends Outlook task summary emails from a JSON payload.
.PARAMETER PayloadPath
    Path to JSON containing a messages array with to, subject, and html_body fields.
.PARAMETER Send
    Send messages immediately. Without this switch, messages open as Outlook drafts.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadPath,
    [switch]$Send
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PayloadPath)) {
    throw "Payload file not found: $PayloadPath"
}

$payload = Get-Content -Path $PayloadPath -Raw | ConvertFrom-Json
if (-not $payload.messages -or $payload.messages.Count -eq 0) {
    throw "No messages found in payload."
}

$outlook = New-Object -ComObject Outlook.Application

foreach ($message in $payload.messages) {
    if (-not $message.to) {
        continue
    }
    $mail = $outlook.CreateItem(0)
    $mail.To = [string]$message.to
    $mail.Subject = [string]$message.subject
    $mail.HTMLBody = [string]$message.html_body
    if ($Send) {
        $mail.Send()
    } else {
        $mail.Display()
    }
}

Remove-Item -Path $PayloadPath -Force -ErrorAction SilentlyContinue
