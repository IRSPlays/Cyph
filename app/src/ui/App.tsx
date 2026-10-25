import { useCallback, useEffect, useState } from "react";
import { startChat, stopChat } from "../lib/chatStore";
import { ipc } from "../lib/ipc";
import { useRoute } from "../lib/route";
import { onUnpaired, startSync, stopSync } from "../lib/sync";
import { useTalkKey } from "../lib/voiceStore";
import { Arm } from "../pages/Arm";
import { Calendar } from "../pages/Calendar";
import { Home } from "../pages/Home";
import { Memory } from "../pages/Memory";
import { Settings } from "../pages/Settings";
import { useIsMobile } from "../lib/mobile";
import { Mobile } from "./Mobile";
import { Pair } from "./Pair";

export default function App() {
  const [paired, setPaired] = useState<boolean | null>(null);
  const route = useRoute();
  const mobile = useIsMobile();
  useTalkKey();
  const refresh = useCallback(() => {
    void ipc.getSettings().then((s) => setPaired(s.paired));
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);
  useEffect(() => {
    if (!paired) return;
    const drop = () => setPaired(false);
    onUnpaired.add(drop);
    void startSync();
    void startChat();
    return () => {
      onUnpaired.delete(drop);
      void stopSync();
      void stopChat();
    };
  }, [paired]);

  // the companion window opens pages here ("OPEN HOME", "PLAN")
  useEffect(() => {
    let off: (() => void) | undefined;
    void ipc.onNavigate((r) => (location.hash = "/" + r)).then((f) => (off = f));
    return () => off?.();
  }, []);

  if (paired === null) return null;
  if (!paired) return <Pair onPaired={() => setPaired(true)} />;
  if (mobile) return <Mobile onUnpaired={() => setPaired(false)} />;
  switch (route) {
    case "calendar":
      return <Calendar />;
    case "memory":
      return <Memory />;
    case "arm":
      return <Arm />;
    case "settings":
      return <Settings />;
    default:
      return <Home />;
  }
}
