// Small helpers: running game name, time/size formatting, toast.
import { toaster } from "@decky/api";
import { GamepadButton, Router } from "@decky/ui";

export function currentGame(): string {
  try {
    return String((Router as any).MainRunningApp?.display_name ?? "");
  } catch {
    return "";
  }
}

export function fmtTime(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(r).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function fmtBytes(n: number): string {
  if (!n || n < 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${i === 0 ? v.toFixed(0) : v.toFixed(1)} ${units[i]}`;
}

export function toast(title: string, body: string, critical = false, duration = 3500): void {
  try {
    toaster.toast({ title, body, duration, critical });
  } catch {
    /* toaster not ready */
  }
}

/** Button number from Decky's own GamepadButton enum, looked up by name (same approach as Deck Notes). */
export function buttonCode(...patterns: RegExp[]): number | undefined {
  const table = (GamepadButton ?? {}) as Record<string, unknown>;
  for (const re of patterns) {
    for (const [name, value] of Object.entries(table)) {
      if (typeof value === "number" && re.test(name)) return value;
    }
  }
  return undefined;
}
