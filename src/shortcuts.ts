// Steam + Start (and optional Steam + Select) detection. Status is exposed for the Diagnostics section.
export const shortcutInfo = { state: "pending" as "pending" | "ok" | "unavailable" | "failed", via: "", error: "", events: 0, lastButtons: 0, tries: 0 };

export function registerShortcuts(onButtons: (states: any[]) => void): () => void {
  let reg: { unregister?: () => void } | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let stopped = false;
  const delays = [1000, 3000, 8000, 20000];

  const attempt = () => {
    if (stopped) return;
    shortcutInfo.tries++;
    try {
      const input = (window as any).SteamClient?.Input;
      if (!input) throw new Error("SteamClient.Input is missing");
      if (typeof input.RegisterForControllerStateChanges !== "function") {
        // Seen on a real Deck: current Steam clients no longer offer this call. Nothing to retry; the backend's
        // controller reader (finally_hotkeys.py) is the way shortcuts work there.
        shortcutInfo.state = "unavailable";
        shortcutInfo.error = "not offered by this Steam version";
        return;
      }
      reg = input.RegisterForControllerStateChanges((states: any[]) => {
        shortcutInfo.events++;
        for (const s of states ?? []) {
          if (s?.ulButtons) shortcutInfo.lastButtons = s.ulButtons;
        }
        onButtons(states);
      });
      shortcutInfo.state = "ok";
      shortcutInfo.via = "RegisterForControllerStateChanges";
      shortcutInfo.error = "";
    } catch (e: any) {
      shortcutInfo.state = "failed";
      shortcutInfo.error = String(e?.message ?? e).slice(0, 200);
      if (shortcutInfo.tries <= delays.length) {
        timer = setTimeout(attempt, delays[shortcutInfo.tries - 1]);
      }
    }
  };
  attempt();
  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    try {
      reg?.unregister?.();
    } catch {
      /* already gone */
    }
  };
}
