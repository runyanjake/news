/** Milliseconds from `now` until the next local wall-clock "HH:MM" (process TZ). */
export function msUntilNext(now, hhmm) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(hhmm);
  if (!match) throw new Error(`Invalid time "${hhmm}", expected HH:MM`);
  const next = new Date(now);
  next.setHours(Number(match[1]), Number(match[2]), 0, 0);
  if (next <= now) next.setDate(next.getDate() + 1);
  return next - now;
}
