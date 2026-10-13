// Section: video/audio/mic/shortcut settings.
import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { useState } from "react";
import { PROFILES, currentProfile } from "../profiles";
import type { Ctx } from "./ctx";

export function SettingsSection({ cfg, opts, update }: Ctx) {
  const [showSettings, setShowSettings] = useState(false);
  const [showMic, setShowMic] = useState(false);
  return (
    <>
      <PanelSection title="Settings">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => setShowSettings(!showSettings)}>
            {showSettings ? "Hide video & audio settings" : "Video & audio settings"}
          </ButtonItem>
        </PanelSectionRow>
        {showSettings && (
          <>
            <PanelSectionRow>
              <DropdownItem
                label="Quality profile"
                description="Lighter = less load on the game. Individual settings below can still be changed."
                rgOptions={[...Object.entries(PROFILES).map(([key, p]) => ({ data: key, label: p.label })), { data: "custom", label: "Custom (settings below)" }]}
                selectedOption={currentProfile(cfg)}
                onChange={(o: any) => {
                  if (PROFILES[o.data]) void update(PROFILES[o.data].patch);
                }}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Replay buffer only while a game runs"
                description="Saves power and GPU time at the Steam home screen."
                checked={cfg.buffer_only_in_game}
                onChange={(v: boolean) => void update({ buffer_only_in_game: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Lower quality automatically if too slow"
                description="30 fps first, then a smaller picture, then a lower bitrate."
                checked={cfg.auto_step_down}
                onChange={(v: boolean) => void update({ auto_step_down: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="Save to"
                description={cfg.output_folder || "Videos (internal)"}
                rgOptions={opts.locations.map((l) => ({ data: l.path, label: l.label }))}
                selectedOption={cfg.output_folder || opts.locations[0]?.path}
                onChange={(o: any) => void update({ output_folder: o.data })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="File format"
                rgOptions={[{ data: "mkv", label: "MKV (safest)" }, { data: "mp4", label: "MP4" }]}
                selectedOption={cfg.format}
                onChange={(o: any) => void update({ format: o.data })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="Resolution"
                description="Scaling is applied with the vaapi/x264 encoders"
                rgOptions={[
                  { data: "native", label: "Native" },
                  { data: "1280x800", label: "1280x800" },
                  { data: "1280x720", label: "1280x720" },
                  { data: "960x600", label: "960x600" },
                  { data: "854x480", label: "854x480" },
                ]}
                selectedOption={cfg.resolution}
                onChange={(o: any) => void update({ resolution: o.data })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="Frame rate"
                rgOptions={[{ data: 0, label: "Native" }, { data: 30, label: "30 fps (smaller files)" }]}
                selectedOption={cfg.fps}
                onChange={(o: any) => void update({ fps: o.data })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Video bitrate (kbps)"
                value={cfg.video_kbps}
                min={2000}
                max={30000}
                step={1000}
                showValue
                onChange={(v: number) => void update({ video_kbps: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Audio bitrate (kbps)"
                value={cfg.audio_kbps}
                min={96}
                max={320}
                step={32}
                showValue
                onChange={(v: number) => void update({ audio_kbps: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="Engine"
                description={opts.engine_reason}
                rgOptions={[{ data: "auto", label: "Auto" }, { data: "gstreamer", label: "GStreamer only" }, { data: "ffmpeg", label: "ffmpeg encode" }]}
                selectedOption={cfg.engine}
                onChange={(o: any) => void update({ engine: o.data })}
              />
            </PanelSectionRow>
            {opts.encoders.length > 1 && (
              <PanelSectionRow>
                <DropdownItem
                  label="Encoder"
                  rgOptions={[{ data: "auto", label: "Auto" }, ...opts.encoders.map((e) => ({ data: e, label: e }))]}
                  selectedOption={cfg.encoder}
                  onChange={(o: any) => void update({ encoder: o.data })}
                />
              </PanelSectionRow>
            )}
            <PanelSectionRow>
              <ToggleField
                label="Record game audio"
                checked={cfg.game_audio}
                onChange={(v: boolean) => void update({ game_audio: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Record microphone"
                checked={cfg.mic_enabled}
                onChange={(v: boolean) => void update({ mic_enabled: v })}
              />
            </PanelSectionRow>
            {cfg.mic_enabled && (
              <>
                <PanelSectionRow>
                  <ButtonItem layout="below" onClick={() => setShowMic(!showMic)}>
                    {showMic ? "Hide microphone options" : "Microphone options"}
                  </ButtonItem>
                </PanelSectionRow>
                {showMic && (
                  <>
                    <PanelSectionRow>
                      <SliderField
                        label="Microphone gain (dB)"
                        value={cfg.mic_gain_db}
                        min={-10}
                        max={30}
                        step={1}
                        showValue
                        onChange={(v: number) => void update({ mic_gain_db: v })}
                      />
                    </PanelSectionRow>
                    <PanelSectionRow>
                      <ToggleField
                        label="Echo cancellation"
                        checked={cfg.mic_cleanup}
                        onChange={(v: boolean) => void update({ mic_cleanup: v })}
                      />
                    </PanelSectionRow>
                    {cfg.mic_cleanup && opts.rnnoise && (
                      <PanelSectionRow>
                        <SliderField
                          label="Noise reduction"
                          value={cfg.noise_reduction}
                          min={0}
                          max={100}
                          step={5}
                          showValue
                          valueSuffix="%"
                          onChange={(v: number) => void update({ noise_reduction: v })}
                        />
                      </PanelSectionRow>
                    )}
                    <PanelSectionRow>
                      <DropdownItem
                        label="Microphone source"
                        rgOptions={[{ data: "", label: "System default" }, ...opts.sources.map((s) => ({ data: s, label: s }))]}
                        selectedOption={cfg.mic_source}
                        onChange={(o: any) => void update({ mic_source: o.data })}
                      />
                    </PanelSectionRow>
                  </>
                )}
              </>
            )}
            <PanelSectionRow>
              <SliderField
                label="Sound delay (ms)"
                description="Clap test: if the sound comes too early raise it, too late lower it. ffmpeg engine only."
                value={cfg.audio_offset_ms}
                min={-500}
                max={1000}
                step={10}
                showValue
                onChange={(v: number) => void update({ audio_offset_ms: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Low CPU priority for recording"
                description="Recording yields to the game. Turn off only if the recording stutters."
                checked={cfg.low_priority}
                onChange={(v: boolean) => void update({ low_priority: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Software encoder threads"
                description="Only for libx264. Fewer threads take less CPU from the game; the Encoder warning tells you if it is too few."
                value={cfg.encoder_threads}
                min={1}
                max={6}
                step={1}
                showValue
                onChange={(v: number) => void update({ encoder_threads: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Read the controller directly"
                description="Makes the shortcuts work inside games. Diagnostics shows whether it sees the controller."
                checked={cfg.hid_hotkeys}
                onChange={(v: boolean) => void update({ hid_hotkeys: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Shortcut: Steam + Start = save clip"
                checked={cfg.hotkey_clip}
                onChange={(v: boolean) => void update({ hotkey_clip: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <ToggleField
                label="Shortcut: Steam + Select = start/stop"
                description="Experimental: button number not yet confirmed on a Deck"
                checked={cfg.hotkey_record}
                onChange={(v: boolean) => void update({ hotkey_record: v })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Restart if capture freezes (seconds)"
                description="0 = off. Saves what exists and continues. Turn off if a static screen triggers it."
                value={cfg.stall_seconds}
                min={0}
                max={30}
                step={1}
                showValue
                onChange={(v: number) => void update({ stall_seconds: v === 0 ? 0 : Math.max(3, v) })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <SliderField
                label="Stop when free space below (MB)"
                value={cfg.min_free_mb}
                min={200}
                max={10000}
                step={200}
                showValue
                onChange={(v: number) => void update({ min_free_mb: v })}
              />
            </PanelSectionRow>
          </>
        )}
      </PanelSection>
    </>
  );
}
