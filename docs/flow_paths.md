# Flow paths

System > Paths configures a root and its ASAP, GSCM, Outlook, Local, Python
and Web subfolders. The default is metronome/flows beside governance.db; DG_FLOWS_ROOT
can supply a bootstrap value, and a saved setting takes precedence.

Saving a root does not move files or change existing flow destinations. Check
impact to see which flows need relocation. Enforce paths for existing flows
rejects destinations outside their source folder, Local inputs outside Local,
Python-script Flow scripts outside Python, and transformations outside the
root. Changes wait for queued/active runs.

Enforcement is off by default for a staged migration. It restricts configured
application paths, not the filesystem permissions of transformation processes.
Browser profiles, credentials and private legacy recovery stores retain their
existing ownership. Historic recovery jobs retain their frozen configuration.

Uploaded transformation scripts are staged under .metronome/uploads in unique
directories; Python-source scripts uploaded from the builder are staged under
Python/.uploads so enforcement accepts them.
No user files are moved or deleted by changing these settings.

## Managed flow folders

New flows created in the builder receive a source folder containing a sanitized
flow name only, with Downloads, Scripts and an ownership manifest. The internal
flow ID remains in metadata for ownership checks; it is never appended to the
folder name. Saving a renamed flow moves its existing folder and contents, then
updates managed output, transformation and historical file references. Existing
ID-suffixed folders switch to name-only on their next successful Save, including
a save with no name change. Python and JSON are refreshed before Save returns. Deleting a paused flow preserves all
files and marks its manifest deleted. Folder creation refuses existing foreign
folders and compensates failures only when its new directories are still empty.

Legacy flows keep their current destination input. Adopt managed folder changes
future output and leaves historic downloads/recovery paths intact. It waits for
active runs and pipeline reservations. The source file of a Local flow never
moves: its visible folder does not publish the private source snapshots.

## Layout and private storage

Repair folder layout validates the flow ID and layout version before creating
missing Downloads/Scripts directories. An existing unmarked or linked folder
is refused. Repairing a missing whole folder is an explicit action and cannot
restore missing download or script contents. Active runs block repair.

Saving a managed transformation copies an external/uploaded script into Scripts
under a unique name. Existing versions and source bytes remain unchanged. Old
managed configurations continue using their saved script until save or repair.

New managed Direct and Local runs use `<root>/.metronome/artifacts` with a
host-and-root store identity shared across worker profiles. Run-folder output
continues under Downloads. Historical profile stores and their exact recovery
identities are preserved; no artifacts are migrated. Resume copies validated
historical Direct artifacts into the new bundle before publishing. Workers
advertise concrete shared roots from queued jobs; older workers cannot claim
new shared-store jobs. All workers must be upgraded before enabling those jobs.

### Name collisions and rename recovery

The existing filename cleanup still applies: unsupported filename characters are
removed, whitespace is normalized, names are limited to 72 characters, and Windows
reserved names receive a `Flow ` prefix. Names that resolve to an occupied folder
(including case-only collisions with a different folder) are rejected. Choose a
different flow name; Metronome never adds the ID or overwrites the occupied folder.

Rename waits for active/queued runs and recording sessions. A standalone process
holding the Flow execution lock also prevents Save. If the move or database commit
fails, the old folder and saved settings are restored and the form keeps the edits
for retry. An interrupted move is reconciled from its ownership marker on the next
Save. Folder access must be available. No directories are merged or deleted.

If another Flow uses a file inside the folder, update that dependency before
renaming. External consumers (for example a Power BI file connection, an operator's
shortcut or a manually configured Task Scheduler action) must use the new path;
Metronome cannot rewrite configuration in those external applications. Archived
Python versions preserve their original configuration and remain historical copies.
