from typing import Any, List, Dict, Optional, Callable
from aiohttp.web import middleware
from aiohttp import web
from navigator.middlewares import check_path
import ujson as json

#TODO: Middleware Class to avoid repeat check_path($0)

@middleware
async def django_session(request, handler):
    id = None
    if not check_path(request.path):
        return await handler(request)
    try:
        id = request.headers.get('sessionid', None)
    except Exception as e:
        print(e)
        id = request.headers.get('X-Sessionid', None)
    if id is not None:
        session = None
        try:
            # first: clear session
            session = request.app['session']
            await session.logout() # clear existing session
            if not await session.decode(key=id):
                message = {
                    'code': 403,
                    'message': 'Invalid Session',
                    'reason': str(err)
                }
                return web.json_response({'error': message})
        except Exception as err:
            print('Error Decoding Session: {}, {}'.format(err, err.__class__))
            return await handler(request)
        try:
            request['user_id'] = session['user_id']
            request['session'] = session
        except Exception as err:
            #TODO: response to an auth error
            message = {
                'code': 403,
                'message': 'Invalid Session or Authentication Error',
                'reason': str(err)
            }
            return web.json_response({'error': message})
        finally:
            return await handler(request)
    else:
        # TODO: authorization
        return await handler(request)
