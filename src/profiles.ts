// One-tap quality profiles: each is a bundle of settings. "Custom" is shown when the settings match none of them.
import type { Settings } from "./api";

export interface Profile { label: string; patch: Partial<Settings> }

export const PROFILES: Record<string, Profile> = {
  light: { label: "Light: 30 fps, 960x600, 5 Mbps (least impact on the game)", patch: { fps: 30, resolution: "960x600", video_kbps: 5000 } },
  balanced: { label: "Balanced: 30 fps, full size, 8 Mbps", patch: { fps: 30, resolution: "native", video_kbps: 8000 } },
  high: { label: "High: 60 fps, full size, 12 Mbps", patch: { fps: 0, resolution: "native", video_kbps: 12000 } },
};

export function currentProfile(cfg: Settings): string {
  for (const [key, p] of Object.entries(PROFILES)) {
    const patch = p.patch as Record<string, unknown>;
    if (Object.keys(patch).every((k) => (cfg as unknown as Record<string, unknown>)[k] === patch[k])) return key;
  }
  return "custom";
}
