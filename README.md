# MDBList for Kodi

This addon connects Kodi to MDBList: live scrobbling while you watch, plus two-way sync for watched status and ratings, one-way library/collection sync, watchlist add/remove/browse, and an optional post-playback rating prompt.

## Features

| Feature | Direction | Notes |
|---------|-----------|-------|
| Live scrobbling | Kodi → MDBList | Real-time progress updates while playing |
| Watched status sync | Two-way | Live push on any watched/unwatched change, remote changes pulled in the background |
| Ratings sync | Two-way | Live push, remote changes pulled in the background |
| Library/collection sync | Kodi → MDBList | Reflects what's actually in your Kodi library, including removals |
| Watchlist | Add / remove / browse | Context menu add/remove, plus a browsable watchlist directory |
| Rating prompt | Kodi → MDBList (+ Kodi library) | Optional dialog after playback |

## Installation

The recommended installation method is through the MDBList Kodi Repository, so Kodi can receive add-on updates automatically.

1. In Kodi, enable **Unknown sources** under **Settings > System > Add-ons**.
2. Open **Settings > File manager > Add source**.
3. Add this source URL:

   ```text
   https://linaspurinis.github.io/repository.mdblist/
   ```

4. Name the source `MDBList`.
5. Open **Add-ons > Install from zip file**.
6. Select the `MDBList` source and install:

   ```text
   repository.mdblist-1.0.0.zip
   ```

7. Open **Add-ons > Install from repository > MDBList Kodi Repository** and install **MDBList**.

You can also download the repository installer directly:

```text
https://linaspurinis.github.io/repository.mdblist/repository.mdblist-1.0.0.zip
```

After that, connect your MDBList account (or configure an API key) in the addon settings.

## Scrobbling

The addon sends HTTP POST requests containing a JSON payload.

A request is sent once the playback starts, pauses, resumes or stops.

Additionally to that, it's also possible to regularly send the current progress while playing movies or episodes (i.e. not paused). This feature can be configured on the "Interval" page in the addon settings.

Events are mapped to MDBList endpoints as follows:

| Event    | MDBList endpoint  |
|----------|-------------------|
| start    | /scrobble/start   |
| pause    | /scrobble/pause   |
| resume   | /scrobble/start   |
| stop     | /scrobble/stop    |
| end      | /scrobble/stop    |
| seek     | /scrobble/start   |
| interval | /scrobble/start   |

Each event can be enabled or disabled individually in the addon settings.

### Playback progress reporting

The `progress` property is especially useful in combination with the `interval` event as it contains the current playback progress. But the progress is also included in other events like `pause`, `seek` or `stop`.

Progress is sent as a percentage (0-100) in the `progress` field.

### Payload structure

The following examples provide the usual structure which will be used for sending the data to MDBList.

**Movies**

```json
{
  "movie": {
    "ids": {
    "imdb": "tt0088763"
    }
  },
  "progress": 0.0
}
```

**Episodes**

```json
{
  "show": {
    "ids": {
      "tvdb": "75897"
    },
    "season": {
      "number": 20,
      "episode": {
        "number": 1
      }
    }
  },
  "progress": 0.0
}
```

## Sync (watched status, ratings, collection)

All three are configured under **Settings > Sync**, and are off by default:

| Setting | Description |
|---------|-------------|
| Sync watched status (two-way) | `sync.watched.enabled` |
| Sync ratings (two-way) | `sync.ratings.enabled` -- also gates the rating prompt's "Save to MDBList" |
| Sync library/collection status (Kodi to MDBList only) | `sync.collection.enabled` |
| Sync after library scan finishes | `sync.on_library_scan`, default on |
| Sync now | Runs a full sync immediately |
| Last sync | Read-only status of the most recent run |

**Not everything is instant.** Watched-status changes made in Kodi push to MDBList immediately. Rating changes made through Kodi's native UI can take up to 2 minutes to reach MDBList (see why below). Changes made on MDBList, or on another device, can take up to 10 minutes to reach Kodi -- and in rare cases up to a day, if the faster check misses something. If you need it sooner, use **Sync now**.

### How changes reach MDBList (push)

- **Watched status**: any local watched/unwatched change (native Kodi "mark as watched", another addon, or this addon's own scrobbling) fires immediately via Kodi's `VideoLibrary.OnUpdate` notification.
- **Ratings**: Kodi's native rating UI does not reliably announce a change the way marking watched does, so ratings are instead caught by a lightweight local poll every 2 minutes (a cheap query -- just item ids and rating values, nothing else).
- **Library/collection**: pushed and reconciled (additions and removals) whenever the Kodi library scan finishes, on the periodic full sync, or on manual "Sync now".

### How changes reach Kodi (pull)

- A lightweight check against MDBList's activity feed runs every 10 minutes; if watched status or ratings actually changed remotely, the affected item(s) are pulled and applied to the Kodi library.
- A full reconciliation additionally runs once a day, on library scan, and on manual "Sync now" -- this also catches anything the incremental checks might have missed (e.g. the addon was offline when a change happened).
- Collection/library sync has no pull direction: MDBList reflects what's in your Kodi library, not the other way around, since Kodi can't materialize a file it doesn't have.

### Conflict resolution

- **Watched status**: last-write-wins, comparing Kodi's local watch timestamp against MDBList's remote timestamp (both normalized to UTC).
- **Ratings**: Kodi has no "rated at" timestamp to compare against, so local changes are pushed immediately and any remote change picked up by the periodic checks is treated as authoritative for that item from then on.

## Rating prompt

After finishing or stopping playback, the addon can prompt you to rate the movie or episode on a scale of 1–10.

### Configuration

| Setting | Description |
|---------|-------------|
| Enable rating prompt | Master toggle for the rating feature |
| Prompt on playback end | Show prompt when playback finishes naturally |
| Prompt on playback stop | Show prompt when playback is manually stopped |
| Prompt for movies | Enable rating prompt for movies |
| Prompt for episodes | Enable rating prompt for episodes |
| Only prompt if unrated | Skip prompt if the item already has a user rating in Kodi |
| Minimum progress (%) | Only prompt if playback reached this percentage (e.g. 80 to skip if you barely watched) |
| Save rating to Kodi | Write the rating back to the Kodi library as a user rating |

Saving the rating to MDBList itself is governed by **Settings > Sync > Sync ratings**, not a separate toggle here -- if that's off, the prompt still shows and can save to the Kodi library, it just won't push to MDBList.

### How it works

When playback ends or stops, a selection dialog appears with the title of the movie or episode and choices from 1 to 10 (plus Skip). Selecting a number saves the rating to the configured destinations and shows a confirmation notification. The prompt is shown at most once per playback session.

## Watchlist

Enabled via **Settings > General > Enable MDBList watchlist**.

- **Add / remove**: a context menu item ("Add to MDBList watchlist") appears on movies, TV shows, and episodes in the Kodi library.
- **Browse**: the addon also provides a video source (find it under Add-ons > Video add-ons) listing your MDBList watchlist as Movies/Shows folders. Items already in your Kodi library play directly; others are marked "Not in library". A remove action is available from the browse view too.

## Supported IDs

The addon resolves media identifiers using the following ID types:

| Media type | Supported IDs |
|------------|---------------|
| Movies | imdb, tmdb, trakt, kitsu, mdblist |
| Shows/Episodes | imdb, tmdb, trakt, tvdb, mdblist |

Common Kodi aliases (e.g. `imdbnumber`, `themoviedb`, `tvdb_id`) are automatically mapped to their canonical forms.
