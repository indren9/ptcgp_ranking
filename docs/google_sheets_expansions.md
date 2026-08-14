# Google Sheets expansion catalog

The public Pocket expansion catalog is generated from Limitless once per day by
`.github/workflows/update-expansion-catalog.yml`. The workflow commits the file
only when the list changes.

Import the two-column catalog into a dedicated Google Sheets tab with:

```text
=IMPORTDATA("https://raw.githubusercontent.com/indren9/ptcgp_ranking/main/public/expansions_pocket_standard.csv")
```

The repository and the raw CSV must be publicly readable for `IMPORTDATA` to
work without authentication. Google Sheets checks `IMPORTDATA` sources for
updates periodically while the spreadsheet is open.

To refresh the catalog manually, open the repository's **Actions** page, select
**Update public expansion catalog**, choose **Run workflow**, and wait for the
workflow to finish.

The workflow needs **Settings > Actions > General > Workflow permissions > Read
and write permissions** so it can commit a changed catalog.
