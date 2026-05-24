"""Admin surface — operator login + tenant + API key + usage CRUD.

JWT-protected (HttpOnly cookie). Server-side rendered with Jinja2 +
HTMX; no SPA, no bundler. See docs/architecture/auth-multi-tenant.md §2.
"""

from .router import admin_router

__all__ = ["admin_router"]
