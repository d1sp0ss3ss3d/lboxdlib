# Internal lists package migration

`ListManager`, `FilmHydrator`, and `hydrate_lids` are now implemented in a real
`lboxdlib.lists` package instead of being re-exported from the old catch-all
`lboxdlib.modules` namespace.

Public imports remain unchanged:

```python
from lboxdlib import FilmHydrator, ListManager, hydrate_lids
from lboxdlib.lists import FilmHydrator, ListManager, hydrate_lids
```

Implementation locations:

```text
src/lboxdlib/lists/manager.py
src/lboxdlib/lists/hydration.py
```

Compatibility shims remain at:

```text
src/lboxdlib/modules/list_manager.py
src/lboxdlib/modules/film_hydrator.py
```

Those shims are transitional and should not be used by new code.
