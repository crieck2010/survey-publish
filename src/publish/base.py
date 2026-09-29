"""Shared adapter base class and error types.

Every platform adapter subclasses :class:`PlatformAdapter` and implements the
four abstract members. ``connect()`` is an OAuth installed-app flow shared via
:meth:`PlatformAdapter._installed_app_flow`: the adapter prints an
authorization URL, the user opens it in a browser and pastes back the code
(or the full redirect URL), the adapter exchanges it for tokens, and the
tokens are persisted through :class:`publish.store.TokenStore`.

Design rules enforced here:
  * ``publish()`` raises :class:`PublishError` with a human-actionable message
    on any failure -- never a raw traceback, KeyError, or leaked token.
  * A missing connection raises :class:`NotConnectedError` naming the exact
    ``survey-publish connect <platform>`` command and setup doc to follow.
  * Adapters accept an injectable ``session`` (a ``requests.Session``-like)
    so tests can stub HTTP without touching the network.
"""
from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from urllib.parse import parse_qs, urlparse

import requests

from .models import Credentials, PublishRequest, PublishResult
from .store import TokenStore


class PublishError(Exception):
    """Any publish/connect failure, with a human-actionable message."""


class NotConnectedError(PublishError):
    """Raised when an adapter has no usable stored credentials."""


def extract_code(pasted: str) -> str:
    """Pull an OAuth ``code`` out of pasted text.

    Accepts either the bare code or the full redirect URL the browser landed
    on (``https://localhost/?code=...&scope=...``). Returns ``""`` when no
    code can be found.
    """
    pasted = (pasted or "").strip()
    if not pasted:
        return ""
    if "code=" in pasted:
        try:
            query = parse_qs(urlparse(pasted).query)
            codes = query.get("code")
            if codes:
                return codes[0].strip()
        except Exception:
            pass
    # Bare code (or something we could not parse): hand it back as-is.
    if "://" not in pasted and " " not in pasted:
        return pasted
    return ""


def api_error_message(response, context: str) -> str:
    """Build a one-line, token-free error message from an HTTP response."""
    detail = ""
    try:
        data = response.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("code") or "")
        elif err is not None:
            detail = str(err)
        if not detail:
            # TikTok-style envelope: {"error": {"code": "...", "message": "..."}}
            detail = json.dumps(data)[:300]
    if not detail:
        text = getattr(response, "text", "") or ""
        detail = text[:300]
    status = getattr(response, "status_code", "?")
    msg = f"{context} failed (HTTP {status})."
    if detail.strip():
        msg += f" Provider said: {detail.strip()}"
    return msg


class PlatformAdapter(ABC):
    """Abstract base for all platform adapters."""

    #: Human label used in messages (defaults to ``name``).
    display_name: str = ""

    def __init__(self, store: "TokenStore | None" = None, session=None):
        self.store = store if store is not None else TokenStore()
        # ``session`` is a requests.Session-like; inject a fake in tests.
        self.session = session if session is not None else requests.Session()
        # Polling knobs for async platform processing (tests set these low).
        self.poll_interval_s = 10.0
        self.poll_timeout_s = 600.0

    # -- abstract surface -------------------------------------------------
    @property
    @abstractmethod
    def name(self) -> str:
        """Registry key, e.g. ``"youtube"``."""

    @abstractmethod
    def is_connected(self) -> bool:
        """True when stored credentials exist and validate against the API."""

    @abstractmethod
    def connect(self) -> None:
        """Run the OAuth installed-app flow and persist the resulting tokens."""

    @abstractmethod
    def publish(self, request: PublishRequest) -> PublishResult:
        """Upload ``request`` and return a successful :class:`PublishResult`.

        Raises :class:`PublishError` (or :class:`NotConnectedError`) on any
        failure -- never returns ``ok=False`` directly.
        """

    # -- concrete helpers -------------------------------------------------
    def disconnect(self) -> None:
        """Delete stored tokens for this platform."""
        removed = self.store.delete(self.name)
        label = self.display_name or self.name
        if removed:
            print(f"{label}: disconnected (stored tokens deleted).")
        else:
            print(f"{label}: was not connected; nothing to delete.")

    def account_label(self) -> str:
        """Short account identifier for ``survey-publish status`` ("" if none)."""
        creds = self.store.load(self.name)
        if creds is None:
            return ""
        return str(creds.extra.get("account_label") or "")

    def setup_doc(self) -> str:
        """Setup-doc filename for this platform, used in error messages."""
        return f"docs/SETUP_{self.name.upper()}.md"

    def _not_connected_message(self) -> str:
        label = self.display_name or self.name
        return (
            f"{label} is not connected. Run `survey-publish connect {self.name}` "
            f"and follow the steps in {self.setup_doc()}."
        )

    def _require_connected(self) -> Credentials:
        creds = self.store.load(self.name)
        if creds is None or not creds.access_token:
            raise NotConnectedError(self._not_connected_message())
        return creds

    def _require_video(self, request: PublishRequest) -> str:
        path = request.video_path
        if not path or not os.path.isfile(path):
            raise PublishError(
                f"Video file not found: {path!r}. "
                "Pass --video with the path to an existing MP4 file."
            )
        return path

    def _prompt_client_credentials(
        self,
        env_prefix: str,
        id_label: str = "client ID",
        secret_label: str = "client secret",
    ) -> "tuple[str, str]":
        """Read OAuth app credentials from env vars or interactive prompts."""
        client_id = os.environ.get(f"{env_prefix}_CLIENT_ID", "").strip()
        client_secret = os.environ.get(f"{env_prefix}_CLIENT_SECRET", "").strip()
        if not client_id:
            print(f"Enter your {id_label} (or set the {env_prefix}_CLIENT_ID env var):")
            client_id = input("> ").strip()
        if not client_secret:
            print(f"Enter your {secret_label} (or set the {env_prefix}_CLIENT_SECRET env var):")
            client_secret = input("> ").strip()
        if not client_id or not client_secret:
            raise PublishError(
                "A client ID and client secret are required to connect. "
                f"See {self.setup_doc()} for where to create them."
            )
        return client_id, client_secret

    def _installed_app_flow(self, authorize_url: str, exchange_code, instructions: str) -> dict:
        """Shared OAuth dance: print URL -> user pastes code/URL -> exchange.

        ``exchange_code`` is a callable ``(code) -> dict`` performing the token
        exchange and returning at least ``access_token``. The returned dict is
        never printed.
        """
        label = self.display_name or self.name
        print(f"\n=== Connect {label} ===")
        print("\n1. Open this URL in your browser and authorize the app:\n")
        print(f"   {authorize_url}\n")
        print(instructions)
        pasted = input("\n2. Paste the authorization code (or the full redirect URL)\n   here and press Enter:\n> ")
        code = extract_code(pasted)
        if not code:
            raise PublishError(
                "Could not find an authorization code in what you pasted. "
                "Run the connect command again and paste the code (or the full "
                "browser address-bar URL) carefully."
            )
        return exchange_code(code)

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class MetaOAuthMixin:
    """Facebook Login OAuth shared by the Facebook and Instagram adapters.

    Both adapters talk to Meta's Graph API, so they share the authorize /
    token-exchange / long-lived-token plumbing. ``meta_scopes()`` is the only
    per-adapter hook.

    Uncertainty note (documented, Sept 2026): Meta has been migrating
    Instagram API access toward a newer "Instagram Business Login" product
    with differently-named scopes (``instagram_business_basic``,
    ``instagram_business_content_publish``). The classic Facebook-Login scopes
    below are the long-documented path for the Instagram Graph API and are
    what this engine requests. If Meta rejects them for a new app, the setup
    docs name the alternative.
    """

    meta_graph_version = "25.0"
    #: Must be registered under the Meta app's "Valid OAuth Redirect URIs".
    #: The browser will fail to load it -- that is expected; the user pastes
    #: the address-bar URL (which carries ``code=...``) back into the CLI.
    meta_redirect_uri = "https://localhost/"

    def meta_scopes(self) -> str:
        raise NotImplementedError

    def build_meta_authorize_url(self, client_id: str, state: str = "survey-publish") -> str:
        from urllib.parse import urlencode

        params = {
            "client_id": client_id,
            "redirect_uri": self.meta_redirect_uri,
            "response_type": "code",
            "scope": self.meta_scopes(),
            "state": state,
        }
        return (
            f"https://www.facebook.com/v{self.meta_graph_version}/dialog/oauth?"
            + urlencode(params)
        )

    def exchange_meta_code(self, code: str, client_id: str, client_secret: str) -> dict:
        """Exchange the code for a short-lived user access token."""
        resp = self.session.get(
            f"https://graph.facebook.com/v{self.meta_graph_version}/oauth/access_token",
            params={
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": self.meta_redirect_uri,
                "code": code,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, "Meta token exchange"))
        data = resp.json()
        if "access_token" not in data:
            raise PublishError(
                "Meta token exchange returned no access token. "
                "The code may have expired -- run the connect flow again."
            )
        return data

    def exchange_for_long_lived(self, short_token: str, client_id: str, client_secret: str) -> dict:
        """Trade a short-lived user token for a ~60-day long-lived token."""
        resp = self.session.get(
            f"https://graph.facebook.com/v{self.meta_graph_version}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "fb_exchange_token": short_token,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, "Meta long-lived token exchange"))
        return resp.json()

    def meta_get(self, path: str, token: str, params: "dict | None" = None) -> dict:
        """GET a Graph API path; raise PublishError with the provider message."""
        merged = dict(params or {})
        merged["access_token"] = token
        resp = self.session.get(
            f"https://graph.facebook.com/v{self.meta_graph_version}/{path.lstrip('/')}",
            params=merged,
            timeout=30,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, f"Meta API {path}"))
        data = resp.json()
        if isinstance(data, dict) and "error" in data:
            raise PublishError(api_error_message(resp, f"Meta API {path}"))
        return data

    def meta_post(self, path: str, token: str, data: "dict | None" = None, files=None) -> dict:
        """POST a Graph API path; raise PublishError with the provider message."""
        payload = dict(data or {})
        payload["access_token"] = token
        resp = self.session.post(
            f"https://graph.facebook.com/v{self.meta_graph_version}/{path.lstrip('/')}",
            data=payload,
            files=files,
            timeout=120,
        )
        if resp.status_code != 200:
            raise PublishError(api_error_message(resp, f"Meta API {path}"))
        body = resp.json()
        if isinstance(body, dict) and "error" in body:
            raise PublishError(api_error_message(resp, f"Meta API {path}"))
        return body

    def pick_page(self, user_token: str) -> "tuple[str, str, str]":
        """Return ``(page_id, page_name, page_access_token)`` for the user's Page.

        Lists ``/me/accounts``; auto-selects when the user manages exactly one
        Page, otherwise prompts for a numbered choice.
        """
        data = self.meta_get("me/accounts", user_token, {"fields": "id,name,access_token"})
        pages = data.get("data", [])
        if not pages:
            raise PublishError(
                "No Facebook Pages found for this account. Publishing needs a Page "
                "you can perform the CREATE_CONTENT task on -- create one at "
                "facebook.com/pages/create, then run connect again. "
                "See the platform setup doc for details."
            )
        if len(pages) == 1:
            page = pages[0]
        else:
            print("\nMultiple Pages found -- which one should publishing use?")
            for i, page in enumerate(pages, 1):
                print(f"  {i}. {page.get('name')} (id {page.get('id')})")
            choice = input("Enter the number: ").strip()
            try:
                page = pages[int(choice) - 1]
            except (ValueError, IndexError):
                raise PublishError("Invalid choice. Run the connect command again.")
        return page["id"], page.get("name", page["id"]), page["access_token"]
