// Panel root: owns status/settings/options/recent files, hands them to the sections below.
import { PanelSection, PanelSectionRow } from "@decky/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { addEventListener, removeEventListener } from "@decky/api";
import { EVENT, getOptions, getSettings, getStatus, listRecent, setSettings } from "../api";
import type { Options, RecentFile, Saved, Settings, Status } from "../api";
import { fmtBytes, fmtTime, toast } from "../util";
import type { Ctx } from "./ctx";
import { DiagnosticsSection } from "./Diagnostics";
import { LastSavedSection } from "./LastSaved";
import { RecentSection } from "./Recent";
import { RecordSection } from "./Record";
import { ReplaySection } from "./Replay";
import { SettingsSection } from "./Settings";

/** Owns the data (status, settings, options, recent files) and hands it to the sections. */
export function Content() {
  const [st, setSt] = useState<Status | null>(null);
  const [cfg, setCfg] = useState<Settings | null>(null);
  const [opts, setOpts] = useState<Options | null>(null);
  const [recent, setRecent] = useState<RecentFile[]>([]);
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const s = await getStatus();
      if (alive.current) setSt(s);
    } catch {
      /* backend restarting */
    }
  }, []);

  const refreshRecent = useCallback(async () => {
    try {
      const r = await listRecent();
      if (alive.current) setRecent(r);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void (async () => {
      try {
        const [c, o] = await Promise.all([getSettings(), getOptions()]);
        if (!alive.current) return;
        setCfg(c);
        setOpts(o);
      } catch {
        /* shown as 'loading' */
      }
      void refresh();
      void refreshRecent();
    })();
    const timer = setInterval(() => void refresh(), 1000);
    const saved = addEventListener<[{ type: string }]>(EVENT, (e) => {
      if (e?.type === "saved" || e?.type === "deleted") void refreshRecent();
    });
    return () => {
      alive.current = false;
      clearInterval(timer);
      removeEventListener(EVENT, saved);
    };
  }, [refresh, refreshRecent]);

  const update = async (patch: Partial<Settings>) => {
    setCfg((c) => (c ? { ...c, ...patch } : c));
    try {
      const next = await setSettings(patch);
      if (alive.current) setCfg(next);
    } catch {
      toast("Settings", "Could not save settings", true);
    }
    void refresh();
  };

  const guard = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
    } finally {
      setTimeout(() => alive.current && setBusy(false), 600);
      void refresh();
    }
  };

  if (!st || !cfg || !opts) {
    return (
      <PanelSection title="Finally Game Recorder">
        <PanelSectionRow><div>Loading...</div></PanelSectionRow>
      </PanelSection>
    );
  }

  const recording = st.state === "recording";
  const buffering = st.state === "buffering";
  const saving = st.state === "saving";
  const lowDisk = st.free > 0 && st.free < cfg.min_free_mb * 1024 * 1024;
  const stateLine =
    recording ? `REC  ${fmtTime(st.elapsed)}  ·  ${fmtBytes(st.size)}`
    : saving ? "Saving the file..."
    : buffering ? `Replay buffer on  ·  ${fmtTime(st.buffer_fill)} / ${fmtTime(cfg.buffer_seconds)}`
    : st.state === "error" ? "Error"
    : "Ready";

  const last = st.last_saved as Partial<Saved>;
  const sdLoc = opts.locations.find((l) => l.label.startsWith("SD/USB"));
  const hasSd = !!sdLoc && st.minutes_left < 60;
  const onSd = !!sdLoc && cfg.output_folder === sdLoc.path;

  const ctx: Ctx = { st, cfg, opts, recent, busy, update, guard, refresh, refreshRecent, recording, buffering, saving,
    lowDisk, stateLine, last, hasSd, onSd, setCfg };

  return (
    <>
      <RecordSection {...ctx} />
      <ReplaySection {...ctx} />
      {last.name && <LastSavedSection {...ctx} />}
      <SettingsSection {...ctx} />
      <RecentSection {...ctx} />
      <DiagnosticsSection {...ctx} />
    </>
  );
}
