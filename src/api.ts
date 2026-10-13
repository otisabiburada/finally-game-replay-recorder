// Backend contract: types and callable() wrappers. One place to change when a backend method changes.
import { callable } from "@decky/api";

export interface Saved { name: string; path: string; size: number; kind: string; ts: number }
export interface Status {
  state: "idle" | "recording" | "buffering" | "saving" | "error";
  mode: "manual" | "replay" | null;
  elapsed: number;
  size: number;
  free: number;
  encoder: string;
  game: string;
  last_saved: Saved | Record<string, never>;
  error: string;
  warnings: string[];
  engine: string;
  speed: number | null;
  cpu: number | null;
  paused: boolean;
  gpu: number | null;
  watts: number | null;
  power_src: string;
  cost: { delta_gpu: number | null; delta_watts: number | null; base_gpu: number | null; cap_gpu: number | null } | null;
  minutes_left: number;
  replay: boolean;
  buffer_fill: number;
}
export interface Settings {
  format: string; output_folder: string; resolution: string; fps: number; video_kbps: number; audio_kbps: number;
  encoder: string; game_audio: boolean; mic_enabled: boolean; mic_source: string; mic_gain_db: number;
  mic_cleanup: boolean; noise_reduction: number; replay_enabled: boolean; buffer_seconds: number;
  clip_seconds: number; min_free_mb: number; hotkey_clip: boolean; hotkey_record: boolean; stall_seconds: number; engine: string; audio_offset_ms: number;
  low_priority: boolean; encoder_threads: number; hid_hotkeys: boolean;
  buffer_only_in_game: boolean; auto_step_down: boolean;
}
export interface Options {
  encoders: string[]; aac: string | null; sources: string[]; rnnoise: boolean;
  locations: { label: string; path: string }[]; have_gst: boolean; have_ffmpeg: boolean;
  engine: string; engine_reason: string; can_record: boolean; pulse: boolean;
}
export interface RecentFile { name: string; size: number; mtime: number }
export interface TestResult { name: string; ok: boolean; detail: string }
export interface ClipResult { ok: boolean; error?: string; name?: string }

export const EVENT = "finally_recorder_event";

export const getStatus = callable<[], Status>("get_status");
export const getSettings = callable<[], Settings>("get_settings");
export const setSettings = callable<[Partial<Settings>], Settings>("set_settings");
export const getOptions = callable<[], Options>("get_options");
export const startRecording = callable<[string], Status>("start_recording");
export const stopRecording = callable<[], Status>("stop_recording");
export const setReplay = callable<[boolean], Status>("set_replay");
export const saveClip = callable<[string, number], ClipResult>("save_clip");
export const listRecent = callable<[], RecentFile[]>("list_recent");
export const deleteRecording = callable<[string], { ok: boolean; error?: string }>("delete_recording");
export const runSelfTest = callable<[], TestResult[]>("run_self_test");
export interface PlaybackResult { ok: boolean; url?: string; format?: string; note?: string; name?: string; size?: number; error?: string }
export const reportGame = callable<[string], { ok: boolean }>("report_game");
export const preparePlayback = callable<[string, string], PlaybackResult>("prepare_playback");
export interface HotkeyStatus { state: string; path: string; raw: number; valid: number; last_buttons: number; headers: string[]; error: string; fired: number }
export const getHotkeyStatus = callable<[], HotkeyStatus>("get_hotkey_status");
export const getLogTail = callable<[], string[]>("get_log_tail");
