"""GUI-side code for the TradingLab app.

Exists to keep `app.py` tractable: wider-surface components (dialogs, event
subsystems) live here as separate modules. Everything imported from this
package must avoid importing `tradinglab.app` at module load time to
prevent circular imports — use `TYPE_CHECKING` for type annotations.
"""
