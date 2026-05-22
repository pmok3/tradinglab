"""Sandbox scanner: continuous block-tree scans over the preloaded universe.

See ``scanner/README.md`` (TBD) and the per-module ``*.spec.md`` siblings
for design rationale. Public submodules:

- :mod:`tradinglab.scanner.model` — pure dataclasses + JSON round-trip
  for ``ScanDefinition`` and its constituent parts. No app/Tk coupling.
- :mod:`tradinglab.scanner.fields` — curated field registry projecting
  over :data:`tradinglab.indicators.base.INDICATORS`. Single source of
  truth for which fields are scannable.
- :mod:`tradinglab.scanner.engine` — pure tri-valued evaluator.
- :mod:`tradinglab.scanner.runner` — ThreadPoolExecutor live-tick driver.
- :mod:`tradinglab.scanner.storage` — per-scan JSON files under
  ``<cache_dir>/scans/`` with a UUID-keyed index.
"""
