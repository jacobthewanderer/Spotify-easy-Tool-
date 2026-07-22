# Spotify Album Playlist Builder V3

This GitHub Pages app reads every saved Spotify album, caches all album tracks, then creates:

- `Album Playlist 1`
- `Album Playlist 2`
- `Album Playlist 3`
- and so on

Each playlist contains at most 2,500 songs. Albums remain together whenever possible.

## V3 reliability changes

- Two-stage cache/build workflow.
- Serialized Spotify requests.
- Exact method, endpoint, request number, and response status logging.
- 30-second request timeout.
- Eight automatic retries for `Failed to fetch`, `Load failed`, and related browser network errors.
- Exponential backoff with jitter.
- Automatic retry for Spotify 500, 502, 503, and 504.
- Spotify 429 `Retry-After` support.
- Recovery of an existing automatic playlist after an uncertain create response.
- Exact-range verification after every playlist write.
- Counters advance only after Spotify returns the expected URIs.
- Downloadable diagnostics.
- Separate V3 browser storage.

## Required Spotify scopes

```text
user-library-read
playlist-modify-private
playlist-read-private
```

## Install

1. Replace the old GitHub repository files with all files from this ZIP.
2. Commit the changes.
3. Wait for GitHub Pages to redeploy.
4. Open the deployed page.
5. Add the exact Redirect URI shown on the page to Spotify Developer Dashboard.
6. Paste your Spotify Client ID.
7. Log in.
8. Run the self-test.
9. Press **Start / Resume**.

No Client Secret should be placed in GitHub.
