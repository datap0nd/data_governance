<#
.SYNOPSIS
    Creates or sends Outlook task summary emails from a JSON payload.
.PARAMETER PayloadPath
    Path to JSON containing a messages array with to, subject, html_body, and
    optional inline_images fields. Each inline image has path and cid fields.
.PARAMETER ReceiptPath
    Atomic JSON receipt written after Outlook accepts Send/Display calls.
.PARAMETER Send
    Send messages immediately. Without this switch, messages open as Outlook drafts.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$PayloadPath,
    [Parameter(Mandatory = $true)]
    [string]$ReceiptPath,
    [switch]$Send
)

$ErrorActionPreference = "Stop"
$payload = $null
$submittedCount = 0
$receiptStatus = "failed"
$receiptError = $null

try {
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
        foreach ($inlineImage in @($message.inline_images)) {
            $imagePath = [string]$inlineImage.path
            $contentId = [string]$inlineImage.cid
            if (-not $imagePath -or -not $contentId) {
                continue
            }
            if (-not (Test-Path -LiteralPath $imagePath -PathType Leaf)) {
                throw "Inline image not found: $imagePath"
            }
            $attachment = $mail.Attachments.Add($imagePath)
            $attachment.PropertyAccessor.SetProperty(
                "http://schemas.microsoft.com/mapi/proptag/0x3712001F",
                $contentId
            )
            $attachment.PropertyAccessor.SetProperty(
                "http://schemas.microsoft.com/mapi/proptag/0x7FFE000B",
                $true
            )
        }
        $mail.HTMLBody = [string]$message.html_body
        if ($Send) {
            $mail.Send()
        } else {
            $mail.Display()
        }
        $submittedCount += 1
    }
    $receiptStatus = "submitted"
}
catch {
    $receiptError = $_.Exception.Message
}
finally {
    $receipt = @{
        dispatch_id = $(if ($payload) { $payload.dispatch_id } else { $null })
        dispatch_token = $(if ($payload) { $payload.dispatch_token } else { $null })
        status = $receiptStatus
        submitted_count = $submittedCount
        submitted_at = (Get-Date).ToUniversalTime().ToString("o")
        error = $receiptError
    }
    $receiptDirectory = Split-Path -Parent $ReceiptPath
    if ($receiptDirectory -and -not (Test-Path -LiteralPath $receiptDirectory)) {
        New-Item -ItemType Directory -Path $receiptDirectory -Force | Out-Null
    }
    $temporaryReceipt = "$ReceiptPath.tmp-$PID"
    $receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporaryReceipt -Encoding UTF8
    Move-Item -LiteralPath $temporaryReceipt -Destination $ReceiptPath -Force
    Remove-Item -LiteralPath $PayloadPath -Force -ErrorAction SilentlyContinue
}

if ($receiptStatus -ne "submitted") {
    throw $receiptError
}
