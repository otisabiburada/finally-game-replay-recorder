// Full-screen page that plays one clip. Controls sit at the TOP, because Steam's button-hint bar covers the bottom.
// A on a button, L1/R1 = -/+ 15 s, B = back.
import { DialogButton, Focusable, Navigation } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { deleteRecording, preparePlayback } from "../api";
import { playerState } from "../player";
import { buttonCode, fmtTime, toast } from "../util";

const BUMPER_LEFT = buttonCode(/^BUMPER_LEFT$/, /bumper.*left|left.*bumper|^L1$/i);
const BUMPER_RIGHT = buttonCode(/^BUMPER_RIGHT$/, /bumper.*right|right.*bumper|^R1$/i);
const FOOTER_SPACE = 84; // px kept free at the bottom for Steam's button-hint bar

const ERRORS: Record<number, string> = {
  1: "Playback was aborted.",
  2: "The clip could not be loaded from the local server.",
  3: "The clip is damaged or cut off.",
  4: "Steam's browser cannot play this format.",
};

export function Player() {
  const video = useRef<any>(null);
  const [src, setSrc] = useState(playerState.url);
  const [format, setFormat] = useState(playerState.format);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState("");
  const [converting, setConverting] = useState(false);
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    const v = video.current;
    if (v && src) void v.play?.().catch(() => undefined);
    return () => {
      const el = video.current;
      if (el) {
        el.pause?.();
        el.removeAttribute?.("src");
        el.load?.();
      }
    };
  }, [src]);

  const seek = (delta: number) => {
    const v = video.current;
    if (v) v.currentTime = Math.max(0, Math.min((v.duration || 0) - 0.1, v.currentTime + delta));
  };
  const toggle = () => {
    const v = video.current;
    if (!v) return;
    if (v.paused) void v.play?.().catch(() => undefined);
    else v.pause();
  };
  const onBumpers = (evt: any) => {
    const button = evt?.detail?.button;
    if (button === undefined) return;
    if (BUMPER_LEFT !== undefined && button === BUMPER_LEFT) seek(-15);
    else if (BUMPER_RIGHT !== undefined && button === BUMPER_RIGHT) seek(15);
  };
  const convert = async () => {
    setConverting(true);
    setError("");
    try {
      const r = await preparePlayback(playerState.name, "webm");
      if (r.ok && r.url) {
        setFormat("webm");
        setSrc(r.url);
      } else {
        setError(r.error ?? "Conversion failed.");
      }
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
    setConverting(false);
  };
  // Delete needs a second press within 4 s, same as in the recordings list.
  const remove = async () => {
    if (!armed) {
      setArmed(true);
      setTimeout(() => setArmed(false), 4000);
      return;
    }
    video.current?.pause?.();
    try {
      const r = await deleteRecording(playerState.name);
      if (!r.ok) {
        toast("Delete failed", r.error ?? "", true);
        setArmed(false);
        return;
      }
      toast("Deleted", playerState.name);
      Navigation.NavigateBack();
    } catch (e: any) {
      toast("Delete failed", String(e?.message ?? e), true);
      setArmed(false);
    }
  };

  const pct = duration > 0 ? Math.min(100, (100 * time) / duration) : 0;
  return (
    <div style={{ marginTop: 40, height: "calc(100% - 40px)", display: "flex", flexDirection: "column", padding: `6px 24px ${FOOTER_SPACE}px`, boxSizing: "border-box" }}>
      <div style={{ fontSize: 15, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", marginBottom: 6 }}>{playerState.name}</div>
      <Focusable style={{ display: "flex", gap: 8, marginBottom: 8 }} flow-children="horizontal" onButtonDown={onBumpers}>
        <DialogButton onClick={() => seek(-15)}>-15 s</DialogButton>
        <DialogButton onClick={toggle}>{playing ? "Pause" : "Play"}</DialogButton>
        <DialogButton onClick={() => seek(15)}>+15 s</DialogButton>
        {error && format === "mp4" && (
          <DialogButton disabled={converting} onClick={() => void convert()}>{converting ? "Converting..." : "Try compatible format"}</DialogButton>
        )}
        <DialogButton onClick={() => void remove()}>{armed ? "Press again to DELETE" : "Delete"}</DialogButton>
        <DialogButton onClick={() => Navigation.NavigateBack()}>Back</DialogButton>
      </Focusable>
      {error && (
        <div style={{ fontSize: 12, color: "#ff9a9a", marginBottom: 6 }}>
          {error} {format === "mp4" ? "You can try a slower, more compatible conversion, or open the file on your PC." : "Open the file on your PC instead."}
        </div>
      )}
      <video
        ref={video}
        src={src}
        style={{ flex: 1, minHeight: 0, width: "100%", background: "#000", objectFit: "contain" }}
        onTimeUpdate={() => setTime(video.current?.currentTime ?? 0)}
        onLoadedMetadata={() => setDuration(video.current?.duration ?? 0)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onError={() => setError(ERRORS[video.current?.error?.code ?? 0] ?? "Playback failed.")}
      />
      <div style={{ height: 6, background: "rgba(255,255,255,0.15)", borderRadius: 3, margin: "8px 0 4px" }}>
        <div style={{ height: 6, width: `${pct}%`, background: "#1a9fff", borderRadius: 3 }} />
      </div>
      <div style={{ fontSize: 12, opacity: 0.8 }}>
        {fmtTime(time)} / {fmtTime(duration)} · {format}{playerState.note && format === "mp4" ? ` · ${playerState.note}` : ""}
      </div>
    </div>
  );
}
