# Changelog

## 2.0.0

- Added detailed request and HTTP error reporting.
- Added automatic retries for network failures and Spotify server errors.
- Added persistent Spotify 429 waiting and retry.
- Added self-test for profile and saved-album access.
- Added diagnostics JSON export.
- Added global browser error logging.
- Added online/offline detection.
- Added detection and reuse of existing `Album Playlist N` playlists.
- Added request, retry, and phase counters.
- Added v2-specific storage keys to avoid damaged v1 progress.
- Retained automatic names, 2,500-song maximum, album-preserving rollover, PKCE login, and procedural resume.

## 1.0.0

- Initial GitHub Pages release.
