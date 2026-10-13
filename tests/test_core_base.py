"""Unit tests: settings, names, GStreamer pipeline, segments, small parsers. ~0.1 s."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import recorder_core as c


class Settings(unittest.TestCase):
    def test_junk_in_gives_valid_out(self):
        s = c.sanitize_settings({"format": "avi", "video_kbps": "abc", "audio_kbps": 99999, "fps": 61,
                                 "resolution": "8k", "buffer_seconds": -5, "mic_gain_db": float("nan"),
                                 "encoder": "rm -rf", "replay_enabled": "yes", "output_folder": 7})
        self.assertEqual(s["format"], "mkv")
        self.assertEqual(s["video_kbps"], c.DEFAULTS["video_kbps"])
        self.assertEqual(s["audio_kbps"], 320)
        self.assertEqual(s["fps"], 0)
        self.assertEqual(s["resolution"], "native")
        self.assertEqual(s["buffer_seconds"], c.MIN_BUFFER)
        self.assertEqual(s["mic_gain_db"], c.DEFAULTS["mic_gain_db"])
        self.assertEqual(s["encoder"], "auto")
        self.assertIs(s["replay_enabled"], False)
        self.assertEqual(s["output_folder"], "")

    def test_buffer_length_minimum_is_15_seconds(self):
        self.assertEqual(c.MIN_BUFFER, 15)
        self.assertEqual(c.sanitize_settings({"buffer_seconds": 15})["buffer_seconds"], 15)
        self.assertEqual(c.sanitize_settings({"buffer_seconds": 14})["buffer_seconds"], 15)
        self.assertEqual(c.sanitize_settings({"buffer_seconds": 601})["buffer_seconds"], 600)

    def test_non_dict(self):
        self.assertEqual(c.sanitize_settings(None), c.DEFAULTS)
        self.assertEqual(c.sanitize_settings([1]), c.DEFAULTS)


class Names(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(c.safe_name('Baldur\'s Gate 3: "Enhanced" / $(touch x); `id`'), "Baldur's Gate 3 Enhanced (touch x) id")
        self.assertEqual(c.safe_name("../../etc/passwd"), "etc passwd")
        self.assertEqual(c.safe_name(None), "Recording")
        self.assertEqual(c.safe_name("..."), "Recording")
        self.assertLessEqual(len(c.safe_name("x" * 500)), 60)

    def test_unique_path(self, ):
        import tempfile
        d = tempfile.mkdtemp()
        p1 = c.unique_path(d, "a", "mkv"); open(p1, "w").close()
        p2 = c.unique_path(d, "a", "mkv"); open(p2, "w").close()
        self.assertEqual([os.path.basename(p1), os.path.basename(p2), os.path.basename(c.unique_path(d, "a", "mkv"))],
                         ["a.mkv", "a_2.mkv", "a_3.mkv"])

    def test_staging_path_ok(self):
        self.assertTrue(c.staging_path_ok("/home/deck/.cache/x/rec_1.mkv"))
        self.assertFalse(c.staging_path_ok("/home/de ck/x.mkv"))
        self.assertFalse(c.staging_path_ok('/x/"y".mkv'))


class Encoders(unittest.TestCase):
    def test_pick(self):
        self.assertEqual(c.pick_encoder(["x264enc", "vaapih264enc"]), "vaapih264enc")
        self.assertEqual(c.pick_encoder(["x264enc"]), "x264enc")
        self.assertIsNone(c.pick_encoder([]))
        self.assertEqual(c.pick_encoder(["x264enc", "vaapih264enc"], "x264enc"), "x264enc")
        self.assertEqual(c.pick_encoder(["vaapih264enc"], "x264enc"), "vaapih264enc")

    def test_parse_props(self):
        out = ("Factory Details:\n  Rank  primary\n\nElement Properties:\n"
               "  bitrate             : Bitrate\n                        flags: readable, writable\n"
               "  rate-control        : Rate control\n\nElement Signals:\n  \"x\" : void\n")
        self.assertEqual(c.parse_gst_props(out), {"bitrate", "rate-control"})


class Pipeline(unittest.TestCase):
    S = c.sanitize_settings({})
    PROPS = {"bitrate", "rate-control", "keyframe-period"}

    def build(self, **kw):
        a = dict(encoder="vaapih264enc", props=self.PROPS, split_props={"send-keyframe-requests"}, aac="avenc_aac",
                 settings=self.S, audio_device="Finally-Rec-Sink.monitor", rolling=False, out_path="/tmp/x/rec.mkv")
        a.update(kw)
        return c.build_gst_argv(**a)

    def test_manual_matches_original_shape(self):
        argv, _ = self.build()
        j = " ".join(argv)
        self.assertTrue(j.startswith("-e pipewiresrc do-timestamp=true ! vaapipostproc ! queue ! vaapih264enc"))
        self.assertIn("! h264parse ! matroskamux name=mux ! filesink location=/tmp/x/rec.mkv", j)
        self.assertIn("pulsesrc device=Finally-Rec-Sink.monitor", j)
        self.assertTrue(j.endswith("! mux.audio_0"))

    def test_rolling_variants(self):
        a, _ = self.build(rolling=True, segment_pattern="/dev/shm/s/seg_%05d.mkv", split_variant="original")
        self.assertIn("muxer=matroskamux", a); self.assertIn("muxer-pad-map=x-pad-map,audio=vid", a)
        b, _ = self.build(rolling=True, segment_pattern="/dev/shm/s/seg_%05d.mkv", split_variant="factory")
        self.assertIn("muxer-factory=matroskamux", b); self.assertNotIn("muxer-pad-map=x-pad-map,audio=vid", b)
        self.assertIn("max-size-time=1000000000", a)

    def test_no_shell_metacharacters_through(self):
        with self.assertRaises(ValueError):
            self.build(out_path='/tmp/a b"; rm -rf ~; ".mkv')

    def test_no_aac_warns_and_drops_audio(self):
        argv, w = self.build(aac=None)
        self.assertNotIn("pulsesrc", " ".join(argv)); self.assertTrue(any("AAC" in x for x in w))

    def test_scaling_and_fps(self):
        s = c.sanitize_settings({"resolution": "1280x720", "fps": 30})
        j = " ".join(self.build(settings=s)[0])
        self.assertIn("videorate drop-only=true max-rate=30", j); self.assertIn("width=1280 height=720", j)

    def test_x264_software(self):
        j = " ".join(self.build(encoder="x264enc")[0])
        self.assertIn("x264enc tune=zerolatency speed-preset=ultrafast", j)

    def test_bitrate_only_if_property_exists(self):
        j = " ".join(self.build(props=set())[0])
        self.assertNotIn("bitrate=12000", j)


class Segments(unittest.TestCase):
    def test_window_and_newest_excluded(self):
        now = 1000.0
        files = [(f"s{i}", now - 100 + i * 1.5) for i in range(60)]     # 1.5s segments, last = now-11.5
        got = c.select_segments(files, 30, now)
        self.assertNotIn("s59", got)                                    # in-progress one excluded
        self.assertEqual(got, sorted(got, key=lambda p: int(p[1:])))    # oldest -> newest
        self.assertTrue(all(now - dict(files)[p] <= 30 for p in got))
        self.assertIn("s58", got)

    def test_single_file_is_kept(self):
        self.assertEqual(c.select_segments([("only", 99.0)], 30, 100.0), ["only"])

    def test_empty(self):
        self.assertEqual(c.select_segments([], 30, 100.0), [])


class Misc(unittest.TestCase):
    def test_concat_list_quotes(self):
        self.assertEqual(c.concat_list(["/a b/it's.mkv"]), "file '/a b/it'\\''s.mkv'\n")

    def test_pactl_modules(self):
        txt = "12\tmodule-null-sink\tsink_name=Finally-Rec-Sink\n13\tmodule-loopback\tsource=x sink=Finally-Rec-Sink\n14\tmodule-other\tfoo\n"
        self.assertEqual(c.parse_pactl_modules(txt, "Finally-Rec"), [12, 13])

    def test_sources_filter(self):
        txt = "1\tmic\tPW\n2\tsink.monitor\tPW\n3\tFinally-Rec-Mic\tmodule\n"
        self.assertEqual(c.parse_source_names(txt), ["mic"])

    def test_wake(self):
        self.assertTrue(c.woke_from_sleep(300, 2)); self.assertFalse(c.woke_from_sleep(2.3, 2))

    def test_buffer_estimate(self):
        self.assertEqual(c.estimate_buffer_mb(12000, 192, 120), 179)


if __name__ == "__main__":
    unittest.main()
