from resources.lib import library_snapshot, mdblist_api, sync_state
from resources.lib.sync_payload import build_shows_payload, chunked

CATEGORY = "collection"
BATCH_SIZE = 100

# Kodi -> MDBList only: this reflects what's actually in the local library so
# MDBList's collected status is accurate. There is deliberately no pull
# direction -- Kodi can't materialize a file just because MDBList thinks it's
# collected, so a remote-only "collected" flag has nothing local to apply.


def _to_api_datetime(value):
    return value.replace(" ", "T") if value else None


def _current_collected_items(snapshot):
    items = {}
    for movie in library_snapshot.iter_movies(snapshot):
        if movie["file"]:
            key = library_snapshot.canonical_movie_key(movie["ids"])
            if key:
                items[key] = {
                    "type": "movie", "ids": movie["ids"],
                    "collected_at": _to_api_datetime(movie["dateadded"]),
                }
    for episode in library_snapshot.iter_episodes(snapshot):
        if episode["file"]:
            key = library_snapshot.canonical_episode_key(episode["show_ids"], episode["season"], episode["episode"])
            if key:
                items[key] = {
                    "type": "episode", "show_ids": episode["show_ids"],
                    "season": episode["season"], "episode": episode["episode"],
                    "collected_at": _to_api_datetime(episode["dateadded"]),
                }
    return items


def _push_add(items):
    movies_payload = [{"ids": item["ids"], "collected_at": item["collected_at"]} for item in items if item["type"] == "movie"]
    episode_entries = [
        (item["show_ids"], item["season"], item["episode"], {"collected_at": item["collected_at"]})
        for item in items if item["type"] == "episode"
    ]

    for batch in chunked(movies_payload, BATCH_SIZE):
        mdblist_api.push_sync_items("/sync/collection", {"movies": batch})
    for batch in chunked(episode_entries, BATCH_SIZE):
        mdblist_api.push_sync_items("/sync/collection", {"shows": build_shows_payload(batch)})


def _push_remove(items):
    movies_payload = [{"ids": item["ids"]} for item in items if item["type"] == "movie"]
    episode_entries = [
        (item["show_ids"], item["season"], item["episode"], {})
        for item in items if item["type"] == "episode"
    ]

    for batch in chunked(movies_payload, BATCH_SIZE):
        mdblist_api.push_sync_items("/sync/collection/remove", {"movies": batch})
    for batch in chunked(episode_entries, BATCH_SIZE):
        mdblist_api.push_sync_items("/sync/collection/remove", {"shows": build_shows_payload(batch)})


def push(snapshot):
    """Push + reconcile: anything newly present in the Kodi library is added,
    anything that dropped out (file removed/library item deleted) since the
    last run is removed from MDBList's collection -- the "clean collection"
    step, mirroring script.trakt's collection sync."""
    known = sync_state.get_known_items(CATEGORY)
    current = _current_collected_items(snapshot)

    to_add = [item for key, item in current.items() if key not in known]
    to_remove = [item for key, item in known.items() if key not in current]

    if to_add:
        _push_add(to_add)
    if to_remove:
        _push_remove(to_remove)

    sync_state.set_known_items(CATEGORY, current)
    return {"pushed_add": len(to_add), "pushed_remove": len(to_remove)}
