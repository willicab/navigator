""" Abstract handler for Authorization Middleware."""

import asyncio
from aiohttp import web
from aiohttp_session import get_session
from abc import ABC, abstractmethod


class BaseAuthzHandler(ABC):

    @abstractmethod
    async def check_authorization(self, request: web.Request) -> bool:
        pass
