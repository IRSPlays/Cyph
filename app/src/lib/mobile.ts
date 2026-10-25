import { useSyncExternalStore } from "react";

/** The phone layout is for a phone-sized window (or any Android webview); everything else gets the desktop pages. */
const query = () => matchMedia("(max-width: 700px)");
const isAndroid = /Android|iPhone|iPad/i.test(navigator.userAgent);
export const useIsMobile = (): boolean =>
  useSyncExternalStore(
    (l) => {
      const q = query();
      q.addEventListener("change", l);
      return () => q.removeEventListener("change", l);
    },
    () => isAndroid || query().matches,
  );
