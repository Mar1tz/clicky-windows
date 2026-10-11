---
name: Convert a document to PDF
when: the user wants a document, spreadsheet or presentation turned into, saved as or exported to a PDF
---

## Steps
1. Use the file the user means - its full path is in the request. If there is none, ask which file; never guess.
2. Convert it with the app that made it, from PowerShell (run_command). Save the PDF next to the original, with the same name and .pdf:
   - Word (.docx, .doc, .rtf, .odt): `$w = New-Object -ComObject Word.Application; $d = $w.Documents.Open("FILE", $false, $true); $d.SaveAs2("PDF", 17); $d.Close($false); $w.Quit()`
   - Excel (.xlsx, .xls, .csv): `$x = New-Object -ComObject Excel.Application; $b = $x.Workbooks.Open("FILE"); $b.ExportAsFixedFormat(0, "PDF"); $b.Close($false); $x.Quit()`
   - PowerPoint (.pptx, .ppt): `$p = New-Object -ComObject PowerPoint.Application; $s = $p.Presentations.Open("FILE", $true, $false, $false); $s.SaveAs("PDF", 32); $s.Close(); $p.Quit()`
   - No Office on the PC: LibreOffice, if it is there - `soffice --headless --convert-to pdf --outdir "FOLDER" "FILE"`.
3. Check the PDF is there and not empty (list_dir).
4. Say where the PDF is. The original is never changed or deleted.

## What to know
- A PDF of the same name already there: add " (2)" rather than overwrite it.
