# lboxdlib restructure step 1 import map

This step adds stable public namespaces without physically moving the current
implementation files yet. Existing `lboxdlib.core.*` and `lboxdlib.modules.*`
imports continue to work.

Prefer these new import paths in new code:

| Responsibility | New namespace | Current implementation |
| --- | --- | --- |
| Domain dataclasses | `lboxdlib.models` | `lboxdlib.core.typedefs` |
| HTML parsers | `lboxdlib.parsers.*` | `lboxdlib.modules.*_from_page` |
| Page/data fetchers | `lboxdlib.fetchers.*` | `lboxdlib.core.fetchers`, `lboxdlib.modules.activity_page_handling` |
| Authenticated write actions | `lboxdlib.actions.*` | `lboxdlib.modules.like_state`, `user_follow`, `watch`, etc. |
| Read-only selectors | `lboxdlib.selectors.*` | `lboxdlib.modules.rated_viewing_selector`, `filter_sort`, etc. |
| Multi-step workflows | `lboxdlib.workflows.*` | `lboxdlib.modules.batch_like_reviews`, `reciprocal_follow_cleanup`, etc. |
| CLI entrypoints | `lboxdlib.cli.*` | reserved for later step |
| Temporary migration shims | `lboxdlib.compat.*` | reserved for later step |

Do not delete or move the implementation modules until wrapper imports and
fixture tests are passing in the target development environment.

## Step 2 facade preference

Step 2 adds an even smaller user-facing facade surface. For normal code, prefer:

| Task | Preferred facade |
| --- | --- |
| Sessions/auth | `lboxdlib.auth` |
| Fetching | `lboxdlib.fetch` |
| Parsing | `lboxdlib.parse` |
| Selection/discovery | `lboxdlib.select` |
| Authenticated actions | `lboxdlib.actions` |
| Multi-step workflows | `lboxdlib.workflows` |
| Result/dataclass types | `lboxdlib.models` |

The step-1 subpackages such as `lboxdlib.parsers.*` and `lboxdlib.fetchers.*` remain useful as internal/advanced namespaces, but the facades are the clean public API.

