# -*- coding: utf-8 -*-
#
# Copyright 2018 - Swiss Data Science Center (SDSC)
# A partnership between École Polytechnique Fédérale de Lausanne (EPFL) and
# Eidgenössische Technische Hochschule Zürich (ETHZ).
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Service creating named servers for given project."""

import json
import os
from functools import wraps
from urllib.parse import quote

import requests
from jupyterhub.services.auth import HubOAuth

import gitlab
from flask import Flask, Response, abort, make_response, redirect, request

SERVICE_PREFIX = os.environ.get('JUPYTERHUB_SERVICE_PREFIX', '/')
"""Service prefix is set by JupyterHub service spawner."""

auth = HubOAuth(
    api_token=os.environ['JUPYTERHUB_API_TOKEN'],
    cache_max_age=60,
)
"""Wrap JupyterHub authentication service API."""

app = Flask(__name__)


def _server_name(namespace, project, environment_slug):
    """Return a name for Jupyter server."""
    return '{0}-{1}-{2}'.format(namespace, project, environment_slug)


def authenticated(f):
    """Decorator for authenticating with the Hub"""

    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get(auth.cookie_name)
        if token:
            user = auth.user_for_token(token)
        else:
            user = None
        if user:
            return f(user, *args, **kwargs)
        else:
            # redirect to login url on failed auth
            state = auth.generate_state(next_url=request.path)
            response = make_response(
                redirect(auth.login_url + '&state=%s' % state))
            response.set_cookie(auth.state_cookie_name, state)
            return response

    return decorated


@app.route(SERVICE_PREFIX)
@authenticated
def whoami(user):
    """Return information about the authenticated user."""
    return Response(
        json.dumps(user, indent=1, sort_keys=True),
        mimetype='application/json',
    )


@app.route(
    SERVICE_PREFIX + '<namespace>/<project>/<environment_slug>', methods=['GET'])
@app.route(
    SERVICE_PREFIX + '<namespace>/<project>/<environment_slug>/<path:notebook>',
    methods=['GET'])
@authenticated
def launch_notebook(user, namespace, project, environment_slug, notebook=None):
    """Launch user server with a given name."""
    server_name = _server_name(namespace, project, environment_slug)
    headers = {auth.auth_header_name: 'token {0}'.format(auth.api_token)}
    # 1. launch using spawner that checks the access
    r = requests.request(
        'POST',
        auth.api_url + '/users/{user[name]}/servers/{server_name}'.format(
            user=user, server_name=server_name),
        json={
            'namespace': namespace,
            'project': project,
            'environment_slug': environment_slug,
            'notebook': notebook,
        },
        headers=headers,
    )

    # 2. redirect to launched server
    if r.status_code not in {201, 400}:
        abort(r.status_code)

    notebook_url = auth.hub_host + '/user/{user[name]}/{server_name}/'.format(
        user=user, server_name=server_name)

    if notebook:
        notebook_url += '/notebooks/{notebook}'.format(notebook=notebook)

    return redirect(notebook_url)


@app.route(
    SERVICE_PREFIX + '<namespace>/<project>/<environment_slug>',
    methods=['DELETE'])
@app.route(
    SERVICE_PREFIX + '<namespace>/<project>/<environment_slug>/<path:notebook>',
    methods=['DELETE'])
@authenticated
def stop_notebook(user, namespace, project, environment_slug, notebook=None):
    """Launch user server with name."""
    server_name = _server_name(namespace, project, environment_slug)
    headers = {'Authorization': 'token %s' % auth.api_token}

    r = requests.request(
        'DELETE',
        auth.api_url + '/users/{user[name]}/servers/{server_name}'.format(
            user=user, server_name=server_name),
        headers=headers)
    return app.response_class(
        r.content, status=r.status_code)


@app.route(SERVICE_PREFIX + 'oauth_callback')
def oauth_callback():
    """Set a token in the cookie."""
    code = request.args.get('code', None)
    if code is None:
        return 403

    # validate state field
    arg_state = request.args.get('state', None)
    cookie_state = request.cookies.get(auth.state_cookie_name)
    if arg_state is None or arg_state != cookie_state:
        # state doesn't match
        return 403

    token = auth.token_for_code(code)
    app.logger.info(token)
    next_url = auth.get_next_url(cookie_state) or SERVICE_PREFIX
    response = make_response(redirect(next_url))
    response.set_cookie(auth.cookie_name, token)
    return response