import { useEffect, useState } from "react";
import { sgNow } from "./day";

/** Singapore date and minute of day, refreshed every `every` ms. */
export function useClock(every = 15_000) {
  const [now, setNow] = useState(() => sgNow());
  useEffect(() => {
    const t = setInterval(() => setNow(sgNow()), every);
    return () => clearInterval(t);
  }, [every]);
  return now;
}

const DOW = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];
const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
/** "SAT 24 OCT" for an ISO date. */
export function dayLabel(iso: string): string {
  const d = new Date(iso + "T00:00:00Z");
  return `${DOW[d.getUTCDay()]} ${d.getUTCDate()} ${MON[d.getUTCMonth()]}`;
}
/** HH:MM in Singapore for an epoch-seconds timestamp. */
export function clockOf(t: number): string {
  const { minutes } = sgNow(new Date(t * 1000));
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}
export const dateOf = (t: number) => sgNow(new Date(t * 1000)).date;
