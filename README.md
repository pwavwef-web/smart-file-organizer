# Smart File Organizer

A Windows-friendly Python organizer that previews every decision before moving anything. It uses explicit local rules first, optionally asks Gemini about ambiguous files, routes each file to the **real Windows system folder** it belongs in (Videos, Music, Pictures, Documents) or a catch-all Library, avoids overwriting duplicates, records every move, and can undo the latest batch.

## Safety model

- `scan` and `watch` are preview-only by default.
- Moving files requires the explicit `--apply` flag.
- Name collisions become `name (1).ext`; existing files are never overwritten.
- Byte-identical duplicates are detected by SHA-256 and handled by the `on_duplicate` policy instead of cluttering your folders.
- Files already sitting in their correct destination are skipped, so the watcher never re-moves or churns sorted files.
- No command deletes files.
- `undo` restores the most recent move batch.
- Shortcuts and system files (`.lnk`, `.url`, `.ini`) are ignored.
- Gemini sees a file only when no local keyword rule matches. The controls are in `config.json`.

## Where files go: destination roots

Destinations are written as templates that begin with a **root token** in braces, followed by an optional subpath:

| Token | Resolves to (via the Win32 Known Folder API) |
|-------|----------------------------------------------|
| `{Videos}` | Your real Videos folder — even if you relocated it to another drive |
| `{Music}` | Your real Music folder |
| `{Pictures}` | Your real Pictures folder |
| `{Documents}` | Your real Documents folder |
| `{Downloads}`, `{Desktop}`, `{Home}` | The corresponding known folders |
| `{Library}` | The catch-all in `library_root` (default `%USERPROFILE%\Sorted Files`) |

Because roots are resolved through the Windows Known Folder API, the organizer honours folders you have moved to another drive instead of guessing `%USERPROFILE%\Videos`. Run `python -m smart_sorter check` to see exactly where every root resolves on your machine. You can override or add roots with a `locations` block in `config.json`.

A destination with no leading `{Root}` (e.g. `"Documents\\Career\\CVs"`) is treated as a path under `{Library}` for backward compatibility.

### Date and format tokens

Subpaths may contain tokens that are filled in per file, so media groups itself by date:

- `{yyyy}` / `{year}`, `{mm}` / `{month}`, `{dd}` / `{day}`, `{yyyy-mm}`
- `{ext}`, `{category}`

For example, photos default to `{Pictures}\Photos\{yyyy}\{yyyy-mm}`, and for JPEG/TIFF the date comes from the **EXIF capture time** (falling back to the file's modified time). Unknown tokens are rejected when the config loads, so typos fail fast.

### Duplicate policy

`on_duplicate` controls what happens when an identical file already occupies the target path:

- `separate` (default) — route the duplicate to `{Library}\Duplicates\<Category>` so your main folders stay clean.
- `skip` — leave the duplicate in place; nothing is moved.
- `version` — keep both as `name (1).ext`.

Duplicate detection compares the incoming file against the file it would collide with at the destination (same name, same folder) — the usual "downloaded it twice" case.

## First run

Open PowerShell in this folder and run:

```powershell
python -m smart_sorter check
python -m smart_sorter --config config.sample.json scan
```

The sample config sorts `sample_inbox` into `sample_sorted` (with its roots redirected there) so you can see multi-folder routing without touching your real folders. Nothing moves in preview.

When the suggestions look right, run one deliberate batch against your real config:

```powershell
python -m smart_sorter scan --apply
```

To continuously sort new files, run `watch-live.ps1`; it asks you to type `LIVE` first. `watch-preview.ps1` continuously reports decisions without moving files.

To restore the latest batch:

```powershell
python -m smart_sorter undo
```

## Always-on auto-sort

To keep sorting automatically, register a logon task that runs the live watcher:

```powershell
$py = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $py -Argument "-m smart_sorter watch --apply" -WorkingDirectory (Get-Location).Path
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
Register-ScheduledTask -TaskName "SmartFileOrganizer-AutoSort" -Action $action -Trigger $trigger -Force
```

Pause it any time with `Disable-ScheduledTask -TaskName SmartFileOrganizer-AutoSort`, or remove it with `Unregister-ScheduledTask`.

## Gemini setup (optional)

The organizer works without Gemini using local keywords and extensions. To enable content-aware classification for ambiguous files, create a Gemini API key in Google AI Studio and set it for the current PowerShell session:

```powershell
$env:GEMINI_API_KEY = "your-key-here"
python -m smart_sorter check
```

To save the key for future Windows sessions:

```powershell
[Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "your-key-here", "User")
```

Do not paste the key into `config.json`. Restart PowerShell after saving it.

Gemini is currently configured as `gemini-3.6-flash`. Images or PDFs up to 12 MB and up to 8,000 characters of locally extracted document text may be sent only when local rules are inconclusive. Set `enabled`, `send_images`, `send_pdf_files`, or `send_document_text` to `false` in `config.json` for stricter privacy.

### Gemini content subfolders

With `gemini.suggest_subfolder` set to `true`, Gemini names a short content subfolder for the file. This is most useful for screenshots: a terminal capture is filed under `Pictures\Screenshots\{yyyy}\Terminal`, a chat under `…\Chat`, a code capture under `…\Code`, and so on. Screenshots always stay in the Screenshots bucket — Gemini only names the subfolder, never re-buckets them. The label is sanitized to a single safe folder name (letters, numbers, spaces).

## Desktop notifications

When files are moved in `--apply`/live mode, a Windows toast summarizes the batch and the folder each file landed in (e.g. `Moved Screenshot… -> Pictures\Screenshots\2026\Terminal`). One toast is shown per batch, listing up to `notifications.max_files` files. Turn it off with `notifications.enabled: false`. Notifications are best-effort and never block or crash sorting.

## Customize destinations

Edit `config.json`:

- `locations` — add or override named roots, e.g. `{ "Library": "D:\\Archive" }`.
- `default_location` — the root used for destinations without a `{Root}` token (default `Library`).
- `on_duplicate` — `separate`, `skip`, or `version`.
- `rules` — high-priority keyword rules; destinations use the token syntax above. Add project rules above general ones.

PDF text extraction is optional. Install `pypdf` if you want local PDF keyword matching before Gemini:

```powershell
python -m pip install pypdf
```

Without it, PDFs still sort by filename, Gemini (if enabled), or extension.
