param(
    [Parameter(Mandatory = $true)][string]$RequestPath,
    [Parameter(Mandatory = $true)][string]$ResultPath
)

$ErrorActionPreference = "Stop"

function Write-AtomicJson([hashtable]$Value) {
    $parent = Split-Path -Parent $ResultPath
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $temporary = "$ResultPath.partial"
    $Value | ConvertTo-Json -Depth 6 -Compress | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $ResultPath -Force
}

function Get-AttachmentIdentity(
    [string]$StoreId,
    [string]$EntryId,
    [int]$AttachmentIndex,
    [string]$AttachmentName
) {
    $canonical = @($StoreId, $EntryId, [string]$AttachmentIndex, $AttachmentName) -join [char]0x1f
    $bytes = [Text.Encoding]::UTF8.GetBytes($canonical)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-SafeFileName([string]$Name) {
    $leaf = [IO.Path]::GetFileName($Name)
    foreach ($character in [IO.Path]::GetInvalidFileNameChars()) {
        $leaf = $leaf.Replace([string]$character, "_")
    }
    $leaf = $leaf.Trim().TrimEnd(".")
    if ([string]::IsNullOrWhiteSpace($leaf)) {
        throw "The selected Outlook attachment has no usable filename."
    }
    $reservedStem = [IO.Path]::GetFileNameWithoutExtension($leaf).ToUpperInvariant()
    if ($reservedStem -match '^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') {
        $leaf = "_$leaf"
    }
    return $leaf
}

$outlook = $null
$namespace = $null
$inbox = $null
$items = $null
try {
    $request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $needle = [string]$request.subject_contains
    if ([string]::IsNullOrWhiteSpace($needle)) {
        throw "The Outlook subject search text is empty."
    }
    $outputFolder = [IO.Path]::GetFullPath([string]$request.output_folder)
    New-Item -ItemType Directory -Force -Path $outputFolder | Out-Null

    $outlook = New-Object -ComObject Outlook.Application
    $namespace = $outlook.GetNamespace("MAPI")
    # 6 is olFolderInbox. GetDefaultFolder deliberately targets only the
    # default profile's top-level Inbox; subfolders are not searched.
    $inbox = $namespace.GetDefaultFolder(6)
    $items = $inbox.Items
    $items.Sort("[ReceivedTime]", $true)

    foreach ($message in $items) {
        try {
            $subject = [string]$message.Subject
            if ($subject.IndexOf($needle, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
                continue
            }
            $supported = @()
            for ($index = 1; $index -le $message.Attachments.Count; $index++) {
                $attachment = $message.Attachments.Item($index)
                $extension = [IO.Path]::GetExtension([string]$attachment.FileName).ToLowerInvariant()
                if ($extension -eq ".csv" -or $extension -eq ".xlsx") {
                    $supported += [pscustomobject]@{ Index = $index; Attachment = $attachment }
                }
            }
            if ($supported.Count -eq 0) {
                continue
            }
            if ($supported.Count -gt 1) {
                throw "The newest matching Outlook email has more than one CSV/XLSX attachment. Keep exactly one supported data attachment on the message."
            }

            $selected = $supported[0]
            $attachment = $selected.Attachment
            $originalName = [string]$attachment.FileName
            $identity = Get-AttachmentIdentity `
                -StoreId ([string]$inbox.StoreID) `
                -EntryId ([string]$message.EntryID) `
                -AttachmentIndex ([int]$selected.Index) `
                -AttachmentName $originalName
            if (-not [bool]$request.force_reprocess -and
                $identity -eq [string]$request.last_processed_identity) {
                Write-AtomicJson @{
                    status = "already_processed"
                    message = "The newest qualifying Outlook attachment was already processed."
                }
                exit 0
            }

            $safeName = Get-SafeFileName $originalName
            $savePath = Join-Path $outputFolder $safeName
            if (Test-Path -LiteralPath $savePath) {
                $stem = [IO.Path]::GetFileNameWithoutExtension($safeName)
                $extension = [IO.Path]::GetExtension($safeName)
                $reserved = $false
                for ($suffix = 2; $suffix -lt 10000; $suffix++) {
                    $candidate = Join-Path $outputFolder "$stem ($suffix)$extension"
                    if (-not (Test-Path -LiteralPath $candidate)) {
                        $savePath = $candidate
                        $safeName = [IO.Path]::GetFileName($candidate)
                        $reserved = $true
                        break
                    }
                }
                if (-not $reserved) {
                    throw "Could not reserve a unique local filename for the Outlook attachment."
                }
            }
            $attachment.SaveAsFile($savePath)
            Write-AtomicJson @{
                status = "saved"
                saved_path = $savePath
                attachment_name = $originalName
                saved_name = $safeName
                identity = $identity
                store_id = [string]$inbox.StoreID
                entry_id = [string]$message.EntryID
                attachment_index = [int]$selected.Index
                subject = $subject
                received_at = $message.ReceivedTime.ToString("o")
            }
            exit 0
        }
        finally {
            if ($null -ne $attachment) { [Runtime.InteropServices.Marshal]::ReleaseComObject($attachment) | Out-Null }
            $attachment = $null
        }
    }
    Write-AtomicJson @{
        status = "no_match"
        message = "No matching Outlook email with exactly one CSV/XLSX attachment was found."
    }
}
catch {
    Write-AtomicJson @{ status = "error"; error = $_.Exception.Message }
    exit 1
}
finally {
    foreach ($object in @($items, $inbox, $namespace, $outlook)) {
        if ($null -ne $object) {
            try { [Runtime.InteropServices.Marshal]::ReleaseComObject($object) | Out-Null } catch {}
        }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
