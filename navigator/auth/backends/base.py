from typing import Iterable
from abc import ABC, ABCMeta, abstractmethod
from aiohttp import web, hdrs

class BaseAuthHandler(ABC):
    """Abstract Base for Authentication."""
    credentials_required: bool = False
    user_property: str = 'user'
    _scheme: str = 'Bearer'
    _authz_backends: tuple = ()

    def __init__(self, credentials_required: bool = False, authorization_backends: tuple = (), **kwargs):
        self.credentials_required = credentials_required
        self.user_property = kwargs['user_property']
        self._scheme = kwargs['scheme']
        # configuration Authorization Backends:
        self._authz_backends = self.get_authorization_backends(
            authorization_backends
        )

    def get_authorization_backends(self, backends: Iterable) -> tuple:
        for backend in backends:
            # TODO: more automagic logic
            if backend == 'hosts':
                yield authz_hosts()

    def configure(self):
        pass

    async def authorization_backends(self, app, handler, request):
        if request.method == hdrs.METH_OPTIONS:
            return await handler(request)
        # logic for authorization backends
        for backend in self._authz_backends:
            logging.debug(f'Running Authorization backend {backend!s}')
            if await backend.check_authorization(request):
                return await handler(request)
        return None

    @abstractmethod
    async def check_credentials(self, request):
        pass

    async def auth_middleware(self, app, handler):
        async def middleware(request):
            request.user = None
            return await handler(request)
        return middleware
