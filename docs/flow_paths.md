# Flow paths

System > Paths configures a root and its ASAP, GSCM, Outlook, Local and Web
subfolders. The default is metronome/flows beside governance.db; DG_FLOWS_ROOT
can supply a bootstrap value, and a saved setting takes precedence.

Saving a root does not move files or change existing flow destinations. Check
impact to see which flows need relocation. Enforce paths for existing flows
rejects destinations outside their source folder, Local inputs outside Local,
and transformations outside the root. Changes wait for queued/active runs.

Enforcement is off by default for a staged migration. It restricts configured
application paths, not the filesystem permissions of transformation processes.
Browser profiles, credentials and private legacy recovery stores retain their
existing ownership. Historic recovery jobs retain their frozen configuration.

Uploaded scripts are staged under .metronome/uploads in unique directories.
No user files are moved or deleted by changing these settings.

## Managed flow folders

New flows created in the builder receive a source folder containing a sanitized
flow name and stable ID, with Downloads, Scripts and an ownership manifest.
Display-name edits keep that path stable. Deleting a paused flow preserves all
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
