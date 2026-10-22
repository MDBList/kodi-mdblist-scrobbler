"""Collection media info: Kodi streamdetails -> MDBList's Trakt-style fields
(resources/lib/media_info.py), and collection_sync pushing them and re-pushing
an item when its file's media info changes.

Run from the repo root (kodistubs provides xbmc):
  uv run python -m unittest tests.test_collection_media
"""
import unittest
from unittest import mock

from resources.lib import collection_sync, library_snapshot, media_info, sync_payload


class MediaInfoTests(unittest.TestCase):
    def test_resolution(self):
        cases = [
            ((3840, 2160), "uhd_4k"), ((3840, 1600), "uhd_4k"), ((1920, 800), "hd_1080p"),
            ((1280, 536), "hd_720p"), ((720, 576), "sd_576p"), ((720, 480), "sd_480p"), ((0, 0), None),
        ]
        for (width, height), expected in cases:
            self.assertEqual(media_info.resolution(width, height), expected, (width, height))

    def test_hdr(self):
        cases = [("dolbyvision", "dolby_vision"), ("hdr10", "hdr10"), ("hdr10plus", "hdr10_plus"),
                 ("hlg", "hlg"), ("", None), (None, None)]
        for value, expected in cases:
            self.assertEqual(media_info.hdr(value), expected, value)

    def test_audio(self):
        cases = [
            ("truehd", "dolby_truehd"), ("truehd_atmos", "dolby_atmos"), ("eac3", "dolby_digital_plus"),
            ("eac3_ddp_atmos", "dolby_digital_plus_atmos"), ("ac3", "dolby_digital"), ("dca", "dts"),
            ("dtshd_ma", "dts_ma"), ("dtshd_hra", "dts_hr"), ("dtshd_ma_x", "dts_x"), ("dtshd_ma_x_imax", "dts_x"),
            ("aac", "aac"), ("opus", "ogg_opus"), ("pcm_s16le", "lpcm"), ("weird", None), (None, None),
        ]
        for codec, expected in cases:
            self.assertEqual(media_info.audio(codec), expected, codec)

    def test_channels(self):
        self.assertEqual(media_info.channels(6), "5.1")
        self.assertEqual(media_info.channels(8), "7.1")
        self.assertIsNone(media_info.channels(12))
        self.assertIsNone(media_info.channels(None))

    def test_from_streamdetails(self):
        details = {
            "video": [{"codec": "hevc", "width": 3840, "height": 2160, "hdrtype": "dolbyvision", "stereomode": ""}],
            "audio": [{"codec": "truehd_atmos", "channels": 8, "language": "eng"}, {"codec": "ac3", "channels": 6}],
            "subtitle": [],
        }
        self.assertEqual(media_info.from_streamdetails(details), {
            "resolution": "uhd_4k", "hdr": "dolby_vision", "audio": "dolby_atmos", "audio_channels": "7.1",
        })

    def test_mono_is_not_3d(self):
        self.assertEqual(media_info.from_streamdetails({"video": [{"width": 1920, "height": 1080, "stereomode": "mono"}]}),
                         {"resolution": "hd_1080p"})

    def test_3d_and_unknown(self):
        self.assertEqual(media_info.from_streamdetails({"video": [{"width": 1920, "height": 1080, "stereomode": "left_right"}]}),
                         {"resolution": "hd_1080p", "3d": True})
        self.assertEqual(media_info.from_streamdetails({}), {})
        self.assertEqual(media_info.from_streamdetails(None), {})


def copy(key, **fields):
    return dict(fields, copy_key=key)


class CollectionSyncTests(unittest.TestCase):
    def test_media_changed(self):
        self.assertTrue(collection_sync._media_changed({"copies": [copy("movie-1", resolution="hd_1080p")]},
                                                       {"copies": [copy("movie-1", resolution="uhd_4k")]}))
        self.assertFalse(collection_sync._media_changed(
            {"copies": [copy("movie-1", resolution="uhd_4k"), copy("movie-2", resolution="hd_1080p")]},
            {"copies": [copy("movie-2", resolution="hd_1080p"), copy("movie-1", resolution="uhd_4k")]}))

    def test_state_from_before_media_info_pushes_items_with_media_once(self):
        self.assertTrue(collection_sync._media_changed({}, {"copies": [copy("movie-1", resolution="hd_720p")]}))
        self.assertFalse(collection_sync._media_changed({}, {"copies": []}))

    def record(self, dbid, resolution, ids=None, dateadded="2026-01-01 10:00:00"):
        return {"dbtype": "movie", "dbid": dbid, "ids": ids or {"tmdb": "157336"}, "file": "/m/%d.mkv" % dbid,
                "dateadded": dateadded, "media": {"resolution": resolution} if resolution else {}}

    def items(self, *records):
        # Indexed like library_snapshot.build_snapshot: the last record wins each id.
        index = {}
        for record in records:
            for provider, value in record["ids"].items():
                index["{}:{}".format(provider, value)] = record
        return collection_sync._current_collected_items(
            {"movie": index, "episode": {}, "movie_records": list(records), "episode_records": []})

    def test_two_entries_of_one_title_become_two_copies(self):
        items = self.items(self.record(1, "uhd_4k"), self.record(2, "hd_1080p"), self.record(2, "hd_1080p"))
        self.assertEqual(len(items), 1)
        copies = next(iter(items.values()))["copies"]
        self.assertEqual(sorted(c["resolution"] for c in copies), ["hd_1080p", "uhd_4k"])
        self.assertEqual(len({c["copy_key"] for c in copies}), 2)

    def test_copy_key_is_stable_across_library_rebuilds(self):
        before = self.items(self.record(1, "uhd_4k"))["movie:tmdb:157336"]["copies"]
        rebuilt = dict(self.record(907, "uhd_4k"), file="/m/1.mkv")  # same file, new dbid
        self.assertEqual(self.items(rebuilt)["movie:tmdb:157336"]["copies"], before)
        self.assertTrue(before[0]["copy_key"].startswith("f-"))

    def test_item_keys_stay_the_id_indexed_ones(self):
        # Entries with different id sets keep the keys the id index gives them, so
        # nothing already in sync_state looks removed; a hidden entry adds its copy.
        a = self.record(1, "uhd_4k", ids={"imdb": "tt1"})
        b = self.record(2, "hd_1080p", ids={"tmdb": "5", "imdb": "tt1"})
        items = self.items(a, b)
        self.assertEqual(list(items), ["movie:tmdb:5"])
        self.assertEqual(len(items["movie:tmdb:5"]["copies"]), 2)
        c = self.record(3, "hd_720p", ids={"tmdb": "6", "imdb": "tt9"})
        self.assertEqual(sorted(self.items(a, b, c)), ["movie:tmdb:5", "movie:tmdb:6"])

    def test_a_mis_scraped_duplicate_keeps_its_own_copy(self):
        # Two different films sharing a (wrong) imdb id: each file stays on its own title.
        one = self.record(1, "uhd_4k", ids={"imdb": "tt0000001", "tmdb": "1"})
        two = self.record(2, "hd_1080p", ids={"imdb": "tt0000001", "tmdb": "2"})
        items = self.items(one, two)
        self.assertEqual([c["resolution"] for c in items["movie:tmdb:1"]["copies"]], ["uhd_4k"])
        self.assertEqual([c["resolution"] for c in items["movie:tmdb:2"]["copies"]], ["hd_1080p"])

    def test_entries_with_different_id_sets_both_push_the_full_copy_set(self):
        # A (tmdb+imdb) and B (imdb only) stay separate items (keys unchanged) but
        # describe one film: each pushes both files, so neither push drops the other.
        a = self.record(1, "uhd_4k", ids={"tmdb": "1", "imdb": "tt9"})
        b = self.record(2, "hd_1080p", ids={"imdb": "tt9"})
        items = self.items(a, b)
        self.assertEqual(sorted(items), ["movie:imdb:tt9", "movie:tmdb:1"])
        for item in items.values():
            self.assertEqual(sorted(c["resolution"] for c in item["copies"]), ["hd_1080p", "uhd_4k"])

    def test_an_unprobed_entry_adds_no_copy_but_keeps_the_others(self):
        items = self.items(self.record(1, "uhd_4k"), self.record(2, None))
        self.assertEqual([c["resolution"] for c in items["movie:tmdb:157336"]["copies"]], ["uhd_4k"])

    def test_entries_kept_apart_but_sharing_a_key_push_both_copies(self):
        # The imdb ids contradict, so the entries stay in separate groups, but both key to tmdb:1.
        one = self.record(1, "uhd_4k", ids={"tmdb": "1", "imdb": "tt1"})
        two = self.record(2, "hd_1080p", ids={"tmdb": "1", "imdb": "tt2"})
        items = self.items(one, two)
        self.assertEqual(sorted(c["resolution"] for c in items["movie:tmdb:1"]["copies"]), ["hd_1080p", "uhd_4k"])

    def test_a_bridging_entry_does_not_merge_contradicting_titles(self):
        a = self.record(1, "uhd_4k", ids={"imdb": "tt1", "tvdb": "10"})
        b = self.record(2, "hd_1080p", ids={"tmdb": "5", "tvdb": "20"})
        bridge = self.record(3, "hd_720p", ids={"imdb": "tt1", "tmdb": "5"})
        groups = collection_sync._title_groups([a, b, bridge], lambda r: r["ids"])
        self.assertIsNot(groups[id(a)], groups[id(b)])

    def test_title_grouping_scales(self):
        import time
        records = [self.record(i, "hd_1080p", ids={"tmdb": str(i), "imdb": "tt%d" % i}) for i in range(20000)]
        started = time.time()
        self.items(*records)
        self.assertLess(time.time() - started, 5)

    def test_no_media_info_is_not_a_change(self):
        # Not probed yet / replaced: MDBList keeps its copies until the file is read again.
        self.assertFalse(collection_sync._media_changed({"copies": [copy("f-1", resolution="uhd_4k")]}, {"copies": []}))
        self.assertEqual(collection_sync._media_fields({"copies": []}), {})

    def test_media_repushes_are_capped_per_run(self):
        changed = collection_sync._capped(lambda known, item: True, 2)
        self.assertEqual([changed({}, {}) for _ in range(4)], [True, True, False, False])

    def test_push_sends_media_fields_on_movies_and_episodes(self):
        sent = []
        items = [
            {"type": "movie", "ids": {"tmdb": 1}, "collected_at": "2026-01-01T00:00:00Z", "copies": [copy("movie-7", resolution="uhd_4k")]},
            {"type": "movie", "ids": {"tmdb": 2}, "collected_at": "2026-01-01T00:00:00Z", "copies": []},
            {"type": "episode", "show_ids": {"tvdb": 3}, "season": 1, "episode": 2,
             "collected_at": "2026-01-01T00:00:00Z", "copies": [copy("episode-9", hdr="hdr10")]},
        ]
        with mock.patch.object(sync_payload.mdblist_api, "push_sync_items", side_effect=lambda endpoint, payload: sent.append(payload)), \
                mock.patch.object(sync_payload.sync_state, "merge_known_items"):
            collection_sync._push_add(items)

        movies = sent[0]["movies"]
        self.assertEqual(movies[0], {"ids": {"tmdb": 1}, "collected_at": "2026-01-01T00:00:00Z",
                                     "copies": [copy("movie-7", resolution="uhd_4k")]})
        self.assertEqual(movies[1], {"ids": {"tmdb": 2}, "collected_at": "2026-01-01T00:00:00Z"})  # nothing known: no copies
        episode = sent[1]["shows"][0]["seasons"][0]["episodes"][0]
        self.assertEqual(episode, {"number": 2, "collected_at": "2026-01-01T00:00:00Z", "copies": [copy("episode-9", hdr="hdr10")]})

    def test_upgrade_in_place_is_pushed_again(self):
        known = {"movie:tmdb:1": {"type": "movie", "ids": {"tmdb": 1}, "copies": [copy("movie-1", resolution="hd_1080p")]}}
        current = {"movie:tmdb:1": {"type": "movie", "ids": {"tmdb": 1}, "copies": [copy("movie-1", resolution="uhd_4k")]}}
        pushed = []
        with mock.patch.object(sync_payload.sync_state, "get_known_items", return_value=known):
            result = sync_payload.diff_and_reconcile("collection", current, pushed.extend, lambda items: None,
                                                     value_changed=collection_sync._media_changed)
        self.assertEqual(result["pushed_add"], 1)
        self.assertEqual(pushed[0]["copies"], [copy("movie-1", resolution="uhd_4k")])


class SnapshotTests(unittest.TestCase):
    def build(self, include_media, fail_media=False):
        calls = []

        def rpc(method, params):
            calls.append((method, params.get("properties"), params.get("limits")))
            if params.get("properties") == ["streamdetails"]:
                if fail_media:
                    raise RuntimeError("out of memory")
                return {"movies": [{"movieid": 1, "streamdetails": {"video": [{"width": 3840, "height": 2160}]}}],
                        "limits": {"total": 1}}
            if method == "VideoLibrary.GetMovies":
                return {"movies": [{"movieid": 1, "title": "M", "uniqueid": {"tmdb": "603"}, "file": "/m.mkv"}]}
            return {}
        with mock.patch.object(library_snapshot, "jsonrpc_request", side_effect=rpc), \
                mock.patch.object(library_snapshot, "fix_unique_ids", side_effect=lambda ids, kind: dict(ids)):
            snapshot = library_snapshot.build_snapshot(include_media=include_media)
        return snapshot, calls

    def test_streamdetails_only_for_the_collection_push(self):
        _, calls = self.build(False)
        self.assertFalse(any(props == ["streamdetails"] for _, props, _ in calls))
        snapshot, calls = self.build(True)
        self.assertTrue(any(props == ["streamdetails"] and limits for _, props, limits in calls))  # paged
        self.assertEqual(snapshot["movie_records"][0]["media"], {"resolution": "uhd_4k"})

    def test_a_failed_media_query_leaves_media_unknown_not_the_snapshot_broken(self):
        snapshot, _ = self.build(True, fail_media=True)
        self.assertEqual(len(snapshot["movie_records"]), 1)
        self.assertEqual(snapshot["movie_records"][0]["media"], {})



class MediaInfoSettingTests(unittest.TestCase):
    def include_media(self, settings):
        from resources.lib import sync_orchestrator
        with mock.patch.object(sync_orchestrator, "_bool_setting", side_effect=lambda k, d=False: settings.get(k, d)), \
                mock.patch.object(library_snapshot, "build_snapshot", side_effect=RuntimeError("stop")) as build:
            try:
                sync_orchestrator.run()
            except RuntimeError:
                pass
        return build.call_args.kwargs["include_media"]

    def test_media_info_is_opt_in(self):
        # Off by default: reading every file's streamdetails slows the sync on a big library.
        self.assertFalse(self.include_media({"sync.collection.enabled": True}))
        self.assertTrue(self.include_media({"sync.collection.enabled": True, "sync.collection.media": True}))
        self.assertFalse(self.include_media({"sync.watched.enabled": True, "sync.collection.media": True}))


if __name__ == "__main__":
    unittest.main()
