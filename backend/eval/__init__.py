"""Honest evaluation of the Spot the Spin core on the frozen eval set.

``run.py`` runs ``app.core.classifier.classify`` over ``data/eval/<version>/`` and writes the
results JSON read by the /metrics page. Metric code lives in ``metrics.py``,
``calibration.py``, ``wilson.py`` and ``kappa.py``; it is pure and has no I/O.
"""
