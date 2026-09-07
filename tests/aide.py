"""Petits outils partagés par les tests.

Les routes sont appelées directement, sans `TestClient`, pour ne pas déclencher le
`lifespan` de l'application — donc pas de Chromium à démarrer pour vérifier trois
chaînes de caractères. Certaines réclament maintenant une `Request` : la voici, réduite
au strict nécessaire.
"""

from __future__ import annotations

from starlette.requests import Request


def requete(entetes: dict[str, str] | None = None, ip: str = "203.0.113.7") -> Request:
    return Request({
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "root_path": "",
        "server": ("test", 80),
        "client": (ip, 45678),
        "headers": [(nom.lower().encode(), valeur.encode())
                    for nom, valeur in (entetes or {}).items()],
    })
