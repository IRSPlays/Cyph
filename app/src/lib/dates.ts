/** Date maths on ISO strings (YYYY-MM-DD), done in UTC so the machine's time zone never shifts a day. */
export const addDays = (iso: string, n: number): string => {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
/** Monday = 0 ... Sunday = 6 */
export const dow = (iso: string): number => (new Date(iso + "T00:00:00Z").getUTCDay() + 6) % 7;
export const mondayOf = (iso: string): string => addDays(iso, -dow(iso));
export const monthStart = (iso: string): string => iso.slice(0, 8) + "01";
export const addMonths = (iso: string, n: number): string => {
  const d = new Date(iso + "T00:00:00Z");
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() + n);
  return d.toISOString().slice(0, 10);
};
export const dayNum = (iso: string): number => Number(iso.slice(8, 10));

export const DOW_SHORT = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
export const DOW_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
export const monthName = (iso: string): string => MONTHS[Number(iso.slice(5, 7)) - 1];

/** ISO week number (Monday-based), for the "WEEK 43" label. */
export function isoWeek(iso: string): number {
  const d = new Date(iso + "T00:00:00Z");
  const day = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - day + 3);
  const first = new Date(Date.UTC(d.getUTCFullYear(), 0, 4));
  return 1 + Math.round(((d.getTime() - first.getTime()) / 86400000 - 3 + ((first.getUTCDay() + 6) % 7)) / 7);
}

export const toMin = (t: string): number => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
export const fmtMin = (m: number): string => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
