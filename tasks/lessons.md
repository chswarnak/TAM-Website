# Lessons

## 2026-11-23: plan review corrections from the user
- Defaults that point at live resources (Rekognition collection, buckets) must
  stay exactly as they are in production, and a test should pin them. Never
  derive them from another setting that might change.
- Never auto-create cloud resources unless their name was configured explicitly,
  and log loudly when it happens.
- Rate-limit defaults must account for a shared venue IP (many users behind one
  address). Make the thresholds configurable.
- "Notify immediately" for admins means batching so they don't get one email per
  registration. Offer bulk actions (approve selected/all) along with one-click links.
- When removing a login path (name login), spell out the exact migration steps
  for existing users in the summary.
