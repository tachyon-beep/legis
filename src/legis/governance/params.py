"""Reviewed governance constants (ADR-0002).

These are POLICY — changing them is an ADR amendment, not a workflow-file or env
tweak an agent can use to slip a gate. The override-rate endpoint reads them
from here, never from request parameters, so the threshold an agent is measured
against cannot be tuned by the agent being measured.
"""

OVERRIDE_RATE_THRESHOLD = 0.2   # max share of kept suppressions forced past the judge
OVERRIDE_RATE_WINDOW = 100      # rolling window of final-disposition records
OVERRIDE_RATE_MIN_SAMPLE = 20   # below this, pass-with-notice (small-corpus floor)
