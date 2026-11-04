# NAS authoring service

`server.py` serves `scrolls.html` and the shared Verse catalog. Use one server
process per data directory. The Unreal importer reads `GET /api/verses` and
continues to perform native typed validation before importing assets.

## Data and deployment

Set `APP_DIR` to the app checkout and `DATA_DIR` to a persistent Docker volume or
NAS bind mount. Set `PORT` to the desired listener port, normally 8088. Configure
`AUTH_TOKEN` through the existing private deployment settings, never in Git.
The app does not create a persistent Docker volume itself. Verify the mount
before recreating a container. A restart retains the container's writable layer;
a recreation may discard it.

The inspected NAS deployment clones this repository into `/app`, then starts
`python server.py`. Its startup command pulls Git updates on restart. Updating
the default branch therefore affects its next restart. Keep a known-good commit
and an authenticated catalog backup before updating it.

Update the server and browser together. An old browser's unconditional writes
receive HTTP 428. Reload the browser to use the new revision-aware client.
Keep local drafts or export a backup before changing devices or clearing storage.

Use trusted HTTPS termination or an encrypted private connection for access.
The Python server does not terminate TLS. Plain HTTP on a LAN still carries the
catalog and authentication header without transport encryption.

## Saving and conflicts

1. Open Sync, enter the access token, and Load from NAS.
2. Edit Verses or Gods. Form saves create local drafts.
3. Save to NAS publishes the catalog using the revision that was loaded.
4. If another device saved first, the NAS returns 412 and retains its catalog.
   Local drafts remain available across a page reload.
5. Download a local backup before replacing drafts with the newer NAS catalog.
   Manual Load offers that backup before discarding conflicting drafts.
   Reapply the desired edits to the latest version and save again.

The client never obtains a fresh revision just to overwrite it with stale data.
Unknown runtime fields, policy arrays, God extensions, and catalog metadata are
preserved. Unreal remains the authority for supported runtime semantics.
Legacy local drafts without a recorded revision require backup and review.

Tokens remain in page memory. Existing browser settings are migrated to remove
stored tokens; tokens must be entered again after closing or reloading the page.
Catalog drafts and their editing baseline remain in local browser storage.

## API contract

- GET/HEAD `/api/verses`: authenticated catalog and strong `ETag`; 404 if absent.
- PUT/POST `/api/verses`: `X-Auth-Token`, JSON body, exact `If-Match` from GET.
- First save: use `If-None-Match: *` only when no catalog exists.
- 428: missing revision; 412: stale revision; 400: invalid JSON/catalog/precondition.
- Authentication uses headers only. Query-string tokens are not accepted or logged.
- The static server exposes only app assets. Data files, source, and Git metadata
  cannot be fetched through static paths.

The revision comparison and atomic replacement use one process-wide lock. Do not
run multiple writers against the data directory or edit the file externally.
No automatic merge, data deletion, Unreal import, or Perforce submit occurs here.

## Validation

Run from the repository root:

```text
python -m unittest discover -s tests -v
node tests/test-sync.mjs
node tests/test-runtime.mjs
```

The runtime fixture is synthetic and tests serialization, not gameplay semantics.
Pass a private catalog path as the runtime test's first argument to verify all
current records locally. Do not commit private catalogs, tokens, or NAS settings.
