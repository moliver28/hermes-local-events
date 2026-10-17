---
name: local-events-recap
description: Sunday-evening recap — savoring + feedback in two taps. Use Sunday evening/night, or when the user says "recap", "how did we do", or mentions a weekend activity that came from a digest pick.
---

# Sunday recap (savoring + learning)

1. Run `events_digest`'s sibling: check the last digest picks (`events_feedback` refs) and the household calendar for what actually happened.
2. Open warm and brief: "Quick weekend recap — 2 taps per thing, then I get smarter:"
3. For each attended event: "Worth it? 🔥 👌 ✖ — and was it the right amount of effort (right / too much / too little)?"
4. Convert each reply to `events_feedback`:
   - 🔥 → `signal=went, effort_match=right_effort`
   - 👌 → `signal=went` (no effort note)
   - ✖ → `signal=dismissed` (+ `effort_match=too_much` if they say it drained them)
   - Too far / too pricey → `signal=too_far|too_pricey`
5. Close with one line of savoring, grounded in real data: "3 new parks this month", "two free adventures". No fabricated streaks.
6. If nothing happened: "Open weekend — rest counts. Want a low-effort idea for next Saturday?" (one suggestion max).
7. Never send recap nudges for events the user didn't engage with. Once per week, max.
