---
name: local-events-adopt
description: Progressive feature adoption for the local-events plugin — checks what the household has actually adopted and offers exactly one next feature at the right moment. Use when the user asks "what else can events do", "set up more events stuff", or at natural moments (after a good recap, a long-weekend mention, a forwarded newsletter).
---

# Feature adoption ladder

Goal: the household uses the full system because each feature arrived when it was useful — never as a feature tour. **Offer at most ONE next step per conversation.** If they decline, note it and don't re-offer for a month.

## State check (run these tool calls quietly, no jargon in the reply)

1. Profile complete? `events_sources` (list) + settings: home set? kid ages? energy tolerances customized or still defaults?
2. Location import done? (ask, or check for `locations_imported_at` in kv via a refresh summary) — if never mentioned, this is usually step 1 after two weeks.
3. Calendar connected? (have any events ever been added? does Hermes have the Google Calendar skill?)
4. Ticketmaster key set? (settings `TICKETMASTER_API_KEY` empty?)
5. Email mining used? (any receipts/`went` labels with note containing "receipt"?)
6. Escapes tried? (any `energy=escape` feedback?)

## The ladder (in order — each builds on the last)

| Stage | Offer when | Say something like |
|---|---|---|
| 1. Digest + 🔥/👌/✖ | day one (onboard ends here) | — |
| 2. Calendar sync | after they say "looks good, let's do #1" twice | "want me to add picks straight to your calendar from now on? Attendance then confirms itself." |
| 3. Google Timeline import | after ~2 weeks of feedback, or when they say "too far" a lot | "if you export your Google Timeline I can calibrate 'too far' from your actual driving — Android: Settings → Location → Location services → Timeline → Export, then just say 'import my timeline' (I'll find the file in Downloads). Nothing raw is kept." — then follow the onboard skill §4 navigator steps (no Google API exists; the export click is theirs) |
| 4. Email mining | when they mention a newsletter, forward any event email, or buy tickets somewhere | (see find-events skill: receipt → automatic `went`) |
| 5. Ticketmaster key | when their picks skew music/sports and they hit "no big events" | "big concerts/sports live on Ticketmaster — free key, takes 2 minutes: developer.ticketmaster.com → paste it in settings." |
| 6. Escapes | before a 3-day weekend (blueprint fires), or "we should get out of town" | surface `energy=escape` picks with booking-lead flags; explain the dial's top end. |
| 7. Adults mode | when they mention date night / friends | "want a grown-ups line in Thursday digests, or ask me 'date night' anytime?" |

## Never do this

- Don't list features unprompted. One question, one feature, once.
- Don't push Timeline import — it's opt-in forever. If declined, the energy questions from onboarding carry the load.
- Don't re-offer a declined feature within a month; track informally in conversation memory.
- When they ask "what else CAN it do?" — answer with the 2–3 most relevant *unused* features for their actual behavior, not the full list.

## Privacy answers (when asked)

- All data lives locally in the plugin's own storage on their machine; the GitHub repo ships zero personal data.
- "Forget my location data" → run `events_forget_locations` and confirm what was removed (derived aggregates only; feedback history stays).
- Email is processed in-memory per message; only extracted event fields persist.
