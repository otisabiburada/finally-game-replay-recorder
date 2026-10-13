// Section: recent recordings with two-tap delete.
import { ButtonItem, Focusable, PanelSection, PanelSectionRow } from "@decky/ui";
import { useState } from "react";
import { deleteRecording } from "../api";
import { openPlayer } from "../player";
import { fmtBytes, toast } from "../util";
import type { Ctx } from "./ctx";

export function RecentSection({ recent, refreshRecent }: Ctx) {
  const [armed, setArmed] = useState("");
  return (
    <>
      <PanelSection title="Recent recordings">
        {recent.length === 0 && <PanelSectionRow><div style={{ fontSize: 12 }}>Nothing here yet.</div></PanelSectionRow>}
        {recent.map((f) => (
          <PanelSectionRow key={f.name}>
            <Focusable style={{ display: "flex", flexDirection: "column", width: "100%" }}>
              <div style={{ fontSize: 12, wordBreak: "break-all" }}>{f.name}</div>
              <ButtonItem layout="below" onClick={() => void openPlayer(f.name)}>Watch</ButtonItem>
              <ButtonItem
                layout="below"
                description={fmtBytes(f.size)}
                onClick={async () => {
                  if (armed !== f.name) {
                    setArmed(f.name);
                    setTimeout(() => setArmed((a) => (a === f.name ? "" : a)), 4000);
                    return;
                  }
                  setArmed("");
                  const r = await deleteRecording(f.name);
                  if (!r.ok) toast("Delete failed", r.error ?? "", true);
                  void refreshRecent();
                }}
              >
                {armed === f.name ? "Press again to DELETE" : "Delete"}
              </ButtonItem>
            </Focusable>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void refreshRecent()}>Refresh list</ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}
