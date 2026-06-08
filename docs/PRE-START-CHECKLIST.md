# Pre-Start Checklist — non-code action items

These are real-world tasks, not build tasks. They came up alongside The Delphic Ledger
design but live outside the repo, which is exactly how they get lost. None is legal advice;
each is a "do this / ask this" reminder. Workiva start date: **July 6, 2026.**

## Before July 6 (while not yet subject to the trading policy)

- [ ] **Liquidate or finalize individual-stock positions you intend to exit.** After the
      start date, selling individual securities requires preclearance (NoTrade@workiva.com),
      and buying customer securities is prohibited outright. ETF/fund trades stay
      exception-carved and need no preclearance — but the individual names are the time-
      sensitive ones. Use the tool's `unwind` queue (lot-level gain/loss, ST/LT, days-to-LT)
      to sequence sales for tax, not panic. Selling appreciated taxable lots realizes gains —
      run the math first.
- [ ] **Bundle your pre-start questions for the CLO / NoTrade office into one email:**
  - [ ] Confirm the exact scope of permitted instruments (broad-market ETFs, sector ETFs,
        bond funds, target-date funds) and that Mandy's accounts follow the same rules.
  - [ ] Confirm there's no holding-period/frequency rule on fund trades.
  - [ ] The atypically high variable-comp ratio in the offer — get the written clarification
        you flagged.

## Before the repo goes public

- [ ] **Outside-activity / IP-assignment check.** A public GitHub repo is low-risk here
      (personal, non-commercial, finance-*adjacent* but not competing with Workiva's product),
      but confirm whether your employment agreement requires disclosure of outside projects.
      Two-minute check; cheaper than a surprise.
- [ ] **Keep it code-only and impersonal in public.** The Insider Trading Policy bars sharing
      information about other companies' securities on websites without authorization. A code
      repo with synthetic example data is fine; what would *not* be fine is publishing live
      market calls, real holdings, or persona "verdicts" on real tickers via GitHub Pages /
      Actions / a hosted instance. The design enforces this (local-only, synthetic examples,
      no publishing CI) — just don't override it later.

## If you ever revisit the parked ideas

- [ ] **Public ad-supported persona site** — would require the CLO website-clause
      authorization conversation *before* launch, and must stay impersonal (publisher's
      exemption). Parked.
- [ ] **Photo-to-preset web tool** — no securities/advice/Workiva entanglement at all; the
      cleanest of the side-project ideas whenever you want it. Parked, not blocked.

---
*This checklist is a personal reminder, not legal advice. The CLO's office is the authority
on all Workiva policy questions.*
