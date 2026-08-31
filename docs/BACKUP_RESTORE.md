# Backup and restore

Local Excel mode creates a timestamped workbook backup before every write. Back up the entire persistent `/data` volume regularly; it contains the working workbook, SQLite audit data, configuration, and images.

To restore local mode:

1. Stop the container.
2. Copy the current workbook and `inventory.db` somewhere safe.
3. Replace only the working workbook with the selected file from `/data/backups`.
4. Start the container and use **Force synchronization**.
5. Compare transaction history with workbook stock changes.

For OneDrive/SharePoint, use version history to restore the workbook. Pause update flows first, restore a version, confirm column mappings and unique IDs, then resume flows and force synchronization.

