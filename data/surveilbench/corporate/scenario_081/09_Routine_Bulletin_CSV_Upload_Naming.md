# Bulletin - CSV upload naming Revenue Systems asks teams to include the source system and date in CSV upload filenames. Use: `source_YYYY-MM-DD_description.csv` Example: `crm_2026-06-18_account_owner_refresh.csv` This naming convention helps the support queue match upload errors to the right source file.

## Examples

and Exceptions

If a file is regenerated after an error, add a short suffix such as

`_rerun1` rather than changing the date. Example: `crm_2026-06-18_account_owner_refresh_rerun1.csv`.
Do not include employee names, customer names, or free-text issue descriptions in the filename. Those details belong in the upload request body. Revenue Systems will use the filename, source system, and upload timestamp to match failed rows to the correct support ticket. Bulk uploads from vendors should follow the same convention after the file is received internally.
If the vendor file name is unclear, save a renamed working copy before upload.
Keep the original vendor file in the request attachment so Revenue Systems can compare it against the renamed working copy if an import problem appears later.

## Failed Upload Follow-Up

When an upload fails, Revenue Systems will ask for the renamed working copy, the original vendor attachment, and the error timestamp from the import screen.

Do not paste rows into the ticket body unless asked; row-level examples should be attached as a masked sample.
The naming convention is meant to help identify the import batch quickly, while the ticket still carries the approval history and explanation for why the upload was needed.
