// Things the panel buttons AND the controller shortcuts both do.
import { Router } from "@decky/ui";
import { getSettings, getStatus, saveClip, setReplay, startRecording, stopRecording } from "./api";
import { currentGame, toast } from "./util";

let lastTrigger = 0;

/** Both detection paths (Steam UI events and the hardware reader) end here; 2 s cooldown stops double triggers. */
export function trigger(kind: string): void {
  if (Date.now() - lastTrigger < 2000) return;
  lastTrigger = Date.now();
  try {
    // the Steam button may have opened Steam's menu by now; same trick the original plugin used
    (Router as any).DisableHomeAndQuickAccessButtons?.();
    setTimeout(() => (Router as any).EnableHomeAndQuickAccessButtons?.(), 1000);
    Router.CloseSideMenus();
  } catch {
    /* optional */
  }
  void (kind === "record" ? actionToggleRecording() : actionSaveClip());
}

export async function actionToggleRecording(): Promise<void> {
  const st = await getStatus();
  if (st.state === "recording") {
    await stopRecording();
    toast("Recording stopped", "Saving the file...");
  } else if (st.state === "idle" || st.state === "buffering" || st.state === "error") {
    const res = await startRecording(currentGame());
    if (res.state === "recording") toast("Recording started", currentGame() || "Game Mode", false, 2000);
  }
}

export async function actionSaveClip(): Promise<void> {
  const st = await getStatus();
  if (st.state !== "buffering") {
    const s = await getSettings();
    await setReplay(true);
    toast("Replay buffer on", `Press the shortcut again to save the last ${s.clip_seconds}s`, false, 3500);
    return;
  }
  toast("Saving clip...", "Hold on, writing the file", false, 2000);
  const res = await saveClip(currentGame(), 0);
  if (!res.ok) toast("Clip not saved", res.error ?? "Unknown error", true);
}
