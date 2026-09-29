"""Hand-rolled HTTP fakes for survey-publish tests.

No real network, no real credentials. ``FakeSession`` is a
``requests.Session``-like: tests register scripted routes
``(method, url_prefix) -> FakeResponse`` (or a callable taking the request
kwargs and returning a FakeResponse), then inject the session into an
adapter. Every call is recorded in ``calls`` for request-shape assertions.
"""
from __future__ import annotations

import json

import requests


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.headers = dict(headers or {})
        if text:
            self.text = text
        elif json_data is not None:
            self.text = json.dumps(json_data)
        else:
            self.text = ""

    def json(self):
        if self._json_data is None:
            raise ValueError("no JSON body")
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self):
        self.calls = []
        self._routes = []

    def add(self, method, url_prefix, response):
        """Register a route. ``response`` may be a FakeResponse or callable."""
        self._routes.append((method.upper(), url_prefix, response))
        return self

    def _dispatch(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        for route_method, prefix, response in self._routes:
            if route_method == method and url.startswith(prefix):
                if callable(response):
                    return response(kwargs)
                return response
        raise AssertionError(f"Unexpected {method} {url} (no fake route)")

    def get(self, url, **kwargs):
        return self._dispatch("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._dispatch("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._dispatch("PUT", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._dispatch("DELETE", url, **kwargs)

    def last_call(self, method=None):
        for call in reversed(self.calls):
            if method is None or call["method"] == method:
                return call
        raise AssertionError("no matching call recorded")


def error_response(status_code=400, message="bad request"):
    return FakeResponse(status_code, {"error": {"message": message, "code": status_code}})
