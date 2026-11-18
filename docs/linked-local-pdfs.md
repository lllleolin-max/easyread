# Linked local PDFs

Local PDFs can be referenced without creating a second PDF in the EasyRead
library. Folder and Zotero bulk imports expose a checkbox; the default remains
copying. Bulk imports prepare original pages without enabling translation.

```sh
easyread import /absolute/path/paper.pdf --link --no-translate
```

The Python API can create a catalog entry without preparing pages immediately:

```python
from pathlib import Path
from easyread.library.library import Library

library = Library(Path("/path/to/EasyRead/library"))
workspace, fresh = library.import_pdf_path(Path("/path/to/paper.pdf"), link=True)
original_pdf = workspace.source_pdf
```

`import_pdf_path(source, meta=None, *, link=False)` returns `(Workspace, bool)`.
PDF content determines the ID in both modes. Importing an existing ID never
changes its storage mode, paper content, notes, progress or item data. A linked
catalog entry without a page cache prepares that cache on its first reader open.
`source_pdf_path` in `paper.json` metadata stores the canonical external path;
the public reader URL remains `/p/<id>/source.pdf`.

Converting an existing copy is a separate, explicit operation:

```python
library.convert_pdf_to_link(workspace.id, Path("/path/to/paper.pdf"), remove_copy=True)
```

Conversion checks the external PDF's full SHA-256 against the saved digest and
the actual library copy before changing metadata. `remove_copy=True` is required
to remove an existing regular `source.pdf`; other saved data stays intact.
Targets inside the EasyRead library and symlinked library copies are rejected.
Conversion is idempotent for an already linked entry with the same content.

Bulk HTTP request (folder or Zotero source):

```json
{
  "source": "folder",
  "path": "/absolute/path/papers",
  "tags": true,
  "fetch": false,
  "link": true
}
```

Send this JSON to `POST /api/migrate/start` with the normal `X-Token` header.
`link` defaults to false. Linked mode accepts local readable PDF files only;
network fetching cannot be combined with it. Entries without a local PDF are
skipped, and per-file errors are recorded in the bulk result.

EasyRead checks linked originals against their saved SHA-256. If the recorded
file disappears or its contents change, the reader can use an identical copy
already indexed in a mapped folder. Folder sync updates the link when it finds
a verified replacement for a mapped original. If no matching copy is available,
the reader shows the recorded path so you can restore the file.

Generated pages, extracts and figures stay inside EasyRead. Trash, restore,
purge and cloud-library cleanup operate on EasyRead's own files. Cloud-library
copy and merge preserve the external reference. Reading the original on another
computer requires access to the original or to an indexed copy with the same contents.
