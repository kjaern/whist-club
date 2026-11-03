# Historical Whist import: design notes

Development notes for the historical import. The interpretation rules sent to the vision model live in
`src/whistapp/prompts/historical-import.md` and `transcribe-score-sheet.md`; club-specific context
(nicknames, confirmed examples from a club's own nights) belongs in that club's AI notes, stored in the
database, never in the repository.

## Purpose

Transcribe historical handwritten score sheets into a readable Excel workbook and import the records into Whist. Preserve enough original hand-level facts to support future statistics (for example, how often each player bids SOL). Do not substitute manually maintained aggregate statistics for source records.

## Workbook presentation

- A structured **Hænder** sheet suitable for review and import, with players across columns, each player's per-hand change and running total, and bids.
- Separate **Melding** (bid type), **Meldt** (numeric target, where applicable), and **Stik** (actual tricks) into distinct fields.
- Represent VIP levels compactly as **VIP1**, **VIP2**, or **VIP3** in *Melding*. Do **not** add a separate *Niveau* column.
- Include *Melder*, *Makker*, and optional *Melder 2*, *Melder 3* fields. Only include additional bidder columns through the last one actually used in the evening.
- Several players declaring SOL in one hand: record the shared bid type once and the additional declarers in *Melder 2* / *Melder 3*. They are **not** partners.
- Player alias resolution happens in the import process (the club's member aliases), not in the workbook or the model output.
- Keep the workbook usable for filtering and calculation, but do not treat Excel as the canonical application data model.

## Excel input formats

- An `.xlsx` with **exactly one sheet**, `Hænder`, one hand per row, using the expected header schema.
- The application also accepts raw, literal transcription workbooks with multiple sheets when it can identify each player's score-change column. Those sheets are normalized into the same review model while the original workbook remains unchanged and viewable.
- `Melder 2`/`Melder 3` are the final columns, only if populated.
- A workbook is not import-ready merely because its money columns reconcile; required bid/partner/trick fields are checked too.

## Validation and highlighting

- Each hand's score changes sum to zero when all participants' changes are known.
- Running totals match previous totals plus hand changes; final totals match the last running totals and the sum of all changes.
- Bid amounts, bidder/partner assignments, and reconstructed tricks are validated against the scoring rules.
- **Yellow means genuine unresolved ambiguity or a value requiring manual attention.** Hand numbers are green when validation passes and red otherwise.
- Impossible, inconsistent, or unreadable entries are reported, not silently fixed.

## Application import

- Detailed historical evenings become normal game evenings/hands whenever the evidence permits, so hand-level statistics can be derived from the database.
- Historical evenings can be corrected or enriched later, including when new photographs become available.
- Source score changes and provenance are preserved so discrepancies with reconstructed scoring can be reviewed.
- An evening that cannot be reconstructed in full can be imported as final results only. Missing data must not count as zero, a loss, or a known bid outcome.
- Imports are previewed and validated first and committed atomically per evening.

## Game origin and statistics filtering

- Each evening has a persistent **origin**: `live` (entered through the application) or `imported`.
- Origin is independent of data completeness, and an imported evening stays `imported` when corrected.
- Statistics can be filtered by **All**, **Live**, or **Imported** (default All), applied consistently to all statistics.
- Imported data contributes only where the underlying facts are known: a known SOL declaration counts toward bid frequency even if its outcome is unknown, but not toward a success rate.
- Re-uploading the same evening should be detected and offered as an update rather than a duplicate.

## Filename date convention

- Name Excel files with an ISO play date, e.g. `whist_2025-01-01.xlsx`; the preview pre-fills the date with precision `day`.
- Only valid `YYYY-MM-DD` (also with `_` or `.` separators) is recognized. Upload time is never used as the play date.
- The inferred date stays editable and is not saved until the import is confirmed.

## Import review controls

- `Melder` and `Makker` offer the four names from the workbook's score columns; `Melding` offers the registered bid keys.
- `Gå med` allows several additional declarers for SOL, Ren sol and Bordlægger; validation rejects extra declarers for ordinary bids.
- Structured `Hænder` sheets and raw transcriptions normalize into the same editable review model. Uncertain and inconsistent values stay visible instead of aborting the preview.
- Names and hand values can be corrected; hands can be inserted, removed and reordered before commit. Exact rational scores remain the storage representation.
- Original source files, raw AI workbooks, `remarks.md` and the generated ZIP stay accessible from the saved evening; original photos stay in its photo viewer.
- Import corrections and hand operations are recorded in the evening change log.

## AI photo import

- The `/import` page accepts score-sheet photos in its AI-fotoimport tab and sends them to the configured OpenAI-compatible vision endpoint.
- Only the server administrator configures the LLM and decides per club whether AI import is allowed, since it uses the server's API key.
- The prompt consists of the two prompt files, the club's bid definitions with its configured values, and the club's AI notes (set by the server administrator on the club page).
- AI output is kept literal in a raw transcription workbook; a separate importer-compatible `Hænder` workbook is generated from it, and users review and correct the hands before commit.
- While the model runs, a server-sent event stream reports each stage; the trace stays visible before opening the review.
- On commit, original photos are attached to the evening and the workbooks, transcription, remarks and ZIP are kept as source files.
- Uploaded images are transmitted to the configured provider.
