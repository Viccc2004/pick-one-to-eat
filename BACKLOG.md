# Backlog

Ideas that came up but are deliberately not in v1.
Adding to this file is free. Building from it is not.

- Conversation history: the page carries prior turns in the form and
  resends them, so the user can change one condition and recompute
  without retyping. Was in v1 scope until 2026-09-15; cut because it was
  never assigned to a milestone and would have landed on M4, already the
  riskiest one
- Multi-turn slot filling: the app asks follow-up questions when the
  input is missing location, time, or cuisine
- Reconsider address (or lat/lng) in the internal schema if the output ever needs to tell the user how to get there
- Add `reviews` / `editorialSummary` to the field mask so reasoning can
  cite dish-level evidence — costs the Enterprise + Atmosphere SKU
- Deployment: put it on a real URL instead of localhost
