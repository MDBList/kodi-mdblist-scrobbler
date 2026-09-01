import datetime

from resources.lib import library_snapshot, mdblist_api, sync_state
from resources.lib.sync_payload import build_shows_payload, chunked
from resources.lib.utils import jsonrpc_request

CATEGORY = "watched"
BATCH_SIZE = 100


def _now_iso():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_api_datetime(value):
    return value.replace(" ", "T") if value else None


def _normalize_ts(value):
    """First 19 chars of either Kodi's 'YYYY-MM-DD HH:MM:SS' or the API's ISO
    8601 timestamp, separator-normalized -- sortable as a plain string without
    needing real datetime parsing/timezone handling."""
    if not value:
        return None
    return value.replace(" ", "T")[:19]


# --- push: Kodi -> MDBList -----------------------------------------------

def _current_watched_items(snapshot):
    items = {}
    for movie in library_snapshot.iter_movies(snapshot):
        if movie["playcount"] > 0:
            key = library_snapshot.canonical_movie_key(movie["ids"])
            if key:
                items[key] = {
                    "type": "movie", "ids": movie["ids"],
                    "watched_at": _to_api_datetime(movie["lastplayed"]),
                }
    for episode in library_snapshot.iter_episodes(snapshot):
        if episode["playcount"] > 0:
            key = library_snapshot.canonical_episode_key(episode["show_ids"], episode["season"], episode["episode"])
            if key:
                items[key] = {
                    "type": "episode", "show_ids": episode["show_ids"],
                    "season": episode["season"], "episode": episode["episode"],
                    "watched_at": _to_api_datetime(episode["lastplayed"]),
                }
    return items


def _push_add(items):
    movies_payload = [{"ids": item["ids"], "watched_at": item["watched_at"]} for item in items if item["type"] == "movie"]
    episode_entries = [
        (item["show_ids"], item["season"], item["episode"], {"watched_at": item["watched_at"]})
        for item in items if item["type"] == "episode"
    ]

    for batch in chunked(movies_payload, BATCH_SIZE):
        mdblist_api.push_sync_items("/sync/watched", {"movies": batch})
    for batch in chunked(episode_entries, BATCH_SIZE):
        mdblist_api.push_sync_items("/sync/watched", {"shows": build_shows_payload(batch)})


def _push_remove(items):
    movies_payload = [{"ids": item["ids"]} for item in items if item["type"] == "movie"]
    episode_entries = [
        (item["show_ids"], item["season"], item["episode"], {})
        for item in items if item["type"] == "episode"
    ]

    for batch in chunked(movies_payload, BATCH_SIZE):
        mdblist_api.push_sync_items("/sync/watched/remove", {"movies": batch})
    for batch in chunked(episode_entries, BATCH_SIZE):
        mdblist_api.push_sync_items("/sync/watched/remove", {"shows": build_shows_payload(batch)})


def push(snapshot):
    """Backfill/membership diff only -- a rewatch that updates lastplayed
    without changing membership is already pushed live via the /scrobble/stop
    event, so this doesn't need ratings_sync's extra "value changed" check."""
    known = sync_state.get_known_items(CATEGORY)
    current = _current_watched_items(snapshot)

    to_add = [item for key, item in current.items() if key not in known]
    to_remove = [item for key, item in known.items() if key not in current]

    if to_add:
        _push_add(to_add)
    if to_remove:
        _push_remove(to_remove)

    sync_state.set_known_items(CATEGORY, current)
    return {"pushed_add": len(to_add), "pushed_remove": len(to_remove)}


# --- pull: MDBList -> Kodi -------------------------------------------------

def _set_watched(record, playcount, lastplayed=None):
    # Only send lastplayed when we have a real value -- e.g. on removal this
    # leaves it untouched, matching Kodi's own "mark unwatched" behavior
    # rather than forcing an empty/invalid date onto the library row.
    params = {"playcount": playcount}
    if lastplayed:
        params["lastplayed"] = lastplayed
    if record["dbtype"] == "movie":
        jsonrpc_request("VideoLibrary.SetMovieDetails", dict(params, movieid=record["dbid"]))
    else:
        jsonrpc_request("VideoLibrary.SetEpisodeDetails", dict(params, episodeid=record["dbid"]))


def _apply_watched(record, status, remote_at):
    """Last-write-wins using Kodi's lastplayed vs the remote timestamp -- the
    one sync category where Kodi actually tracks a comparable local
    timestamp, so real conflict resolution (not just remote-wins) applies."""
    local_ts = _normalize_ts(record.get("lastplayed"))
    remote_ts = _normalize_ts(remote_at)

    if status == "removed":
        if record["playcount"] <= 0:
            return False
        if local_ts and remote_ts and local_ts > remote_ts:
            return False
        _set_watched(record, playcount=0)
        return True

    if record["playcount"] > 0 and local_ts and remote_ts and local_ts >= remote_ts:
        return False

    new_lastplayed = remote_at.replace("T", " ")[:19] if remote_at else record.get("lastplayed")
    _set_watched(record, playcount=max(record["playcount"], 1), lastplayed=new_lastplayed)
    return True


def _apply_movie_entry(snapshot, ids, status, action_at):
    match = library_snapshot.find_movie_match(snapshot, ids)
    return bool(match) and _apply_watched(match, status, action_at)


def _apply_episode_entry(snapshot, show_ids, season, episode, status, action_at):
    match = library_snapshot.find_episode_match(snapshot, show_ids, season, episode)
    return bool(match) and _apply_watched(match, status, action_at)


def _pull_full(snapshot):
    data = mdblist_api.fetch_sync_items("/sync/watched", extended="ids_only")
    applied = 0

    for entry in data.get("movies", []):
        if entry.get("tmdb") and _apply_movie_entry(snapshot, {"tmdb": entry["tmdb"]}, "active", entry.get("last_watched_at")):
            applied += 1

    for entry in data.get("episodes", []):
        if entry.get("show") and _apply_episode_entry(
            snapshot, {"tmdb": entry["show"]}, entry.get("season"), entry.get("episode"),
            "active", entry.get("last_watched_at"),
        ):
            applied += 1

    sync_state.set_synced_at(CATEGORY, _now_iso())
    return {"pulled_applied": applied, "mode": "full"}


def _pull_incremental(snapshot, entries):
    applied = 0
    for entry in entries:
        if entry.get("category") != "watched":
            continue

        ids = entry.get("ids") or {}
        status = entry.get("status")
        action_at = entry.get("action_at")

        if entry.get("item_type") == "movie":
            if _apply_movie_entry(snapshot, ids, status, action_at):
                applied += 1
        elif entry.get("item_type") == "episode":
            if _apply_episode_entry(snapshot, ids, entry.get("season"), entry.get("episode"), status, action_at):
                applied += 1
        # show/season-level rows have no directly writable Kodi field; skipped

    sync_state.set_synced_at(CATEGORY, _now_iso())
    return {"pulled_applied": applied, "mode": "incremental"}


def pull(snapshot):
    since = sync_state.get_synced_at(CATEGORY)
    if not since:
        return _pull_full(snapshot)

    journal = mdblist_api.fetch_journal(since=since)
    if journal.get("requires_full_sync"):
        return _pull_full(snapshot)

    return _pull_incremental(snapshot, journal.get("entries", []))
