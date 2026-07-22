# Changelog

## 3.0.0

- Added two-stage album-track caching and playlist building.
- Added automatic retries for browser `Failed to fetch` and `Load failed` errors.
- Added exact API request and response logging.
- Added serialized request queue and request timeout.
- Added exponential backoff.
- Added Spotify 429 and 5xx handling.
- Added playlist creation recovery.
- Added exact playlist-range verification after every write.
- Added diagnostic JSON export.
- Added independent V3 progress storage.
