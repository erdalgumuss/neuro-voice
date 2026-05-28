"""Language pack namespace.

Each subdirectory ``<iso>/`` exports a ``pack: LanguagePack`` singleton.
:mod:`frontend` resolves the right pack by ISO code at call time;
there is no central registry to keep in sync — the directory IS the
registry.
"""
