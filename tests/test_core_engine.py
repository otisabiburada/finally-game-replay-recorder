"""Unit tests: ffmpeg engine, freeze/speed monitors, hardware buttons, CPU helpers. ~0.1 s."""
import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import recorder_core as c


class FfmpegEngine(unittest.TestCase):
    ENC = (" V....D libx264              libx264 H.264 / AVC\n V....D h264_vaapi           H.264/AVC (VAAPI) (codec h264)\n"
           " A....D aac                  AAC (Advanced Audio Coding)\n S..... ass                  ASS subtitle\n"
           " ------\n V..... libx264rgb x\n")
    DEV = (" D  lavfi           Libavfilter virtual input device\n  E alsa_out         ALSA out\n DE pulse           Pulse audio output\n")

    def test_parsers(self):
        e = c.parse_ffmpeg_encoders(self.ENC)
        self.assertTrue({"libx264", "h264_vaapi", "aac", "libx264rgb"} <= e); self.assertNotIn("------", e)
        d = c.parse_ffmpeg_devices(self.DEV)
        self.assertEqual(d, {"lavfi", "pulse"})                      # alsa_out is output-only

    def test_pick_engine_table(self):
        pe = c.pick_engine
        self.assertEqual(pe(["vaapih264enc"], "avenc_aac", ["libx264"], True, "auto")[0], "gst")
        self.assertEqual(pe([], None, ["libx264"], True, "auto")[0], "ffmpeg")                 # the Deck in the photo
        self.assertEqual(pe(["x264enc"], None, ["libx264"], True, "auto")[0], "ffmpeg")        # gst encoder but no AAC
        self.assertIsNone(pe([], None, [], True, "auto")[0])
        self.assertEqual(pe(["x264enc"], "avenc_aac", ["libx264"], True, "ffmpeg")[0], "ffmpeg")
        self.assertIsNone(pe([], None, ["libx264"], True, "gstreamer")[0])
        self.assertIsNone(pe([], None, ["libx264"], True, "auto", gst_has_capture=False)[0])

    def test_pick_ffmpeg_encoder(self):
        self.assertEqual(c.pick_ffmpeg_encoder(["libx264", "h264_vaapi"]), "h264_vaapi")
        self.assertEqual(c.pick_ffmpeg_encoder(["libx264"]), "libx264")
        self.assertEqual(c.pick_ffmpeg_encoder(["libx264", "h264_vaapi"], "libx264"), "libx264")

    def test_caps_size_and_target(self):
        txt = "caps = video/x-raw, format=(string)BGRx, width=(int)1280, height=(int)800, framerate=(fraction)0/1"
        self.assertEqual(c.parse_caps_size(txt), (1280, 800)); self.assertIsNone(c.parse_caps_size("nothing"))
        s = c.sanitize_settings({})
        self.assertEqual(c.target_size(s, (1920, 1080)), (1920, 1080))
        self.assertEqual(c.target_size(s, (1279, 801)), (1278, 800))                           # NV12 needs even sizes
        self.assertEqual(c.target_size(s, None), c.DEFAULT_SIZE)
        self.assertEqual(c.target_size(c.sanitize_settings({"resolution": "1280x720"}), (1920, 1080)), (1280, 720))

    def test_feeder_argv(self):
        j = " ".join(c.build_feeder_argv((1280, 800), 60, {"keepalive-time"}))
        self.assertIn("pipewiresrc do-timestamp=true keepalive-time=1000 ! queue", j)
        self.assertTrue(j.endswith("video/x-raw,format=NV12,width=1280,height=800,framerate=60/1 ! fdsink fd=1"))
        self.assertTrue(j.startswith("-e -q "))

    def ff(self, enc, **kw):
        a = dict(encoder=enc, settings=c.sanitize_settings({}), size=(1280, 800), fps=60,
                 audio_input=["-f", "pulse", "-i", "Finally-Rec-Sink.monitor"], rolling=False, out_path="/tmp/x/r.mkv")
        a.update(kw)
        return c.build_ffmpeg_argv(**a)

    def test_ffmpeg_argv_vaapi_and_x264(self):
        v = " ".join(self.ff("h264_vaapi"))
        self.assertIn("-vaapi_device /dev/dri/renderD128", v); self.assertIn("-vf format=nv12,hwupload -c:v h264_vaapi", v)
        self.assertIn("-f rawvideo -pix_fmt nv12 -video_size 1280x800 -framerate 60 -i pipe:0", v)
        self.assertIn("-map 0:v:0 -map 1:a:0", v); self.assertIn("-c:a aac -b:a 192k", v); self.assertIn("-g 60", v)
        self.assertTrue(v.endswith("-f matroska /tmp/x/r.mkv"))
        x = " ".join(self.ff("libx264"))
        self.assertNotIn("vaapi", x); self.assertIn("-preset ultrafast", x)

    def test_ffmpeg_argv_replay_segments_and_no_audio(self):
        r = " ".join(self.ff("libx264", rolling=True, segment_pattern="/dev/shm/s/seg_%05d.mkv", audio_input=None, buffer_seconds=120))
        self.assertIn("-f segment -segment_time 1 -segment_format matroska -segment_wrap 124 -reset_timestamps 1 /dev/shm/s/seg_%05d.mkv", r)
        self.assertNotIn("-map 1:a:0", r); self.assertNotIn("-c:a", r)

    def test_ffmpeg_argv_refuses_unsafe_paths(self):
        with self.assertRaises(ValueError): self.ff("libx264", out_path="/tmp/a b.mkv")
        with self.assertRaises(ValueError): self.ff("nvenc9000")

    def test_progress_frame_parser_and_flag(self):
        txt = "frame=10\nfps=30\nprogress=continue\nframe=40\nfps=30.1\nprogress=continue\nframe=7"   # last line may be cut
        self.assertEqual(c.parse_progress_frame(txt), 7); self.assertIsNone(c.parse_progress_frame("fps=1\n"))
        v = " ".join(self.ff("libx264", progress_path="/tmp/x/prog_1.txt"))
        self.assertIn("-progress /tmp/x/prog_1.txt -stats_period 0.5", v)
        with self.assertRaises(ValueError): self.ff("libx264", progress_path="/tmp/a b.txt")

    def test_speed_parser_and_monitor(self):
        self.assertEqual(c.parse_progress_speed("speed=N/A\nframe=3\nspeed=0.62x\n"), 0.62)
        self.assertIsNone(c.parse_progress_speed("speed=N/A\n"))
        m = c.SpeedMonitor(0.9, 10, 5, 0.0)
        self.assertFalse(m.update(0.3, 2.0))                    # grace
        self.assertFalse(m.update(0.7, 6.0)); self.assertFalse(m.update(0.7, 15.0))
        self.assertTrue(m.update(0.7, 16.5)); self.assertFalse(m.update(0.7, 30.0))     # fires once
        ok = c.SpeedMonitor(0.9, 10, 0, 0.0)
        self.assertFalse(any(ok.update(0.99, t) for t in range(100)))
        dip = c.SpeedMonitor(0.9, 10, 0, 0.0)                   # a short dip recovers
        self.assertFalse(dip.update(0.5, 1)); self.assertFalse(dip.update(1.0, 5)); self.assertFalse(dip.update(0.5, 12))

    def test_minutes_left(self):
        gb = 1024 ** 3
        self.assertEqual(c.minutes_left(3.2 * gb, 1024, 12000, 192), 25)      # the Deck in the photo: (3.2-1.0 GB) at 12.2 Mbps = 25.8 min
        self.assertEqual(c.minutes_left(500 * 1024 * 1024, 1024, 12000, 192), 0)
        self.assertGreater(c.minutes_left(100 * gb, 1024, 6000, 192), 200)

    def test_audio_offset_argv(self):
        base = self.ff("libx264")
        self.assertNotIn("-itsoffset", base)
        j = " ".join(self.ff("libx264", settings=c.sanitize_settings({"audio_offset_ms": 250})))
        self.assertIn("-itsoffset 0.250 -f pulse", j)
        j = " ".join(self.ff("libx264", settings=c.sanitize_settings({"audio_offset_ms": -120})))
        self.assertIn("-itsoffset -0.120", j)
        self.assertEqual(c.sanitize_settings({"audio_offset_ms": 99999})["audio_offset_ms"], 1000)

    def test_presets_keep_the_screen_shape_and_never_upscale(self):
        s = lambda r: c.sanitize_settings({"resolution": r})
        self.assertEqual(c.target_size(s("1280x720"), (1280, 800)), (1152, 720))    # was a stretched 1280x720 before
        self.assertEqual(c.target_size(s("854x480"), (1280, 800)), (768, 480))
        self.assertEqual(c.target_size(s("1280x800"), (1280, 800)), (1280, 800))
        self.assertEqual(c.target_size(s("1280x800"), (800, 500)), (800, 500))      # smaller screen: no upscaling
        self.assertEqual(c.target_size(s("960x600"), None), (960, 600))

    def test_step_down_ladder_ends(self):
        s = c.sanitize_settings({})
        steps = []
        while True:
            nxt = c.step_down(s)
            if not nxt:
                break
            steps.append(nxt[0]); s = c.sanitize_settings(dict(s, **nxt[1]))
        self.assertEqual(steps, ["30 fps", "960x600", "5 Mbps"])
        self.assertIsNone(c.step_down(s))

    def test_new_switches(self):
        d = c.sanitize_settings({})
        self.assertTrue(d["buffer_only_in_game"] and d["auto_step_down"])
        o = c.sanitize_settings({"buffer_only_in_game": False, "auto_step_down": False})
        self.assertFalse(o["buffer_only_in_game"] or o["auto_step_down"])

    def test_error_lines_skip_the_generic_ending(self):
        err = ("Setting pipeline to PAUSED ...\n0:00:00.05 123 0x55d0 WARN   pipewiresrc gstpipewiresrc.c:1234:on_state_changed: stream error: no more input formats\n"
               "Failed to set pipeline to PAUSED.\nSetting pipeline to NULL ...\nFreeing pipeline ...\n")
        got = c.best_error_lines(err)
        self.assertIn("no more input formats", got); self.assertNotIn("Failed to set pipeline", got)
        self.assertEqual(c.best_error_lines(""), "no output")
        self.assertEqual(c.best_error_lines("just one line"), "just one line")
        self.assertEqual(c.best_error_lines("Failed to set pipeline to PAUSED."), "Failed to set pipeline to PAUSED.")   # nothing better exists

    def test_pipewire_video_nodes_from_pw_dump(self):
        dump = ('[{"id":31,"type":"PipeWire:Interface:Node","info":{"props":{"media.class":"Audio/Sink","node.name":"alsa"}}},'
                '{"id":58,"type":"PipeWire:Interface:Node","info":{"props":{"media.class":"Video/Source","node.name":"gamescope"}}},'
                '{"id":9,"type":"PipeWire:Interface:Core"}]')
        self.assertEqual(c.parse_pw_video_nodes(dump), ["58:gamescope (Video/Source)"])
        self.assertEqual(c.parse_pw_video_nodes("not json"), []); self.assertEqual(c.parse_pw_video_nodes("{}"), [])

    def test_cost_meter_compares_recording_with_the_minute_before(self):
        m = c.CostMeter()
        for i in range(30):                                   # 60 s idle at 40% GPU, 8 W
            m.add(i * 2.0, 40, 8.0, False)
        self.assertIsNone(m.report(59.0)["delta_gpu"])        # not capturing: only 'now' values
        self.assertEqual(m.report(59.0)["gpu_now"], 40)
        for i in range(30, 50):                               # capture starts at t=60: 45% GPU, 9 W
            m.add(i * 2.0, 45, 9.0, True)
        r = m.report(99.0)
        self.assertAlmostEqual(r["delta_gpu"], 5.0); self.assertAlmostEqual(r["delta_watts"], 1.0)
        self.assertAlmostEqual(r["base_gpu"], 40.0); self.assertAlmostEqual(r["cap_gpu"], 45.0)

    def test_cost_meter_waits_for_enough_data_and_handles_missing_sensors(self):
        m = c.CostMeter()
        for i in range(3):
            m.add(i, 10, 5.0, False)
        for i in range(3, 20):
            m.add(i, 20, 6.0, True)
        self.assertIsNone(m.report(20.0)["delta_gpu"], "only 3 baseline samples: too few")
        m2 = c.CostMeter()
        for i in range(10): m2.add(i, None, None, False)
        for i in range(10, 40): m2.add(i, None, None, True)
        r = m2.report(40.0); self.assertEqual((r["delta_gpu"], r["delta_watts"]), (None, None))
        self.assertIsNone(c.CostMeter().report(1.0)["gpu_now"])

    def test_saved_probe_is_trusted_only_when_nothing_changed(self):
        fp = [["ffmpeg", "/usr/bin/ffmpeg", 1, 2], ["kernel", "6.5"]]
        good = {"v": c.CAPS_CACHE_VERSION, "time": 1000.0, "fingerprint": fp,
                "caps": {"gst_launch": "/x", "ffmpeg": "/y", "encoders": [], "ff_usable": ["h264_vaapi"]}}
        self.assertTrue(c.caps_cache_ok(good, fp, 1500.0, 86400))
        self.assertFalse(c.caps_cache_ok(good, [["ffmpeg", "/usr/bin/ffmpeg", 9, 2], ["kernel", "6.5"]], 1500.0, 86400), "tool changed")
        self.assertFalse(c.caps_cache_ok(good, [["ffmpeg", "/usr/bin/ffmpeg", 1, 2], ["kernel", "6.6"]], 1500.0, 86400), "kernel updated")
        self.assertFalse(c.caps_cache_ok(good, fp, 1000.0 + 86400, 86400), "too old")
        self.assertFalse(c.caps_cache_ok(good, fp, 900.0, 86400), "clock went backwards")
        self.assertFalse(c.caps_cache_ok(dict(good, v=0), fp, 1500.0, 86400), "old layout")
        self.assertFalse(c.caps_cache_ok(dict(good, caps={"gst_launch": "/x", "ffmpeg": "/y", "encoders": [], "ff_usable": []}), fp, 1500.0, 86400), "nothing usable")
        for junk in (None, [], "x", {"v": 1}):
            self.assertFalse(c.caps_cache_ok(junk, fp, 1500.0, 86400))

    def test_engine_setting(self):
        self.assertEqual(c.sanitize_settings({"engine": "ffmpeg"})["engine"], "ffmpeg")
        self.assertEqual(c.sanitize_settings({"engine": "x"})["engine"], "auto")
        self.assertEqual(c.sanitize_settings({"encoder": "libx264"})["encoder"], "libx264")


class Hardware(unittest.TestCase):
    @staticmethod
    def report(buttons, header=(1, 0, 9, 0x40)):
        return bytes(header) + (7).to_bytes(4, "little") + buttons.to_bytes(8, "little") + bytes(48)

    def test_report_parser(self):
        self.assertEqual(c.parse_deck_report(self.report(c.BTN_STEAM | c.BTN_MENU)), 0x6000)
        self.assertIsNone(c.parse_deck_report(self.report(1, header=(1, 0, 4, 0x40))))      # other report type
        self.assertIsNone(c.parse_deck_report(b"\x01\x00\x09"))                             # too short
        self.assertIsNone(c.parse_deck_report(b""))

    def test_detector_fires_once_per_press(self):
        d = c.HotkeyDetector(cooldown=2.0)
        self.assertIsNone(d.update(c.BTN_STEAM, 0.0)); self.assertIsNone(d.update(c.BTN_MENU, 0.1))     # one button only
        self.assertEqual(d.update(c.BTN_STEAM | c.BTN_MENU, 0.2), "clip")
        self.assertIsNone(d.update(c.BTN_STEAM | c.BTN_MENU, 0.3))                                      # still held
        self.assertIsNone(d.update(0, 0.4))
        self.assertIsNone(d.update(c.BTN_STEAM | c.BTN_MENU, 1.0))                                      # inside cooldown
        d.update(0, 1.1)
        self.assertEqual(d.update(c.BTN_STEAM | c.BTN_MENU, 3.0), "clip")

    def test_record_combo_only_when_enabled(self):
        d = c.HotkeyDetector()
        self.assertIsNone(d.update(c.BTN_STEAM | c.BTN_VIEW, 0.0, clip=True, record=False))
        d.update(0, 0.1)
        self.assertEqual(d.update(c.BTN_STEAM | c.BTN_VIEW, 5.0, clip=True, record=True), "record")

    def test_cpu_helpers(self):
        stat = "1234 (ff mpeg (x) y) S 1 1 1 0 -1 4194304 100 0 0 0 250 50 0 0 20 0 8 0 1 1 1 1"
        self.assertEqual(c.parse_stat_ticks(stat), 300)                       # utime 250 + stime 50, name has spaces/brackets
        self.assertIsNone(c.parse_stat_ticks("garbage"))
        self.assertEqual(round(c.cpu_percent(150, 2.0, 100)), 75)             # 1.5 s of CPU in 2 s
        self.assertEqual(c.cpu_percent(10, 0, 100), 0.0)

    def test_new_settings_and_x264_threads(self):
        s = c.sanitize_settings({"encoder_threads": 99, "low_priority": "no", "hid_hotkeys": False})
        self.assertEqual((s["encoder_threads"], s["low_priority"], s["hid_hotkeys"]), (8, True, False))
        self.assertEqual(c.sanitize_settings({})["encoder_threads"], 2)
        j = " ".join(c.build_ffmpeg_argv(encoder="libx264", settings=c.sanitize_settings({"encoder_threads": 3}), size=(1280, 800),
                     fps=30, audio_input=None, rolling=False, out_path="/tmp/x.mkv"))
        self.assertIn("-c:v libx264 -threads 3 -preset ultrafast", j)
        v = " ".join(c.build_ffmpeg_argv(encoder="h264_vaapi", settings=c.sanitize_settings({}), size=(1280, 800),
                     fps=30, audio_input=None, rolling=False, out_path="/tmp/x.mkv"))
        self.assertNotIn("-threads", v)


class Keepalive(unittest.TestCase):
    def build(self, src_props):
        return " ".join(c.build_gst_argv(encoder="vaapih264enc", props={"bitrate"}, split_props=set(), aac="avenc_aac",
                        settings=c.sanitize_settings({}), audio_device=None, rolling=False, out_path="/tmp/x.mkv",
                        src_props=src_props)[0])

    def test_added_only_when_the_element_has_it(self):
        self.assertIn("pipewiresrc do-timestamp=true keepalive-time=1000 !", self.build({"keepalive-time"}))
        self.assertNotIn("keepalive", self.build({"name"}))
        self.assertNotIn("keepalive", self.build(None))


class Stall(unittest.TestCase):
    def test_growing_never_stalls(self):
        d = c.StallDetector(5, 2, 0.0)
        self.assertFalse(any(d.update(i * 100, i * 0.5) for i in range(200)))
        self.assertTrue(d.ever_grew)

    def test_stops_growing_then_stalls_after_threshold(self):
        d = c.StallDetector(5, 2, 0.0)
        for i in range(20):
            d.update(i, i * 0.5)                 # grows until t=9.5
        self.assertFalse(d.update(19, 14.0))     # 4.5s without change
        self.assertTrue(d.update(19, 14.6))      # >= 5s
        self.assertTrue(d.ever_grew)

    def test_grace_period(self):
        d = c.StallDetector(1, 10, 0.0)
        self.assertFalse(d.update(0, 0.0)); self.assertFalse(d.update(0, 9.0))
        self.assertTrue(d.update(0, 10.5))
        self.assertFalse(d.ever_grew)            # never produced anything

    def test_reset_after_sleep(self):
        d = c.StallDetector(5, 2, 0.0)
        d.update(1, 0.0); d.update(2, 1.0)
        d.reset(500.0)                           # woke from suspend
        self.assertFalse(d.update(2, 500.5)); self.assertFalse(d.update(2, 503.0))
        self.assertTrue(d.update(2, 505.6))

    def test_settings(self):
        self.assertEqual(c.sanitize_settings({"stall_seconds": 0})["stall_seconds"], 0)
        self.assertEqual(c.sanitize_settings({"stall_seconds": 1})["stall_seconds"], 3)
        self.assertEqual(c.sanitize_settings({"stall_seconds": 999})["stall_seconds"], 60)
        self.assertEqual(c.sanitize_settings({"stall_seconds": "x"})["stall_seconds"], 5)


if __name__ == "__main__":
    unittest.main()
