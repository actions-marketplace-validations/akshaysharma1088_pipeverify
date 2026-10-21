# Pilot: pipeverify on PipeMend fixes (2026-10-21)

Data: every fix in the PipeMend benchmark (DOI 10.5281/zenodo.23510784) that ran without error:
173 agent fixes (Haiku, Sonnet, Opus; with and without change notes) and 36 rule-based playbook
fixes. 209 fixes, 25 harmful (wrong data, or a fix applied where the right action was escalation).

| Check | Harmful caught | False alarms on correct fixes |
|---|---|---|
| Production monitors (PipeMend gate) | 16 / 25 | 0 / 184 |
| pipeverify, diff against last good snapshot | 5 / 25 | 4 / 184 |
| pipeverify, diff + source conflict | 13 / 25 | 21 / 184 |
| pipeverify, diff + source conflict + mismatch | 13 / 25 | 47 / 184 |
| Monitors + pipeverify (diff + conflict) | 25 / 25 | 21 / 184 |

Agent fixes only: monitors caught 0 of 5 harmful fixes; pipeverify with the source conflict
check caught 5 of 5.

Notes and limits:
- The last good snapshot is the pipeline run on delivery batches 1 to 11, before any incident batch.
- All 21 conflict false alarms are on incidents where the source really carried conflicting values
  (corrections, H2 and F2c) or late events (F3b); a human review is defensible there.
- The mismatch check adds false alarms on correct fixes that transform values (currency strings,
  cents) and catches nothing extra, so it is opt-in.
- Small sample (the three harmful no-notes agent fixes share one case), synthetic data, and the
  source conflict check was designed after seeing the incident families. A held-out benchmark is
  needed before drawing conclusions.

Reproduce: `python docs/pilot_pipemend_eval.py <path to pipemend repo>`
