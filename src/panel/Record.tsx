// Section: setup warning, status line, start/stop button.
import { ButtonItem, PanelSection, PanelSectionRow, Router } from "@decky/ui";
import { startRecording, stopRecording } from "../api";
import { currentGame, fmtBytes } from "../util";
import type { Ctx } from "./ctx";

export function RecordSection({ st, opts, busy, guard, recording, saving, lowDisk, stateLine, hasSd, onSd, buffering }: Ctx) {
  return (
    <>
      {!opts.can_record && (
        <PanelSection title="Setup needed">
          <PanelSectionRow>
            <div style={{ fontSize: 12 }}>
              Recording is not possible yet: {opts.engine_reason} Open Diagnostics and run the self-test, then send a photo.
            </div>
          </PanelSectionRow>
        </PanelSection>
      )}


      <PanelSection title="Record">
        <PanelSectionRow>
          <div style={{ fontWeight: 600, color: recording ? "#ff5a5a" : undefined }}>{stateLine}</div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: 11, opacity: 0.7 }}>
            Engine: {st.engine || opts.engine}{st.encoder ? ` · ${st.encoder}` : ""}
            {st.speed ? ` · ${st.speed.toFixed(2)}x` : ""}
            {st.cpu != null ? ` · CPU ${st.cpu}% (100 = one core)` : ""}
            {` · about ${st.minutes_left} min of space left`}
          </div>
        </PanelSectionRow>
        {(st.gpu != null || st.watts != null) && (
          <PanelSectionRow>
            <div style={{ fontSize: 11, opacity: 0.7 }}>
              {st.gpu != null ? `GPU ${st.gpu}%` : ""}{st.gpu != null && st.watts != null ? " · " : ""}{st.watts != null ? `${st.watts} W (${st.power_src})` : ""}
            </div>
          </PanelSectionRow>
        )}
        {(recording || buffering) && st.cost && st.cost.delta_gpu != null && (
          <PanelSectionRow>
            <div style={{ fontSize: 12 }}>
              Recording adds about {st.cost.delta_gpu >= 0 ? "+" : ""}{st.cost.delta_gpu.toFixed(0)}% GPU
              {st.cost.delta_watts != null ? `, ${st.cost.delta_watts >= 0 ? "+" : ""}${st.cost.delta_watts.toFixed(1)} W` : ""} (rough: compared with the minute before it started)
            </div>
          </PanelSectionRow>
        )}
        {(recording || buffering) && st.encoder.startsWith("libx264") && (
          <PanelSectionRow>
            <div style={{ fontSize: 12, color: "#ffd27a" }}>
              Software encoding uses CPU the game may need. 30 fps and a lower resolution reduce it.
            </div>
          </PanelSectionRow>
        )}
        {hasSd && !onSd && (
          <PanelSectionRow>
            <div style={{ fontSize: 12, color: "#ffd27a" }}>An SD card is available. Settings, Save to, lets you record there.</div>
          </PanelSectionRow>
        )}
        {st.error && st.state === "error" && (
          <PanelSectionRow><div style={{ fontSize: 12, color: "#ff9a9a" }}>{st.error}</div></PanelSectionRow>
        )}
        {st.warnings.map((w, i) => (
          <PanelSectionRow key={i}><div style={{ fontSize: 12, color: "#ffd27a" }}>{w}</div></PanelSectionRow>
        ))}
        {lowDisk && (
          <PanelSectionRow>
            <div style={{ fontSize: 12, color: "#ffd27a" }}>Only {fmtBytes(st.free)} free in the output folder.</div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={busy || saving}
            onClick={() => guard(async () => {
              if (recording) {
                await stopRecording();
              } else {
                await startRecording(currentGame());
                Router.CloseSideMenus();
              }
            })}
          >
            {recording ? "Stop recording" : "Start recording"}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}
