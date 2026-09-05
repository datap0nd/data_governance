# Plan 3 — Owned layout and new shared artifacts

## Goal
Managed folders contain Downloads, Scripts and flow.json. New managed private
runs use root-owned shared storage; historic recovery remains readable.

## Current state
_prepare_run_folder passes run path to register_folder; retention.execute_ops
gets storage parent. private_target_root verifies an exact marker dictionary.
Absolute artifact/job/retention paths and profile-derived IDs are persisted.

## Design
- Verify folder ownership/version before repairing Downloads/Scripts. Reject
  linked directories/markers and foreign IDs. Whole-folder recreation is explicit.
- Run-folder output goes in Downloads; Direct publishes deliverables there while
  immutable originals/transformed CSVs stay private. Local snapshots stay private.
- Optional store-root arguments preserve legacy helper behavior. New managed jobs
  carry <root>/.metronome/artifacts; derive ID from host plus resolved store root.
- Apply the selected store consistently to creation, artifact metadata, resume
  and SQL retry. Historical jobs use exact old producer identity and files.
  Do not move stores, rewrite paths or advertise unverified previous IDs.
- Stage uploads under .metronome/uploads/UUID; copy into Scripts on save.
  Uploading never saves a flow or moves a shared source script.
- Managed transforms execute from Scripts. Adoption can copy legacy scripts while
  enforcement is off. Reserve filenames exclusively.
- List polling uses DB-known state; explicit details/repair may inspect disk.

## Step-by-step
Layout status/repair → script save/adoption → shared store helper/worker routing →
legacy recovery verification → transformation/Local/operator docs.

## Migration and rollout
No historical artifact move. Adoption affects future output. Reverting code
leaves new files intact; recover new-policy runs using a compatible version.

## Risks
Shared store does not remove SQL/publish reservations or establish authorization.
Never broaden retention beyond owned run children.

## Acceptance criteria
Run-path registration, marker gates, cross-profile new-store identity, exact old
recovery routing, checksum validation, upload byte preservation, link/foreign
repair refusal and zero historic file movement/deletion.

