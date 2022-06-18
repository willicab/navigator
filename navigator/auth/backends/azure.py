"""AzureAuth.

Description: Backend Authentication/Authorization using Microsoft authentication.
"""
import logging
from aiohttp import web
from navigator.exceptions import (
    NavException,
    UserDoesntExists,
    InvalidAuth
)
from navigator.conf import (
    AZURE_ADFS_CLIENT_ID,
    AZURE_ADFS_CLIENT_SECRET,
    AZURE_ADFS_SCOPES,
    AZURE_ADFS_DOMAIN,
    AZURE_ADFS_TENANT_ID
)
import msal
import json
from msal.authority import (
    AuthorityBuilder,
    AZURE_PUBLIC
)
from .external import ExternalAuth
from typing import Dict

logging.getLogger("msal").setLevel(logging.INFO)

class AzureAuth(ExternalAuth):
    """AzureAuth.

    Authentication Backend for Microsoft Online Services.
    """
    user_attribute: str = "user"
    username_attribute: str = "username"
    userid_attribute: str = 'userPrincipalName'
    pwd_atrribute: str = "password"
    _service_name: str = "azure"
    _user_mapping: Dict = {
        'email': 'userPrincipalName',
        'given_name': 'givenName',
        'family_name': 'surname',
        'name': 'displayName'
    }

    def configure(self, app, router, handler):
        super(AzureAuth, self).configure(app, router, handler)

        # TODO: build the callback URL and append to routes
        self.base_url: str = f'https://login.microsoftonline.com/{AZURE_ADFS_TENANT_ID}'
        self.authorize_uri = f"https://login.microsoftonline.com/{AZURE_ADFS_TENANT_ID}/oauth2/v2.0/authorize"
        self.userinfo_uri = "https://graph.microsoft.com/v1.0/me"
        # issuer:
        self._issuer = f"https://login.microsoftonline.com/{AZURE_ADFS_TENANT_ID}"
        self._token_uri = f"https://login.microsoftonline.com/{AZURE_ADFS_TENANT_ID}/oauth2/v2.0/token"
        self.authority = AuthorityBuilder(AZURE_PUBLIC, "contoso.onmicrosoft.com")
        self.users_info = "https://graph.microsoft.com/v1.0/users"


    async def get_payload(self, request):
        ctype = request.content_type
        if request.method == "POST":
            if ctype in ("multipart/mixed", "multipart/form-data", "application/x-www-form-urlencoded"):
                data = await request.post()
                if len(data) > 0:
                    user = data.get(self.username_attribute, None)
                    password = data.get(self.pwd_atrribute, None)
                    return [user, password]
                else:
                    return [None, None]
            elif ctype == "application/json":
                try:
                    data = await request.json()
                    user = data[self.username_attribute]
                    password = data[self.pwd_atrribute]
                    return [user, password]
                except Exception:
                    return [None, None]
        else:
            return [None, None]

    
    async def get_cache(self, request: web.Request, state: str):
        cache = msal.SerializableTokenCache()
        result = None
        async with await request.app['redis'].acquire() as redis:
            result = await redis.get(f'azure_cache_{state}')
        if result:
            cache.deserialize(result)
        return cache
    
    async def save_cache(self, request: web.Request, state: str, cache: msal.SerializableTokenCache):
        if cache.has_state_changed:
            result = cache.serialize()
            async with await request.app['redis'].acquire() as redis:
                await redis.setex(f'azure_cache_{state}', result, timeout=120)
            
    def get_msal_app(self):
        authority = self._issuer if self._issuer else self.authority
        return msal.ConfidentialClientApplication(
            AZURE_ADFS_CLIENT_ID,
            authority=authority,
            client_credential = AZURE_ADFS_CLIENT_SECRET,
            validate_authority=True
            # token_cache=cache
        )
        
    def get_msal_client(self):
        authority = self._issuer if self._issuer else self.authority
        return msal.ClientApplication(
            AZURE_ADFS_CLIENT_ID,
            authority=authority,
            client_credential = AZURE_ADFS_CLIENT_SECRET,
            validate_authority=True
        )

    async def authenticate(self, request: web.Request):
        """ Authenticate, refresh or return the user credentials."""
        try:
            user, pwd = await self.get_payload(request)
        except Exception as err:
            logging.error(err)
        if user and pwd:
            Default_SCOPE = ["User.ReadBasic.All"]
            # will use User/Pass Authentication
            app = self.get_msal_client()
            # Firstly, check the cache to see if this end user has signed in before
            accounts = app.get_accounts(username=user)
            result = None
            if accounts:
                result = app.acquire_token_silent(Default_SCOPE, account=accounts[0])
            if not result:
                # we need to get a new token from AAD
                result = app.acquire_token_by_username_password(
                    user, pwd, Default_SCOPE
                )
                try:
                    if 'access_token' in result:
                        access_token = result['access_token']
                        data = await self.get(url=self.userinfo_uri, token=access_token, token_type='Bearer')
                        userdata, uid = self.build_user_info(data)
                        # also, user information:
                        data = await self.create_user(request, uid, userdata, access_token)
                        # Redirect User to HOME
                        return self.home_redirect(request, token=data["token"], token_type='Bearer')
                    else:
                        if 65001 in result.get('error_codes', []):
                            # AAD requires user consent for U/P flow
                            print(
                                "Visit this to consent:", app.get_authorization_request_url(Default_SCOPE)
                            )
                        else:
                            error = result.get('error')
                            desc = result.get('error_description')
                            correlation = result.get("correlation_id")
                            message = f"Azure {error}: {desc}, correlation id: {correlation}"
                            logging.exception(message)
                            raise web.HTTPForbidden(
                                reason=message
                            )
                except Exception as err:
                    raise web.HTTPForbidden(
                        reason=f"Azure: Invalid Response from Server {err}."
                    )
        else:
            domain_url = self.get_domain(request)
            self.redirect_uri = self.redirect_uri.format(domain=domain_url, service=self._service_name)
            SCOPE = ["https://graph.microsoft.com/.default"]
            app = self.get_msal_app()
            try:
                flow = app.initiate_auth_code_flow(
                    scopes=SCOPE,
                    redirect_uri=self.redirect_uri,
                    domain_hint=AZURE_ADFS_DOMAIN,
                    max_age=120
                )
                async with await request.app['redis'].acquire() as redis:
                    state = flow['state']
                    await redis.setex(f'azure_auth_{state}', json.dumps(flow), timeout=120)
                login_url = flow['auth_uri']
                return self.redirect(login_url)
            except Exception as err:
                raise NavException(
                    f"Azure: Client doesn't have info for Authentication: {err}"
                ) from err


    async def auth_callback(self, request: web.Request):
        try:
            try:
                redis = request.app['redis']
            except Exception as err:
                raise Exception(
                    f'Azure Auth: Cannot get a Redis Cache connection: {err}'
                ) from err
            auth_response = dict(request.rel_url.query.items())
            state = None
            # SCOPE = ["https://graph.microsoft.com/.default"]
            try:
                state = auth_response['state']
            except Exception as err:
                raise Exception(
                    'Azure: Wrong authentication Callback, missing State.'
                ) from err
            try:
                async with await request.app['redis'].acquire() as redis:
                    result = await redis.get(f'azure_auth_{state}')
                    flow = json.loads(result)
            except Exception as err:
                raise Exception(
                    f'Azure: Error reading Flow State from Cache: {err}'
                )  from err
            app = self.get_msal_app()
            try:
                result = app.acquire_token_by_auth_code_flow(
                    auth_code_flow=flow,
                    auth_response=auth_response
                )
                if 'token_type' in result:
                    token_type = result['token_type']
                    # expires_in = result['expires_in']
                    access_token = result['access_token']
                    # refresh_token = result['refresh_token']
                    id_token = result['id_token']
                    claims = result['id_token_claims']
                    # getting user information:
                    try:
                        data = await self.get(url=self.userinfo_uri, token=access_token, token_type=token_type)
                        print('USER DATA: ', data)
                        # build user information:
                        userdata, uid = self.build_user_info(data)
                        print(userdata, uid)
                        #userdata['access_token'] = access_token
                        userdata['id_token'] = id_token
                        #userdata['refresh_token'] = refresh_token
                        userdata['claims'] = claims
                        data = await self.create_user(request, uid, userdata, access_token)
                    except Exception as err:
                        logging.exception('Azure: Error getting User information')
                        raise web.HTTPForbidden(
                            reason=f"Azure: Error with User Information: {err}"
                        )
                    # Redirect User to HOME
                    return self.home_redirect(request, token=data['token'], token_type=token_type)
                elif 'error' in result:
                    error = result['error']
                    desc = result['error_description']
                    message = f"Azure {error}: {desc}"
                    logging.exception(message)
                    raise web.HTTPForbidden(
                        reason=message
                    )
                else:
                    raise web.HTTPForbidden(
                        reason="Azure: Invalid Response from Server."
                    )
            except Exception as err:
                logging.exception(err)
                return self.redirect(uri=self.login_failed_uri)
        except Exception as err:
            logging.exception(err)
            return self.redirect(uri=self.login_failed_uri)

    async def logout(self, request):
        pass

    async def finish_logout(self, request):
        pass

    async def check_credentials(self, request):
        """ Authentication and create a session."""
        return True