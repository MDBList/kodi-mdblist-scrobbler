from collections import OrderedDict


def build_shows_payload(episode_entries):
    """Group flat (show_ids, season, episode, extra_fields) tuples into the
    nested {"ids", "seasons": [{"number", "episodes": [{"number", **extra}]}]}
    shape every /sync/* endpoint (watched, ratings, collection) expects for
    episodes -- confirmed against api.mdblist's resolve_show_payload, which
    every one of those POST handlers routes through."""
    shows_by_key = OrderedDict()

    for show_ids, season, episode, extra in episode_entries:
        key = tuple(sorted(show_ids.items()))
        show = shows_by_key.setdefault(key, {"ids": show_ids, "seasons": OrderedDict()})
        entries = show["seasons"].setdefault(season, [])
        entry = {"number": episode}
        entry.update(extra)
        entries.append(entry)

    return [
        {
            "ids": show["ids"],
            "seasons": [
                {"number": season_number, "episodes": episodes}
                for season_number, episodes in show["seasons"].items()
            ],
        }
        for show in shows_by_key.values()
    ]


def chunked(items, size=100):
    for i in range(0, len(items), size):
        yield items[i:i + size]
