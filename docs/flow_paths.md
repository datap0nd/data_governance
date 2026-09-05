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
