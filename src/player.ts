// What the player page should show, and the function the panel buttons call to open it.
import { Navigation, Router } from "@decky/ui";
import { preparePlayback } from "./api";
import { toast } from "./util";

export const PLAYER_ROUTE = "/finally-game-recorder/player";

export const playerState = { name: "", url: "", note: "", format: "mp4" };

export async function openPlayer(name: string): Promise<void> {
  toast("Preparing clip...", name, false, 2000);
  try {
    const r = await preparePlayback(name, "mp4");
    if (!r.ok || !r.url) {
      toast("Cannot play clip", r.error ?? "Unknown error", true, 6000);
      return;
    }
    playerState.name = name;
    playerState.url = r.url;
    playerState.note = r.note ?? "";
    playerState.format = r.format ?? "mp4";
    Router.CloseSideMenus();
    Navigation.Navigate(PLAYER_ROUTE);
  } catch (e: any) {
    toast("Cannot play clip", String(e?.message ?? e), true, 6000);
  }
}
