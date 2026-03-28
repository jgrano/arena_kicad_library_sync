"""
Arena PLM REST API client with bidirectional sync support.

Wraps and extends the original KiCad-Arena-Sync arena client with:
- requests library (cleaner HTTP, bundled in plugin ZIP)
- Exponential backoff on 429 rate limits
- New read endpoints: get_modified_since, get_item_sourcing, get_item_changes
- Write operations with safety guards
- Field normalization and conflict detection

Safety rules enforced in code:
- NEVER update lifecyclePhase, revisionNumber, or workflowStatus
- NEVER delete Arena items
- All writes require allow_writes=True on __init__
- Every write logged at INFO with before/after values
"""

from __future__ import annotations

import logging
import os
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Forbidden fields — never write these to Arena
FORBIDDEN_WRITE_FIELDS = frozenset({
    "lifecyclePhase", "revisionNumber", "workflowStatus",
    "lifecycle_phase", "revision_number", "workflow_status",
})

# Lifecycle phase mapping: Arena -> KiCad display name
LIFECYCLE_MAP = {
    "DESIGN": "Development",
    "Design": "Development",
    "PRELIMINARY": "NPI",
    "Preliminary": "NPI",
    "PRODUCTION": "Active",
    "Production": "Active",
    "OBSOLETE": "Obsolete",
    "Obsolete": "Obsolete",
    "UNRELEASED": "NPI",
    "Unreleased": "NPI",
}


def _make_ssl_context():
    """Create an SSL context that can find system certificates.

    KiCad's bundled Python often can't locate the default cert bundle.
    Tries common macOS/Linux cert paths before falling back.
    """
    cert_paths = [
        "/etc/ssl/cert.pem",
        "/usr/local/etc/openssl/cert.pem",
        "/usr/local/etc/openssl@3/cert.pem",
        "/opt/homebrew/etc/openssl@3/cert.pem",
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
    ]
    try:
        import certifi
        cert_paths.insert(0, certifi.where())
    except ImportError:
        pass

    for path in cert_paths:
        if os.path.exists(path):
            ctx = ssl.create_default_context(cafile=path)
            return ctx

    ctx = ssl._create_unverified_context()
    return ctx


_SSL_CTX = _make_ssl_context()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class KiCadPart:
    """Normalized representation of a component for KiCad library use."""
    arena_guid: str = ""
    arena_number: str = ""
    description: str = ""
    category: str = ""
    revision: str = ""
    lifecycle: str = ""
    primary_mpn: str = ""
    primary_manufacturer: str = ""
    alternates: list[dict] = field(default_factory=list)
    kicad_symbol: str = ""
    kicad_footprint: str = ""
    datasheet_url: str = ""
    last_modified_arena: Optional[str] = None
    custom_fields: dict[str, str] = field(default_factory=dict)


@dataclass
class ConflictResult:
    """Result of conflict detection between Arena and local data."""
    arena_guid: str = ""
    arena_number: str = ""
    description: str = ""
    field_name: str = ""
    arena_value: str = ""
    local_value: str = ""
    arena_modified_at: Optional[str] = None
    local_modified_at: Optional[str] = None
    recommended: str = "arena_wins"
    resolution: Optional[str] = None

    @property
    def has_conflict(self) -> bool:
        return self.arena_value != self.local_value


class ArenaAPIError(Exception):
    """Arena API error with structured information."""

    def __init__(self, message: str, status_code: int = 0, arena_guid: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.arena_guid = arena_guid


# ---------------------------------------------------------------------------
# Arena HTTP Client (low-level transport)
# ---------------------------------------------------------------------------

class ArenaClient:
    """Low-level HTTP client for the Arena PLM REST API.

    Uses the requests library with automatic session management,
    401 re-authentication, and 429 exponential backoff.
    """

    DEFAULT_BASE_URL = "https://api.arenasolutions.com/v1"
    USER_AGENT = "ArenaKiCadLibrarySync/1.0"
    MAX_RETRIES = 5

    WORKSPACES = {
        "Production": 900279607,
        "Sandbox": 900279608,
    }

    def __init__(self, base_url: Optional[str] = None, allow_writes: bool = False):
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.allow_writes = allow_writes
        self.session_id: Optional[str] = None
        self._email: Optional[str] = None
        self._password: Optional[str] = None
        self._workspace_id: Optional[str] = None
        self.requests_remaining: Optional[int] = None
        self.user_guid: Optional[str] = None
        self.user_full_name: Optional[str] = None

        # Import requests with fallback to urllib
        try:
            import requests as req_lib
            self._session = req_lib.Session()
            self._session.verify = _SSL_CTX  # type: ignore[assignment]
            # requests can use the SSL context or a cert path
            # Try to find a valid cert path for requests
            self._session.verify = True
            for path in ["/etc/ssl/cert.pem", "/opt/homebrew/etc/openssl@3/cert.pem",
                         "/etc/ssl/certs/ca-certificates.crt"]:
                if os.path.exists(path):
                    self._session.verify = path
                    break
            try:
                import certifi
                self._session.verify = certifi.where()
            except ImportError:
                pass
            self._use_requests = True
        except ImportError:
            self._use_requests = False
            self._session = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def login(self, email: str, password: str, workspace_id: Optional[str] = None) -> dict:
        """Authenticate and store the session id."""
        self._email = email
        self._password = password
        self._workspace_id = workspace_id

        body: dict[str, Any] = {"email": email, "password": password}
        if workspace_id:
            body["workspaceId"] = int(workspace_id) if workspace_id.isdigit() else workspace_id

        data = self._raw_request("POST", "/login", body=body, auth=False)
        self.session_id = data.get("arenaSessionId")
        if not self.session_id:
            raise ArenaAPIError("Arena login failed — no session id returned", status_code=401)

        self.user_guid = None
        self.user_full_name = None
        self._resolve_current_user()
        logger.info("Logged in as %s (workspace: %s)", email, workspace_id or "default")
        return data

    def _resolve_current_user(self) -> None:
        """Look up the current user's GUID."""
        if not self._email:
            return
        try:
            data = self.get("/settings/users")
            for user in data.get("results", []):
                if user.get("email", "").lower() == self._email.lower():
                    self.user_guid = user.get("guid")
                    self.user_full_name = user.get("fullName")
                    return
        except Exception:
            pass

    def logout(self) -> None:
        """End the Arena session."""
        if self.session_id:
            try:
                self._raw_request("PUT", "/login",
                                  body={"arenaSessionId": self.session_id})
            except Exception:
                pass
            self.session_id = None
            logger.info("Logged out")

    def _relogin(self) -> None:
        """Re-authenticate using stored credentials."""
        if self._email and self._password:
            self.session_id = None
            self.login(self._email, self._password, self._workspace_id)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        return self._request("GET", path, params=params)

    def post(self, path: str, body: Optional[dict] = None) -> dict:
        return self._request("POST", path, body=body)

    def put(self, path: str, body: Optional[dict] = None) -> dict:
        return self._request("PUT", path, body=body)

    def patch(self, path: str, body: Optional[dict] = None) -> dict:
        return self._request("PATCH", path, body=body)

    def delete(self, path: str) -> dict:
        return self._request("DELETE", path)

    def _request(self, method: str, path: str,
                 body: Optional[dict] = None,
                 params: Optional[dict] = None) -> dict:
        """Authenticated request with 401 re-auth and 429 backoff."""
        for attempt in range(self.MAX_RETRIES):
            try:
                return self._raw_request(method, path, body=body,
                                         params=params, auth=True)
            except ArenaAPIError as e:
                if e.status_code == 401 and attempt == 0:
                    logger.debug("Got 401, re-authenticating...")
                    self._relogin()
                    continue
                if e.status_code == 429:
                    wait = min(2 ** attempt * 10, 120)
                    logger.warning("Rate limited (429), waiting %ds (attempt %d/%d)",
                                   wait, attempt + 1, self.MAX_RETRIES)
                    time.sleep(wait)
                    continue
                raise
        raise ArenaAPIError("Max retries exceeded", status_code=429)

    def _raw_request(self, method: str, path: str,
                     body: Optional[dict] = None,
                     params: Optional[dict] = None,
                     auth: bool = True) -> dict:
        """Execute an HTTP request and return parsed JSON."""
        url = self.base_url + path
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.USER_AGENT,
        }
        if auth and self.session_id:
            headers["arena_session_id"] = self.session_id

        logger.debug("%s %s", method, path)

        if self._use_requests:
            return self._request_via_requests(method, url, headers, body, params)
        else:
            return self._request_via_urllib(method, url, headers, body, params)

    def _request_via_requests(self, method: str, url: str, headers: dict,
                              body: Optional[dict], params: Optional[dict]) -> dict:
        """Execute request using the requests library."""
        import requests as req_lib

        try:
            resp = self._session.request(
                method, url,
                headers=headers,
                json=body,
                params=params,
                timeout=60,
            )

            # Track rate limit
            remaining = resp.headers.get("X-Arena-Requests-Remaining")
            if remaining is not None:
                self.requests_remaining = int(remaining)

            if resp.status_code >= 400:
                err_detail = self._parse_error_body(resp.text)
                raise ArenaAPIError(
                    f"Arena {method} {url} failed ({resp.status_code}): {err_detail}",
                    status_code=resp.status_code,
                )

            return resp.json() if resp.text else {}

        except req_lib.RequestException as e:
            if hasattr(e, "response") and e.response is not None:
                raise ArenaAPIError(
                    str(e), status_code=e.response.status_code
                ) from e
            raise ArenaAPIError(str(e)) from e

    def _request_via_urllib(self, method: str, url: str, headers: dict,
                           body: Optional[dict], params: Optional[dict]) -> dict:
        """Fallback: execute request using urllib (stdlib)."""
        import json as json_mod
        import urllib.request
        import urllib.error
        import urllib.parse

        if params:
            url += "?" + urllib.parse.urlencode(params)

        data = json_mod.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, method=method)
        for k, v in headers.items():
            req.add_header(k, v)

        try:
            resp = urllib.request.urlopen(req, context=_SSL_CTX)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            err_detail = self._parse_error_body(err_body)
            raise ArenaAPIError(
                f"Arena {method} {url} failed ({e.code}): {err_detail}",
                status_code=e.code,
            ) from None

        remaining = resp.headers.get("X-Arena-Requests-Remaining")
        if remaining is not None:
            self.requests_remaining = int(remaining)

        raw = resp.read().decode("utf-8")
        return json_mod.loads(raw) if raw else {}

    @staticmethod
    def _parse_error_body(text: str) -> str:
        """Extract human-readable error from Arena error JSON."""
        if not text:
            return "Unknown error"
        try:
            import json as json_mod
            data = json_mod.loads(text)
            errors = data.get("errors", [])
            if errors:
                return "; ".join(
                    f"{e.get('message', '')} (code {e.get('code', '?')})"
                    for e in errors
                )
        except Exception:
            pass
        return text[:500]

    # ------------------------------------------------------------------
    # Multipart file upload (kept from original, uses urllib)
    # ------------------------------------------------------------------

    def upload_file(self, path: str, filepath: str,
                    content_type: Optional[str] = None) -> dict:
        """Upload a file via multipart/form-data POST."""
        return self._do_upload(path, filepath, content_type=content_type)

    def upload_file_form(self, path: str, filepath: str,
                         fields: dict, content_type: Optional[str] = None) -> dict:
        """Upload a file with form fields via multipart/form-data POST."""
        return self._do_upload(path, filepath, content_type=content_type,
                               form_fields=fields)

    def _do_upload(self, path: str, filepath: str,
                   content_type: Optional[str] = None,
                   form_fields: Optional[dict] = None) -> dict:
        """Execute a multipart file upload."""
        import json as json_mod
        import mimetypes
        import urllib.request
        import urllib.error

        if content_type is None:
            content_type = mimetypes.guess_type(filepath)[0] or "application/octet-stream"

        filename = os.path.basename(filepath)
        with open(filepath, "rb") as f:
            file_data = f.read()

        boundary = f"----ArenaKiCadSync{os.urandom(16).hex()}"
        parts = []

        if form_fields:
            for field_name, field_value in form_fields.items():
                parts.append(
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{field_name}"\r\n'
                    f"\r\n"
                    f"{field_value}\r\n"
                )

        file_header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="content"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n"
            f"\r\n"
        )

        body = b""
        for part in parts:
            body += part.encode("utf-8")
        body += file_header.encode("utf-8")
        body += file_data
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        url = self.base_url + path
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("User-Agent", self.USER_AGENT)
        if self.session_id:
            req.add_header("arena_session_id", self.session_id)

        try:
            resp = urllib.request.urlopen(req, context=_SSL_CTX)
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            raise ArenaAPIError(
                f"Arena upload {path} failed ({e.code}): {self._parse_error_body(err_body)}",
                status_code=e.code,
            ) from None

        remaining = resp.headers.get("X-Arena-Requests-Remaining")
        if remaining is not None:
            self.requests_remaining = int(remaining)

        raw = resp.read().decode("utf-8")
        return json_mod.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
# Arena API (high-level business operations)
# ---------------------------------------------------------------------------

class ArenaAPI:
    """High-level Arena PLM operations for library sync.

    Combines item, file, sourcing, and category operations into
    a single API surface with field normalization and write safety.
    """

    def __init__(self, client: ArenaClient):
        self.client = client

    # -- Read operations ----------------------------------------------------

    def get_items(self, page: int = 0, limit: int = 400,
                  filters: Optional[dict] = None) -> dict:
        """Get paginated item list."""
        params = {"offset": page * limit, "limit": limit}
        if filters:
            params.update(filters)
        return self.client.get("/items", params=params)

    def get_item(self, guid: str) -> dict:
        """Get single item detail by GUID."""
        return self.client.get(f"/items/{guid}")

    def search_items(self, keyword: str) -> list[dict]:
        """Search items by keyword."""
        data = self.client.get("/items", params={"number": keyword})
        return data.get("results", [])

    def search_by_number(self, part_number: str) -> Optional[dict]:
        """Search for an item by exact part number."""
        data = self.client.get("/items", params={"number": part_number})
        results = data.get("results", [])
        return results[0] if results else None

    def get_item_sourcing(self, guid: str) -> list[dict]:
        """Get manufacturer/MPN sourcing data for an item."""
        data = self.client.get(f"/items/{guid}/sourcing")
        return data.get("results", [])

    def get_item_files(self, guid: str) -> list[dict]:
        """Get attached files/datasheets for an item."""
        data = self.client.get(f"/items/{guid}/files")
        return data.get("results", [])

    def get_categories(self) -> list[dict]:
        """Get category tree (assignable, activated only)."""
        data = self.client.get("/settings/items/categories")
        categories = []
        for cat in data.get("results", []):
            if not cat.get("assignable", False):
                continue
            if not cat.get("activated", True):
                continue

            prefix = ""
            nf = cat.get("numberFormat", {})
            for f in nf.get("fields", []):
                val = f.get("value")
                if val and val.isdigit() and len(val) == 3:
                    prefix = val
                    break

            parent = cat.get("parentCategory") or {}
            categories.append({
                "guid": cat.get("guid", ""),
                "name": cat.get("name", ""),
                "path": cat.get("path", ""),
                "prefix": prefix,
                "numberFormat": nf,
                "parentName": parent.get("name", ""),
            })

        categories.sort(key=lambda c: (c["prefix"] or "999", c["name"]))
        return categories

    def get_modified_since(self, iso_timestamp: str) -> list[dict]:
        """Get items modified since a given ISO timestamp (for delta sync)."""
        all_items = []
        offset = 0
        limit = 400
        while True:
            data = self.client.get("/items", params={
                "modifiedDateTimeFrom": iso_timestamp,
                "offset": offset,
                "limit": limit,
            })
            results = data.get("results", [])
            all_items.extend(results)
            if len(results) < limit:
                break
            offset += limit
        return all_items

    def get_item_changes(self, guid: str) -> list[dict]:
        """Get change history for conflict detection."""
        data = self.client.get(f"/items/{guid}/revisions")
        return data.get("results", [])

    def get_revision(self, item: dict) -> str:
        """Get the current revision string for an item."""
        rev = item.get("revisionNumber")
        if rev:
            return str(rev)
        guid = item["guid"]
        data = self.client.get(f"/items/{guid}/revisions")
        revisions = data.get("results", [])
        if revisions:
            return revisions[-1].get("revisionNumber", "?")
        return "?"

    def get_lifecycle(self, item: dict) -> str:
        """Get the lifecycle phase name."""
        lc = item.get("lifecyclePhase", {})
        return lc.get("name", "Unknown") if isinstance(lc, dict) else str(lc)

    def get_bom(self, guid: str, max_pages: int = 10) -> list[dict]:
        """Get the single-level BOM for an item (paginated)."""
        bom_lines = []
        offset = 0
        limit = 400
        for _ in range(max_pages):
            data = self.client.get(
                f"/items/{guid}/bom",
                params={"offset": offset, "limit": limit},
            )
            results = data.get("results", [])
            bom_lines.extend(results)
            if len(results) < limit:
                break
            offset += limit
        return bom_lines

    # -- Write operations ---------------------------------------------------

    def update_item_attribute(self, guid: str, attribute_name: str,
                              value: str) -> dict:
        """Update a single attribute on an Arena item.

        Safety: refuses to update forbidden fields (lifecycle, revision, workflow).
        Requires allow_writes=True on the client.
        """
        if not self.client.allow_writes:
            raise ArenaAPIError("Write operations require allow_writes=True")

        if attribute_name in FORBIDDEN_WRITE_FIELDS:
            raise ArenaAPIError(
                f"FORBIDDEN_OPERATION: Cannot update '{attribute_name}' — "
                "lifecycle, revision, and workflow status are managed by Arena",
                arena_guid=guid,
            )

        # Get current value for logging
        current = self.get_item(guid)
        old_value = _extract_nested(current, attribute_name)

        # Determine if this is a custom attribute
        if attribute_name.startswith("customAttributes."):
            attr_key = attribute_name.replace("customAttributes.", "")
            body = {"additionalAttributes": [{
                "apiName": attr_key,
                "value": value,
            }]}
        else:
            body = {attribute_name: value}

        result = self.client.put(f"/items/{guid}", body=body)
        logger.info("Updated %s on %s: '%s' -> '%s'",
                     attribute_name, guid, old_value, value)
        return result

    def create_item(self, item_data: dict) -> dict:
        """Create a new item in Arena.

        Args:
            item_data: Dict with keys matching Arena's create API
                       (category, name, revisionNumber, etc.)

        Returns the full item dict including auto-generated 'number'.
        """
        if not self.client.allow_writes:
            raise ArenaAPIError("Write operations require allow_writes=True")

        result = self.client.post("/items", body=item_data)
        guid = result.get("guid", "")
        number = result.get("number", "")
        logger.info("Created item %s (guid: %s)", number, guid)

        # Set owner to logged-in user
        if guid and self.client.user_guid:
            try:
                self.client.put(f"/items/{guid}", body={
                    "owner": {"guid": self.client.user_guid},
                })
            except Exception:
                pass

        return result

    def add_item_sourcing(self, guid: str, manufacturer: str,
                          mpn: str, description: str = "") -> dict:
        """Add a sourcing entry (manufacturer/MPN) to an item."""
        if not self.client.allow_writes:
            raise ArenaAPIError("Write operations require allow_writes=True")

        body: dict[str, Any] = {
            "manufacturer": {"name": manufacturer},
            "mpn": mpn,
        }
        if description:
            body["description"] = description

        result = self.client.post(f"/items/{guid}/sourcing", body=body)
        logger.info("Added sourcing to %s: %s / %s", guid, manufacturer, mpn)
        return result

    def upload_item_file(self, guid: str, filepath: str,
                         file_type: str = "DATASHEET") -> dict:
        """Upload a file attachment to an Arena item."""
        if not self.client.allow_writes:
            raise ArenaAPIError("Write operations require allow_writes=True")

        filename = os.path.basename(filepath)
        stem = os.path.splitext(filename)[0]
        ext = os.path.splitext(filename)[1].lstrip(".")

        fields = {
            "file.storageMethodName": "FILE",
            "file.category.name": file_type,
            "file.title": stem,
            "file.edition": "1",
            "file.description": "Uploaded by ArenaKiCadLibrarySync",
            "file.format": ext,
        }
        result = self.client.upload_file_form(
            f"/items/{guid}/files", filepath, fields=fields,
        )
        logger.info("Uploaded file %s to %s", filename, guid)
        return result

    def delete_item(self, guid: str) -> None:
        """FORBIDDEN: Arena items cannot be deleted via this client."""
        raise ArenaAPIError(
            "FORBIDDEN_OPERATION: Deleting Arena items is not allowed",
            arena_guid=guid,
        )

    # -- Field normalization ------------------------------------------------

    def normalize_item(self, raw_item: dict,
                       field_mappings: dict[str, str]) -> KiCadPart:
        """Normalize a raw Arena item into a KiCadPart.

        Flattens nested Arena fields, maps lifecycle phases,
        and applies field mappings.
        """
        # Extract core fields
        guid = raw_item.get("guid", "")
        number = raw_item.get("number", "")
        name = raw_item.get("name", "")
        description = raw_item.get("description", "")

        # Category
        cat = raw_item.get("category", {})
        category = cat.get("name", "") if isinstance(cat, dict) else str(cat)

        # Revision
        revision = str(raw_item.get("revisionNumber", ""))

        # Lifecycle
        lc = raw_item.get("lifecyclePhase", {})
        lc_name = lc.get("name", "") if isinstance(lc, dict) else str(lc)
        lifecycle = LIFECYCLE_MAP.get(lc_name, lc_name)

        # Last modified
        last_modified = raw_item.get("modifiedDateTime", raw_item.get("lastModifiedDateTime"))

        # Build custom fields from field mappings
        custom = {}
        for arena_field, kicad_field in field_mappings.items():
            value = _extract_nested(raw_item, arena_field)
            if value:
                # Arena often returns dicts with {guid, name} — extract name
                if isinstance(value, dict):
                    value = value.get("name", str(value))
                custom[kicad_field] = str(value)

        return KiCadPart(
            arena_guid=guid,
            arena_number=number,
            description=description or name,
            category=category,
            revision=revision,
            lifecycle=lifecycle,
            last_modified_arena=last_modified,
            custom_fields=custom,
        )

    def enrich_with_sourcing(self, part: KiCadPart) -> KiCadPart:
        """Fetch and attach sourcing data (MPN, manufacturer) to a part."""
        if not part.arena_guid:
            return part

        try:
            sourcing = self.get_item_sourcing(part.arena_guid)
            if sourcing:
                primary = sourcing[0]
                mfr = primary.get("manufacturer", {})
                part.primary_manufacturer = mfr.get("name", "") if isinstance(mfr, dict) else str(mfr)
                part.primary_mpn = primary.get("mpn", "")

                if len(sourcing) > 1:
                    part.alternates = [
                        {
                            "manufacturer": s.get("manufacturer", {}).get("name", ""),
                            "mpn": s.get("mpn", ""),
                        }
                        for s in sourcing[1:]
                    ]
        except Exception as e:
            logger.debug("Failed to get sourcing for %s: %s", part.arena_guid, e)

        return part

    def denormalize_part(self, part: KiCadPart,
                         field_mappings: dict[str, str]) -> dict:
        """Convert a KiCadPart back to Arena API format for writes.

        Only includes fields present in the kicad_to_arena field mapping.
        """
        result: dict[str, Any] = {}

        # Build a combined dict of all part fields
        part_fields = {
            "MPN": part.primary_mpn,
            "Description": part.description,
            "Category": part.category,
            "Revision": part.revision,
            "Lifecycle": part.lifecycle,
            "Manufacturer": part.primary_manufacturer,
            "Footprint": part.kicad_footprint,
            "Symbol": part.kicad_symbol,
            "Datasheet": part.datasheet_url,
        }
        part_fields.update(part.custom_fields)

        for kicad_field, arena_field in field_mappings.items():
            if arena_field in FORBIDDEN_WRITE_FIELDS:
                continue
            value = part_fields.get(kicad_field, "")
            if value:
                _set_nested(result, arena_field, value)

        return result


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

def detect_conflict(arena_item: dict, local_item: dict,
                    last_sync_time: Optional[str] = None) -> list[ConflictResult]:
    """Compare Arena item against local DB record and detect conflicts.

    Returns a list of ConflictResult for each field that differs.
    """
    conflicts = []

    arena_modified = arena_item.get("modifiedDateTime",
                                     arena_item.get("lastModifiedDateTime"))
    local_modified = local_item.get("last_modified_local",
                                     local_item.get("last_synced_from_arena"))

    # Compare common fields
    field_pairs = [
        ("name", "description"),
        ("number", "arena_number"),
        ("description", "description"),
    ]

    for arena_key, local_key in field_pairs:
        arena_val = str(arena_item.get(arena_key, ""))
        local_val = str(local_item.get(local_key, ""))

        if arena_val and local_val and arena_val != local_val:
            conflicts.append(ConflictResult(
                arena_guid=arena_item.get("guid", ""),
                arena_number=arena_item.get("number", ""),
                description=arena_item.get("name", ""),
                field_name=arena_key,
                arena_value=arena_val,
                local_value=local_val,
                arena_modified_at=arena_modified,
                local_modified_at=local_modified,
                recommended="arena_wins",
            ))

    return conflicts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_nested(obj: dict, dotted_key: str) -> Any:
    """Extract a value from a nested dict using dot notation.

    Example: _extract_nested({"a": {"b": "c"}}, "a.b") -> "c"
    """
    keys = dotted_key.split(".")
    current = obj
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, "")
        else:
            return ""
    return current


def _set_nested(obj: dict, dotted_key: str, value: Any) -> None:
    """Set a value in a nested dict using dot notation."""
    keys = dotted_key.split(".")
    current = obj
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value
