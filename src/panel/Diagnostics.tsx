// Section: shortcut status, self-test results, log tail.
import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { TestResult, runSelfTest, getLogTail, getHotkeyStatus } from "../api";
import type { HotkeyStatus } from "../api";
import { shortcutInfo } from "../shortcuts";
import type { Ctx } from "./ctx";

export function DiagnosticsSection({ st, opts, recording, buffering, saving }: Ctx) {
  const [showDiag, setShowDiag] = useState(false);
  const [tests, setTests] = useState<TestResult[] | null>(null);
  const [testing, setTesting] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [hid, setHid] = useState<HotkeyStatus | null>(null);
  useEffect(() => {
    let alive = true;
    const poll = () => void getHotkeyStatus().then((h) => alive && setHid(h)).catch(() => undefined);
    poll();
    const timer = setInterval(poll, 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  return (
    <>
      <PanelSection title="Summary (photograph this)">
        <PanelSectionRow>
          <div style={{ fontSize: 12, color: opts.can_record ? "#8fe3a0" : "#ff9a9a" }}>
            {opts.can_record
              ? `Recording: ${opts.engine} engine · ${opts.encoders.length ? opts.encoders.join(" / ") : "no encoder"}${opts.encoders[0] === "h264_vaapi" ? " (GPU)" : opts.encoders[0] ? " (CPU)" : ""}`
              : `Recording NOT possible: ${opts.engine_reason}`}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: 12, color: opts.pulse || opts.aac ? "#8fe3a0" : "#ffd27a" }}>
            Audio: {opts.pulse ? "ffmpeg pulse input OK" : opts.aac ? "GStreamer AAC" : "video only (no audio encoder/input)"}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: 12, color: hid && hid.state === "reading" ? "#8fe3a0" : "#ff9a9a" }}>
            Buttons: {hid ? `${hid.state}${hid.error ? " (" + hid.error + ")" : ""} · valid ${hid.valid} · last 0x${hid.last_buttons.toString(16)} · fired ${hid.fired}` : "..."}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: 12, opacity: 0.85 }}>
            Steam UI shortcut API: {shortcutInfo.state === "ok" ? "OK" : shortcutInfo.state === "unavailable" ? "not offered by this Steam (normal, buttons use the reader)" : shortcutInfo.state + " " + shortcutInfo.error}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: 12, opacity: 0.85 }}>
            Sensors: {st.gpu != null ? `GPU ${st.gpu}%` : "GPU n/a"} · {st.watts != null ? `${st.watts} W (${st.power_src})` : "power n/a"}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <div style={{ fontSize: 12, opacity: 0.85 }}>
            Now: {st.state}{st.engine ? ` · ${st.engine}` : ""}{st.cpu != null ? ` · CPU ${st.cpu}%` : ""}{st.speed ? ` · ${st.speed.toFixed(2)}x` : ""} · {st.minutes_left} min space
          </div>
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Diagnostics">
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={testing || recording || buffering || saving}
            onClick={async () => {
              setTesting(true);
              setShowDiag(true);
              try {
                setTests(await runSelfTest());
              } catch {
                setTests([{ name: "Self-test", ok: false, detail: "backend did not answer" }]);
              }
              setLogLines(await getLogTail().catch(() => []));
              setTesting(false);
            }}
          >
            {testing ? "Testing..." : "Run self-test"}
          </ButtonItem>
        </PanelSectionRow>
        {(recording || buffering) && (
          <PanelSectionRow><div style={{ fontSize: 12 }}>Stop recording / replay first to run the self-test.</div></PanelSectionRow>
        )}
        {hid && (
          <PanelSectionRow>
            <div style={{ fontSize: 12, wordBreak: "break-all", color: hid.state === "reading" ? "#8fe3a0" : "#ff9a9a" }}>
              Controller reader: {hid.state}{hid.error ? ` (${hid.error})` : ""}
              {` · ${hid.path || "no device"} · valid ${hid.valid} / raw ${hid.raw} · last 0x${hid.last_buttons.toString(16)} · fired ${hid.fired}`}
              {hid.headers.length ? ` · headers ${hid.headers.join(" | ")}` : ""}
            </div>
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <div style={{ fontSize: 12, wordBreak: "break-all", color: shortcutInfo.state === "failed" ? "#ff9a9a" : "#8fe3a0" }}>
            Shortcut: {shortcutInfo.state === "ok" ? `registered (${shortcutInfo.via})` : `${shortcutInfo.state}: ${shortcutInfo.error}`}
            {` · tries ${shortcutInfo.tries} · events ${shortcutInfo.events} · last buttons 0x${shortcutInfo.lastButtons.toString(16)}`}
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            onClick={async () => {
              setShowDiag(true);
              setLogLines(await getLogTail().catch(() => []));
            }}
          >
            Show log
          </ButtonItem>
        </PanelSectionRow>
        {showDiag && tests && (
          <>
            {tests.filter((x) => !x.ok).map((x, i) => (
              <PanelSectionRow key={"f" + i}>
                <div style={{ fontSize: 12, wordBreak: "break-all", color: "#ff9a9a" }}>FAIL {x.name}{x.detail ? ` - ${x.detail}` : ""}</div>
              </PanelSectionRow>
            ))}
            <PanelSectionRow>
              <div style={{ fontSize: 11, wordBreak: "break-all", color: "#8fe3a0" }}>
                {tests.filter((x) => x.ok).length} checks OK: {tests.filter((x) => x.ok).map((x) => x.name.replace(/^(test |Screen capture - )/, "")).join(", ")}
              </div>
            </PanelSectionRow>
          </>
        )}
        {showDiag && logLines.length > 0 && (
          <PanelSectionRow>
            <div style={{ fontSize: 10, whiteSpace: "pre-wrap", wordBreak: "break-all", opacity: 0.8 }}>{logLines.join("\n")}</div>
          </PanelSectionRow>
        )}
      </PanelSection>
    </>
  );
}
