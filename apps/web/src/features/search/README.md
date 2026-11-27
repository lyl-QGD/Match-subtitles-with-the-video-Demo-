# M5 bilingual search

The home page submits keyword, season, episode, and page state to FastAPI at `/api/v1/search`. FastAPI uses parameterized PostgreSQL queries and returns bilingual segments with adjacent context. Search state is reflected in the URL, and matched text is rendered as React `<mark>` nodes rather than injected HTML.

The page also loads nine random approved clips from `/api/v1/featured`. Search results and featured cards open a modal player; `/api/v1/segments/{id}/playback` rechecks publication/rights and creates a short-lived R2 URL. Playback starts at the segment timestamp, pauses at its end, and the modal's previous/next controls move through the current card group.

The API excludes content unless the video and active subtitle revision are published, the segment is approved, the selected playback asset is ready, and the rights record grants currently valid preview permission. A database connection failure returns HTTP 503 with a stable error code; it is never presented as an empty result set.

The current M4 video has user-confirmed preview/search authorization and is searchable. Sharing and downloading remain disabled. The app is still running locally and has not been deployed publicly.
