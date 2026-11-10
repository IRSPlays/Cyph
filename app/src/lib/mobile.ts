import { useSyncExternalStore } from "react";

/** The phone layout is for a phone-sized window (or an Android/iPhone webview); everything else gets the desktop pages.
 *  An iPad is not a phone: it gets the desktop layout with bigger touch targets and the Pencil pad. */
const query = () => matchMedia("(max-width: 700px)");
const phoneUA = /Android|iPhone/i.test(navigator.userAgent);
export const useIsMobile = (): boolean =>
  useSyncExternalStore(
    (l) => {
      const q = query();
      q.addEventListener("change", l);
      return () => q.removeEventListener("change", l);
    },
    () => phoneUA || query().matches,
  );

/** iPadOS 13+ Safari says "Macintosh", so a touch screen on a "Mac" is an iPad. `?ipad` forces it (for testing). */
export const isIPad =
  /iPad/i.test(navigator.userAgent) ||
  (/Macintosh/i.test(navigator.userAgent) && navigator.maxTouchPoints > 1) ||
  /[?&]ipad\b/.test(location.search + location.hash);
if (isIPad) document.documentElement.classList.add("ipad");
