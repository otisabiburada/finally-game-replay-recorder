// Plugin entry: registers the controller shortcut, toasts for backend events, and the Quick Access panel.
import { addEventListener, definePlugin, removeEventListener, routerHook } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { trigger } from "./actions";
import { EVENT, getSettings, reportGame } from "./api";
import { Content } from "./panel/Content";
import { Player } from "./panel/Player";
import { PLAYER_ROUTE } from "./player";
import { registerShortcuts } from "./shortcuts";
import { currentGame, toast } from "./util";

export default definePlugin(() => {
  let hotClip = true;
  let hotRecord = false;

  const syncHotkeys = async () => {
    try {
      const s = await getSettings();
      hotClip = s.hotkey_clip;
      hotRecord = s.hotkey_record;
    } catch {
      /* keep previous */
    }
  };
  void syncHotkeys();

  // Tell the backend which game is running, so the replay buffer can wait on the home screen. Re-sent every ~30 s in
  // case the backend restarted.
  let lastGame: string | null = null;
  let ticks = 0;
  const pollGame = () => {
    const g = currentGame();
    if (g !== lastGame || ++ticks % 15 === 0) {
      lastGame = g;
      void reportGame(g).catch(() => undefined);
    }
  };
  pollGame();
  const gameTimer = setInterval(pollGame, 2000);
  const settingsTimer = setInterval(() => void syncHotkeys(), 5000);

  // Steam = bit 13, Start = bit 14 - the same combination the original decky-recorder uses.
  // Select = bit 12 comes from that plugin's comment list and is unconfirmed.
  const onButtons = (states: any[]) => {
    for (const input of states ?? []) {
      const b: number = input?.ulButtons ?? 0;
      if (!(b & (1 << 13))) continue;
      const clip = hotClip && (b & (1 << 14));
      const rec = hotRecord && (b & (1 << 12));
      if (!clip && !rec) continue;
      trigger(clip ? "clip" : "record");
    }
  };

  routerHook.addRoute(PLAYER_ROUTE, Player);
  const unregisterShortcuts = registerShortcuts(onButtons);

  const listener = addEventListener<[{ type: string; name?: string; kind?: string; message?: string; action?: string; state?: string }]>(EVENT, (e) => {
    if (!e) return;
    if (e.type === "hotkey") trigger(e.action ?? "clip");
    else if (e.type === "saved") toast(e.kind === "clip" ? "Clip saved" : "Recording saved", e.name ?? "");
    else if (e.type === "warning") toast("Recorder", e.message ?? "", false, 5000);
    else if (e.type === "error") toast("Recorder error", e.message ?? "Unknown error", true, 8000);
  });

  return {
    name: "Finally Game Recorder",
    titleView: <div className={staticClasses.Title}>Finally Game Recorder</div>,
    content: <Content />,
    icon: (
      <svg width="1em" height="1em" viewBox="0 0 24 24" fill="currentColor">
        <circle cx="12" cy="12" r="6" />
        <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 2a8 8 0 1 1 0 16 8 8 0 0 1 0-16z" />
      </svg>
    ),
    alwaysRender: true,
    onDismount() {
      clearInterval(settingsTimer);
      clearInterval(gameTimer);
      removeEventListener(EVENT, listener);
      unregisterShortcuts();
      routerHook.removeRoute(PLAYER_ROUTE);
    },
  };
});
