// Hand-written stand-ins so `tsc --noEmit` can check src/index.tsx WITHOUT node_modules.
// @decky/api signatures: copied from the published @decky/api 1.1.2 index.d.ts / types.d.ts (verified).
// @decky/ui and react: written from memory of their public APIs (NOT verified) - deliberately strict on the
// props this plugin uses, so a typo or wrong value type is caught. Real `pnpm run build` stays the final judge.
declare module "react" {
  export type ReactNode = any;
  export function useState<T>(init: T | (() => T)): [T, (v: T | ((p: T) => T)) => void];
  export function useEffect(fn: () => void | (() => void), deps?: readonly unknown[]): void;
  export function useRef<T>(init: T): { current: T };
  export function useCallback<T extends (...a: any[]) => any>(fn: T, deps: readonly unknown[]): T;
}
declare namespace JSX {
  interface Element {}
  interface ElementChildrenAttribute { children: {} }
  interface IntrinsicAttributes { key?: string | number }
  interface IntrinsicElements { [tag: string]: { [prop: string]: any; style?: Record<string, string | number | undefined>; children?: any } }
}
interface Window { SP_REACT: { createElement: any; Fragment: any } }

declare module "@decky/api" {
  import type { ReactNode } from "react";
  export const call: <Args extends any[] = [], Return = void>(route: string, ...args: Args) => Promise<Return>;
  export const callable: <Args extends any[] = [], Return = void>(route: string) => (...args: Args) => Promise<Return>;
  export const addEventListener: <Args extends any[] = []>(event: string, listener: (...args: Args) => any) => (...args: Args) => any;
  export const removeEventListener: <Args extends any[] = []>(event: string, listener: (...args: Args) => any) => void;
  export interface ToastData { title: ReactNode; body: ReactNode; subtext?: ReactNode; logo?: ReactNode; icon?: ReactNode;
    timestamp?: Date; onClick?: () => void; className?: string; contentClassName?: string; duration?: number;
    expiration?: number; critical?: boolean; eType?: number; sound?: number; showNewIndicator?: boolean;
    playSound?: boolean; showToast?: boolean }
  export const routerHook: { addRoute(path: string, component: () => JSX.Element): void; removeRoute(path: string): void };
  export const toaster: { toast(toast: ToastData): { data: ToastData; dismiss: () => void } };
  export interface Plugin { name: string; titleView?: JSX.Element; content: JSX.Element; icon: JSX.Element;
    onDismount?(): void; alwaysRender?: boolean }
  export function definePlugin(fn: () => Plugin): Plugin;
}

declare module "@decky/ui" {
  import type { ReactNode } from "react";
  type Opt<T> = { data: T; label: ReactNode };
  export const staticClasses: { Title: string };
  export const Router: { CloseSideMenus(): void; [k: string]: any };
  export function PanelSection(p: { title?: string; children?: ReactNode }): JSX.Element;
  export function PanelSectionRow(p: { children?: ReactNode }): JSX.Element;
  export function TextField(p: { label?: ReactNode; value?: string; onChange?: (e: any) => void }): JSX.Element;
  export function Focusable(p: { style?: Record<string, string | number | undefined>; className?: string; onButtonDown?: (e: any) => void; children?: ReactNode }): JSX.Element;
  export function DialogButton(p: { onClick?: () => void; disabled?: boolean; style?: Record<string, string | number | undefined>; children?: ReactNode }): JSX.Element;
  export const Navigation: { Navigate(path: string): void; NavigateBack(): void };
  export const GamepadButton: Record<string, number>;
  export function ButtonItem(p: { layout?: "below" | "inline"; label?: ReactNode; description?: ReactNode;
    disabled?: boolean; onClick?: () => void | Promise<void>; children?: ReactNode }): JSX.Element;
  export function ToggleField(p: { label: ReactNode; description?: ReactNode; checked: boolean; disabled?: boolean;
    onChange?: (v: boolean) => void }): JSX.Element;
  export function SliderField(p: { label: ReactNode; description?: ReactNode; value: number; min?: number; max?: number;
    step?: number; showValue?: boolean; valueSuffix?: string; disabled?: boolean; onChange?: (v: number) => void }): JSX.Element;
  export function DropdownItem<T = any>(p: { label?: ReactNode; description?: ReactNode; rgOptions: Opt<T>[];
    selectedOption: T | undefined; disabled?: boolean; onChange?: (o: Opt<T>) => void }): JSX.Element;
}
