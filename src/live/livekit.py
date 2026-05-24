"""LiveKit token helpers.

The gateway needs to mint participant tokens even in test/dev
environments where `livekit-api` is not installed. When the official
SDK is present we use it; otherwise we emit the equivalent JWT with
PyJWT, which is already a project dependency.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import jwt


@dataclass(frozen=True)
class LiveKitConfig:
    url: str
    public_url: str
    api_key: str
    api_secret: str
    token_ttl_s: int = 600

    @classmethod
    def from_env(cls) -> LiveKitConfig:
        internal_url = os.environ.get("NQAI_LIVEKIT_URL", "ws://localhost:7880")
        return cls(
            url=internal_url,
            public_url=os.environ.get("NQAI_LIVEKIT_PUBLIC_URL", internal_url),
            api_key=os.environ.get("NQAI_LIVEKIT_API_KEY", "devkey"),
            api_secret=os.environ.get("NQAI_LIVEKIT_API_SECRET", "secret"),
            token_ttl_s=int(os.environ.get("NQAI_LIVEKIT_TOKEN_TTL_S", "600")),
        )


class LiveKitTokenIssuer:
    def __init__(self, config: LiveKitConfig) -> None:
        self._config = config

    @property
    def public_url(self) -> str:
        return self._config.public_url

    def issue_join_token(
        self,
        *,
        room_name: str,
        identity: str,
        name: str | None = None,
        can_publish: bool = False,
        can_subscribe: bool = True,
        can_publish_data: bool = True,
    ) -> str:
        try:
            from livekit import api

            token = api.AccessToken(
                self._config.api_key, self._config.api_secret
            ).with_identity(identity).with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=room_name,
                    can_publish=can_publish,
                    can_subscribe=can_subscribe,
                    can_publish_data=can_publish_data,
                )
            )
            if name:
                token = token.with_name(name)
            return token.to_jwt()
        except ImportError:
            return self._issue_fallback_jwt(
                room_name=room_name,
                identity=identity,
                name=name,
                can_publish=can_publish,
                can_subscribe=can_subscribe,
                can_publish_data=can_publish_data,
            )

    def _issue_fallback_jwt(
        self,
        *,
        room_name: str,
        identity: str,
        name: str | None,
        can_publish: bool,
        can_subscribe: bool,
        can_publish_data: bool,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": self._config.api_key,
            "sub": identity,
            "nbf": now,
            "exp": now + self._config.token_ttl_s,
            "video": {
                "roomJoin": True,
                "room": room_name,
                "canPublish": can_publish,
                "canSubscribe": can_subscribe,
                "canPublishData": can_publish_data,
            },
        }
        if name:
            claims["name"] = name
        return jwt.encode(claims, self._config.api_secret, algorithm="HS256")
