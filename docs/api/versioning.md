# API versioning and lifecycle

Status: in force from 2026-05-28. See [ADR-9](../decisions/2026-05-28-public-api-spec.md).

## Version surface

- **URL prefix:** `/v1/`. All public routes live under this prefix. There is no `/v0/`.
- **Package version:** canonical in [pyproject.toml](../../pyproject.toml). The FastAPI app should read it via `importlib.metadata.version("neurovoice")`. A hardcoded `VERSION` constant elsewhere is a drift bug; report it.
- **OpenAPI `info.version`:** mirrors the package version, exposed at `/openapi.json`.

## v0.x — pre-stability

We are in v0.x. The contract is:

- **Anything may change.** Endpoints, request shapes, response shapes, status codes, header names, auth conventions, error codes.
- **Best-effort notice.** Breaking changes appear in the changelog. Material changes get at least 14 days' notice on the public docs site.
- **Pre-stability acknowledgement.** SDK and API consumers using v0.x are operating under a pre-stability contract. Production use at this stage is at the integrator's risk.

There is no SLA on backwards compatibility during v0.x. There is also no obligation to use Sunset headers for v0.x removals, though we will when the change is large enough to matter.

## v1.0 and after — semver

Once we ship v1.0, the API follows semantic versioning at the endpoint level:

| Change kind | Version bump | Sunset cycle |
| --- | --- | --- |
| New endpoint | minor | — |
| New optional request field | minor | — |
| New response field | minor | — |
| New error code | minor | — |
| New required request field | **major** | yes |
| Removed endpoint | **major** | yes |
| Removed response field | **major** | yes |
| Removed or renamed error code | **major** | yes |
| Changed semantics (same shape, different behavior) | **major** | yes |

## Sunset cycle (major changes after v1.0)

When a route is being removed or breaking-changed:

1. **Day 0** — the route ships with [RFC 8594](https://www.rfc-editor.org/rfc/rfc8594) `Sunset` and `Deprecation` headers, plus `Link: rel="successor-version"` pointing to the replacement. The Sunset date is at least **90 days** out.
2. **Day 0** — entry in `docs/api/changelog.md` (file added when the first changelog entry ships) referencing the successor.
3. **Day 0–60** — best-effort outreach to known integrators via the email on their API key registration.
4. **Sunset date+** — removal lands in a major version. The old route returns `410 Gone` with a body pointing to the successor.

Precedent: the existing `POST /v1/tts` and `POST /v1/tts/stream` sync routes stamp Sunset/Deprecation headers ([src/server/main.py:232-244](../../src/server/main.py#L232-L244)); the async `POST /v1/tts/jobs` is the successor.

## Parity routes

Vendor-parity routes ([vendor-parity.md](vendor-parity.md)) inherit the same lifecycle, with one addition:

- A vendor breaking-change upstream can force a non-elective sunset. In that case the Sunset window starts at the date we detect the vendor change.
- If the vendor's own deprecation window is shorter than 90 days, our window may also be shorter than 90 days. We will not stretch a Sunset window beyond the vendor's, because the route is unreachable in practice once the vendor flips.

## Pre-release identifiers

- Internal builds: `0.x.y.dev<n>` (PEP 440).
- Release candidates: `1.0.0rc<n>` once we approach v1.0.
- No git SHA in public version strings. The public `info.version` is always a PEP 440 release identifier.

## Reporting drift

If `pyproject.toml` and `info.version` in the live `/openapi.json` ever disagree, the build is broken. Open an issue or fix in-place; do not ship a release until they match.
