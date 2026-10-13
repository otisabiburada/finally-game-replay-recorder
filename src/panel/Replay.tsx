// Section: replay buffer toggle, save-clip button, clip/buffer length.
import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { setReplay, saveClip } from "../api";
import { currentGame, fmtTime, toast } from "../util";
import type { Ctx } from "./ctx";

const BUFFER_CHOICES = [15, 30, 60, 120, 300, 600];
const CLIP_CHOICES = [15, 30, 60, 120, 300];

export function ReplaySection({ st, setCfg, cfg, busy, update, guard, recording, buffering, saving}: Ctx) {
  return (
    <>
      <PanelSection title="Replay buffer">
        <PanelSectionRow>
          <ToggleField
            label="Replay buffer"
            description={`Keeps the last ${fmtTime(cfg.buffer_seconds)} in RAM. Steam + Start saves a clip.`}
            checked={cfg.replay_enabled}
            disabled={busy || recording || saving}
            onChange={(v: boolean) => guard(async () => { await setReplay(v); setCfg({ ...cfg, replay_enabled: v }); })}
          />
        </PanelSectionRow>
        {cfg.replay_enabled && st.paused && (
          <PanelSectionRow>
            <div style={{ fontSize: 12, color: "#ffd27a" }}>Paused: no game is running. It starts again with the next game.</div>
          </PanelSectionRow>
        )}
        {cfg.replay_enabled && (
          <>
            <PanelSectionRow>
              <ButtonItem
                layout="below"
                disabled={busy || !buffering}
                onClick={() => guard(async () => {
                  const r = await saveClip(currentGame(), 0);
                  if (!r.ok) toast("Clip not saved", r.error ?? "Unknown error", true);
                })}
              >
                Save last {cfg.clip_seconds}s
              </ButtonItem>
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="Clip length"
                rgOptions={CLIP_CHOICES.filter((c) => c <= cfg.buffer_seconds).map((c) => ({ data: c, label: fmtTime(c) }))}
                selectedOption={cfg.clip_seconds}
                onChange={(o: any) => void update({ clip_seconds: o.data })}
              />
            </PanelSectionRow>
            <PanelSectionRow>
              <DropdownItem
                label="Buffer length"
                description={`About ${Math.ceil(((cfg.video_kbps + cfg.audio_kbps) * cfg.buffer_seconds) / 8 / 1024)} MB of RAM`}
                rgOptions={BUFFER_CHOICES.map((c) => ({ data: c, label: fmtTime(c) }))}
                selectedOption={cfg.buffer_seconds}
                onChange={(o: any) => void update({ buffer_seconds: o.data, clip_seconds: Math.min(cfg.clip_seconds, o.data) })}
              />
            </PanelSectionRow>
          </>
        )}
      </PanelSection>
    </>
  );
}
