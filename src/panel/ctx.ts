// The data every panel section receives.
import type { Options, RecentFile, Saved, Settings, Status } from "../api";

/** Everything a panel section may need. Built once in Content.tsx; sections take what they use. */
export interface Ctx {
  st: Status;
  cfg: Settings;
  opts: Options;
  recent: RecentFile[];
  busy: boolean;
  update: (patch: Partial<Settings>) => Promise<void>;
  guard: (fn: () => Promise<void>) => Promise<void>;
  refresh: () => Promise<void>;
  refreshRecent: () => Promise<void>;
  recording: boolean;
  buffering: boolean;
  saving: boolean;
  lowDisk: boolean;
  stateLine: string;
  last: Partial<Saved>;
  hasSd: boolean;
  onSd: boolean;
  setCfg: (c: Settings) => void;
}
