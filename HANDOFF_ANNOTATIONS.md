# Admin role and user management

Requested on 2026-08-09: "first work on implimenting teh admin".

The instance had no notion of privilege at all - every account was equal, so
there was no one who could remove a stale or unwanted account, and the only
way to do it was direct psql access.

## Done: admin role

- `users.is_admin` boolean column, migration `d5b8f21ac640`
- Bootstrap: earliest-created account (id=1) becomes admin automatically
- `require_admin` dependency in `routers/auth.py` - 403 Forbidden for non-admins
- `GET /api/auth/users` - list all users (admin only)
- `PATCH /api/auth/users/{user_id}` - update user (promote/demote admin)
- `DELETE /api/auth/users/{user_id}` - delete user (admin only)
- `UserUpdate` schema for partial updates
- `UserResponse` includes `is_admin` field

## Using the admin role

The admin role is required for user management operations. Non-admin users receive
a 403 "Admin privilege required" error when attempting these actions.

### Promoting a user to admin

Via psql (replace `username` with the target user):

```sql
UPDATE users SET is_admin = true WHERE username = 'username';
```

### Admin endpoints

Once logged in as admin, the following endpoints are available:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/auth/users` | List all users |
| PATCH | `/api/auth/users/{user_id}` | Update user (set `is_admin` to true/false) |
| DELETE | `/api/auth/users/{user_id}` | Delete a user |

### Example API calls

List all users:
```bash
curl -H "Authorization: Bearer <admin_token>" http://localhost:8000/api/auth/users
```

Promote a user:
```bash
curl -X PATCH -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"is_admin": true}' \
  http://localhost:8000/api/auth/users/2
```

Delete a user:
```bash
curl -X DELETE -H "Authorization: Bearer <admin_token>" \
  http://localhost:8000/api/auth/users/2
```

## Done: reading position (this change)

Resuming is passive by design. Bookmarks already existed and are a different
thing - a bookmark is something you deliberately mark and expect to find
again. Resuming has to cost nothing at the moment you stop reading, which is
exactly when nobody presses a button.

- `documents.last_read_page / last_read_position / last_read_at`, migration
  `c1d4a7e93f02`. All nullable so "never opened" stays distinguishable from
  "opened at page 1" - otherwise the library claims you started everything.
- `PUT /api/documents/{id}/progress`, clamped to the document's page count
  (a re-parse can shrink it under the reader) and scoped to the owner.
- `useReadingProgress` saves on a 1.5s debounce, plus a flush on
  `visibilitychange`/`pagehide` - closing the tab mid-page is the most likely
  way a session ends and is exactly what a debounce drops.
- The library row shows `p.12 · 43%` once there is real progress.

Three bugs found while building it, each reproduced before fixing:

1. **Restore never fired.** The store holds the saved page *before* the
   viewer mounts, so the existing "page changed" effect saw no change and the
   reader opened at page 1. Needed an explicit one-shot restore.
2. **Opening a document destroyed its own bookmark.** The reader mounts at
   page 1 and the debounced save wrote that back over the saved position. Now
   gated on `progressRestoredFor`, so nothing is written until the restore has
   happened. Non-PDF formats mark it immediately, having nothing to restore.
3. **The current page crept forward one per open.** The page-visibility
   IntersectionObserver runs with `rootMargin: "300px"` because it also drives
   render/teardown, so several pages qualify at once and the reported page was
   whichever callback fired last - usually the bottom-most. Now decided by
   visible area, ties going to the lower page. Unrendered pages also reserved
   600x850 while real pages here are ~380x570, so any jump landed thousands of
   pixels short by page 12; placeholders now use page 1's real size.

## Not done: highlights and notes

Deliberately left, not blocked - it is a larger build and shipping the
resume work verified beats half-finishing both.

The hard part is already solved elsewhere in this codebase. Citations anchor
into the PDF text layer with `normalizeWithMap` / `findRange`
(`lib/textAlign.ts`), matching normalized text rather than pixel coordinates,
which is why a citation still lands after a zoom, a re-render or a re-parse.
Highlights should reuse exactly that, and must NOT store rects - those are
invalidated by the fit-to-width scale, which now changes whenever a panel
opens.

Sketch:

- `annotations` table: user_id, document_id, page_number, kind
  (`highlight` | `note`), color, `selected_text`, `note` (nullable),
  `text_offset` (character offset into that page's extracted text, for
  disambiguating repeated phrases), created/updated.
- CRUD router at `/api/annotations`, filtered by document.
- Selection toolbar over the text layer (the link layer in `PdfViewer.tsx`
  already demonstrates overlaying interactive elements above it without
  stealing the click-to-read gesture - Alt+click falls through).
- Re-anchor on render by the citation path, and render highlights as spans
  behind the text layer so selection still works.
- A notes list belongs in the existing summary/AI panel rather than a fourth
  panel - the layout is already at its limit on a 15" screen.

Worth deciding before building: whether a note is always attached to a text
range, or whether a page-level note with no selection is also allowed. The
second is easy to add later and awkward to retrofit into a schema that
assumes an anchor.
