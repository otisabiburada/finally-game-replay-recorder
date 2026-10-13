// Section: name and size of the last saved file.
import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { openPlayer } from "../player";
import { fmtBytes } from "../util";
import type { Ctx } from "./ctx";

export function LastSavedSection({ last }: Ctx) {
  return (
    <>
      {last.name && (
        <PanelSection title="Last saved">
          <PanelSectionRow>
            <div style={{ fontSize: 12, wordBreak: "break-all" }}>{last.name} · {fmtBytes(last.size ?? 0)}</div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => void openPlayer(last.name ?? "")}>Watch</ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      )}
    </>
  );
}
