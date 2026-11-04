# Restructure step 2: public facade modules

This step keeps implementation files in place but adds the clean user-facing API
surface that should be documented and treated as stable:

- `lboxdlib.auth`
- `lboxdlib.fetch`
- `lboxdlib.parse`
- `lboxdlib.select`
- `lboxdlib.actions`
- `lboxdlib.workflows`
- `lboxdlib.models`

No major implementation files were moved in this step. Existing imports under
`lboxdlib.core`, `lboxdlib.modules`, and the step-1 wrapper packages continue to
work. Future restructuring can move internals behind these facades without
breaking ordinary users.
