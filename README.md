# Spotify Album Playlist Builder v2

A standalone GitHub Pages app that reads the albums saved in your Spotify library and creates private playlists named:

```text
Album Playlist 1
Album Playlist 2
Album Playlist 3
...
```

Each playlist contains at most 2,500 songs. Albums are kept together whenever possible.

## What v2 fixes

- Replaces the unhelpful `ERROR: Load failed` message with the exact HTTP method, URL, status, response, browser network state, and retry information.
- Retries browser/network failures automatically with increasing delays.
- Retries Spotify 500/502/503/504 responses.
- Saves and respects Spotify 429 retry timing.
- Includes a self-test before a full run.
- Includes a downloadable diagnostics report.
- Detects and reuses existing playlists named `Album Playlist N`.
- Saves progress after every page, playlist creation, album track collection, and 100-song playlist batch.
- Uses PKCE login, so a Client Secret is never exposed in GitHub Pages.

## Current Spotify endpoints

```text
GET  /me
GET  /me/albums?limit=50
GET  /albums/{album_id}/tracks?limit=50
GET  /me/playlists?limit=50
POST /me/playlists
POST /playlists/{playlist_id}/items
```

## Required scopes

```text
user-library-read
playlist-modify-private
playlist-read-private
```

## Install on GitHub Pages

1. Create a GitHub repository.
2. Upload every file from this ZIP into the repository root.
3. Open **Settings → Pages**.
4. Select **Deploy from a branch**.
5. Choose `main` and `/ (root)`.
6. Open the published GitHub Pages URL.
7. Copy the Redirect URI displayed by the app.
8. Add that exact URI to your Spotify app in the Spotify Developer Dashboard.
9. Paste the Spotify Client ID into the app and save it.
10. Log in, run **Self-test**, then press **Start / Resume**.

## Updating from v1

Upload all v2 files over the old files. V2 uses new browser-storage keys so it starts cleanly and will not inherit a corrupted v1 checkpoint.

## Important existing-playlist behavior

V2 reuses playlists with exact names such as `Album Playlist 1`. It reads their existing item count before continuing. Do not manually reorder or remove items while the app is running.

## Diagnosing failures

Press **Export diagnostics**. The downloaded JSON contains the last API response, current checkpoint, browser information, and the activity log. It does not include your Client ID value or access token.
