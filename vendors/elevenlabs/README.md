# ElevenLabs vendor spec

NeuroVoice ships ElevenLabs-compatible parity routes (see [docs/api/vendor-parity.md](../../docs/api/vendor-parity.md)). This directory pins the ElevenLabs OpenAPI specification used as the contract for those routes.

## Files

- `openapi.yaml` — pinned copy of the ElevenLabs OpenAPI spec at the version listed below. **Not yet present**; first pin is a follow-up to ADR-9.
- `README.md` — this file.

## Pinned version

Not yet pinned. The initial pin will be added in the follow-up that lands `openapi.yaml`.

| Pin date | Upstream URL / commit | Vendor `info.version` | Notes |
| --- | --- | --- | --- |
| _(none yet)_ | — | — | — |

## How to update

1. Download the current ElevenLabs OpenAPI spec from their published developer documentation site. (Manual step — outbound HTTP is not enabled for automation.)
2. Save as `openapi.yaml`. YAML form is preferred for diff readability; JSON is acceptable if YAML is not published.
3. Add a row to the pin table above with the date (ISO 8601), the upstream URL, the vendor's `info.version` field, and any notable observations about the diff vs. the previous pin.
4. Run the contract tests locally:
   ```
   pytest tests/contract/test_elevenlabs_parity.py
   ```
5. Resolve any new failures before opening the refresh PR:
   - **Vendor added a field we now ignore?** Decide whether to support it (minor bump) or document it as out-of-scope.
   - **Vendor changed a field's shape?** Major bump for the affected route; follow the sunset cycle in [docs/api/versioning.md](../../docs/api/versioning.md).
   - **Vendor removed a route we support?** Sunset our route in lockstep.

## Refresh cadence

Quarterly. Out-of-cycle refresh is acceptable when:

- ElevenLabs publishes a non-breaking addition we want to support.
- A critical compatibility issue is reported by an integrator.
- A security advisory affects a field shape we depend on.

## Scope reminder

Contract tests cover only the routes listed under "ElevenLabs parity surface" in [docs/api/vendor-parity.md](../../docs/api/vendor-parity.md). Routes the vendor publishes but we do not support are ignored.
