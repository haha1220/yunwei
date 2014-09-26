import re
import os
from functools import update_wrapper

import click
import requests
import subprocess
import libmetaflask

from flask import Flask, request, abort, jsonify
from werkzeug.urls import url_join, url_quote


app = Flask(__name__)
app.config.update(
    ACCESS_TOKEN='add me',
    MEMBER_TEAM_ID='899232',
    METAFLASK_REPO='pocoo/metaflask',
    METAFLASK_MEMBERS_FOLDER='members',
    HOOK_SECRET='5aedb4c4-b1ae-4b66-8426-d641054f9102',
    API_BASE_URL='https://api.github.com/',
    LOCAL_CHECKOUT='checkout',
)
app.config.from_pyfile('localconfig.py', silent=True)
app.config['LOCAL_CHECKOUT'] = os.path.join(os.path.dirname(__file__),
                                            app.config['LOCAL_CHECKOUT'])

_member_fn_re = re.compile(r'^(\d{4})_(.*?)\.txt$')


def require_hook_secret(f):
    def new_func(*args, **kwargs):
        secret = request.args.get('secret')
        if secret != app.config['HOOK_SECRET']:
            abort(401)
        return f(*args, **kwargs)
    return update_wrapper(new_func, f)


def api_endpoint(f):
    f.__is_api_endpoint__ = True
    def new_func(*args, **kwargs):
        return jsonify(f(*args, **kwargs))
    return update_wrapper(new_func, f)


def github_api_request(method, url, *args, **kwargs):
    kwargs['auth'] = (app.config['ACCESS_TOKEN'], 'x-oauth-basic')
    url = url_join(app.config['API_BASE_URL'], url)
    return requests.request(url=url, method=method, *args, **kwargs)


def get_metaview(sync=False):
    if sync:
        sync_local_repo()
    return libmetaflask.MetaView(app.config['LOCAL_CHECKOUT'])


def add_member(username):
    github_api_request('PUT', 'teams/%s/memberships/%s' % (
        app.config['MEMBER_TEAM_ID'],
        url_quote(username),
    )).raise_for_status()


def remove_member(username):
    github_api_request('DELETE', 'teams/%s/members/%s' % (
        app.config['MEMBER_TEAM_ID'],
        url_quote(username),
    )).raise_for_status()


def member_is_pending(username):
    rv = github_api_request('GET', 'teams/%s/memberships/%s' % (
        app.config['MEMBER_TEAM_ID'],
        url_quote(username),
    ))
    if rv.status_code == 404:
        return False
    return rv.json().get('state') == 'pending'


def get_current_members():
    rv = github_api_request('GET', 'teams/%s/members' % (
        app.config['MEMBER_TEAM_ID'],
    ))
    rv.raise_for_status()
    return [x['login'] for x in rv.json()]


def get_intended_members(metaview):
    members = []
    for member in metaview.iter_members():
        members.append((member.num, member.github))

    members.sort()
    return [x[1] for x in members]


def sync_members(metaview):
    current_members = set(get_current_members())
    intended_members = set(get_intended_members(metaview))

    new_members = set()
    for member in intended_members:
        if member not in current_members:
            if member_is_pending(member):
                yield 'pending', member
            else:
                add_member(member)
                yield 'added', member
        else:
            yield 'retained', member
        new_members.add(member)

    for member in current_members - new_members:
        remove_member(member)
        yield 'deleted', member


def sync_projects(metaview):
    for project in metaview.iter_projects():
        project.sync()
        yield project.internal_name


def git(*args, **extra):
    """Runs a git command."""
    extra.setdefault('cwd', app.config['LOCAL_CHECKOUT'])
    stdout = stderr = None
    if extra.pop('capture_stdout', True):
        stdout = subprocess.PIPE
    if extra.pop('capture_stderr', True):
        stderr = subprocess.PIPE
    return subprocess.Popen(['git'] + list(args),
                            stdout=stdout,
                            stderr=stderr,
                            **extra).communicate()


def sync_local_repo(**extra):
    """Updates the local git repo or checks it out."""
    if not os.path.isdir(app.config['LOCAL_CHECKOUT']):
        dirname, reponame = os.path.split(app.config['LOCAL_CHECKOUT'])
        git('clone', 'https://github.com/%s' % app.config['METAFLASK_REPO'],
            reponame, cwd=dirname, **extra)
    return git('pull', **extra)


@app.route('/')
@api_endpoint
def index():
    """Shows all available APIs."""
    rv = {}

    for rule in app.url_map.iter_rules():
        func = app.view_functions.get(rule.endpoint)
        if func is None:
            continue
        if getattr(func, '__is_api_endpoint__', False):
            name = rule.endpoint
            if name.endswith('_api'):
                name = name[:-4]
            rv[name] = {
                'url': rule.rule,
                'doc': (func.__doc__ or '').decode('utf-8'),
            }

    return {
        'endpoints': rv,
    }


@app.route('/members/')
@api_endpoint
def list_members_api():
    """Returns a list of all members."""
    metaview = get_metaview()
    members = [x.to_json(compact=True) for x in metaview.iter_members()]
    return {
        'members': members,
    }


@app.route('/members/<id>')
@api_endpoint
def get_member_api(id):
    """Returns detailed info about a member."""
    metaview = get_metaview()
    member = metaview.members_by_id.get(id)
    if member is None:
        abort(404)
    return member.to_json()


@app.route('/membertree')
@api_endpoint
def get_member_tree_api():
    """Returns all members as a tree by sponsorship."""
    metaview = get_metaview()
    links = {}
    for member in metaview.iter_members():
        links.setdefault(member.sponsor, []).append(member)

    def _make_tree(sponsor):
        return {
            'sponsor': sponsor and sponsor.to_json(compact=True) or None,
            'sponsored': [_make_tree(x) for x in links.get(sponsor) or ()],
        }

    return _make_tree(None)


@app.route('/projects/')
@api_endpoint
def list_projects_api():
    """Returns a list of all projects."""
    metaview = get_metaview()
    projects = [x.to_json(compact=True) for x in metaview.iter_projects()]
    return {
        'projects': projects,
    }


@app.route('/needs-stewards')
@api_endpoint
def list_needs_stewards_api():
    """Returns a list of all projects that don't have stewards."""
    metaview = get_metaview()
    projects = [x.to_json(compact=True) for x in metaview.iter_projects()
                if not x.has_stewards]
    return {
        'projects': projects,
    }


@app.route('/extensions')
@api_endpoint
def list_extensions_api():
    """Returns an overview of all projects that are extensions."""
    metaview = get_metaview()
    extensions = [x.to_json(compact=True) for x in metaview.iter_extensions()]
    return {
        'extensions': extensions,
    }


@app.route('/projects/<short_name>')
@api_endpoint
def get_project_api(short_name):
    """Return details about a project."""
    metaview = get_metaview()
    project = metaview.projects.get(short_name)
    if project is None:
        abort(404)
    return project.to_json()


@app.route('/sync', methods=['POST'])
@require_hook_secret
def sync_api():
    metaview = get_metaview(sync=True)
    return jsonify(
        member_changes=list(sync_members(metaview)),
        project_changes=list(sync_projects(metaview)),
    )


@app.cli.group('sync')
def sync_cmd():
    """Synchronizes things for metaflask."""


@sync_cmd.command('git')
def sync_git_cmd():
    """Synchronizes the local git repo."""
    click.echo('Synchronizing git repo')
    sync_local_repo(capture_stdout=False,
                    capture_stderr=False)
    click.echo('Done.')


@sync_cmd.command('members')
def sync_members_cmd():
    """Synchronizes all members."""
    click.echo('Synchronizing members')
    for op, member in sync_members(get_metaview(sync=True)):
        click.echo('  %s %s' % (
            click.style(op, fg={
                'added': 'green',
                'retained': 'cyan',
                'pending': 'cyan',
                'deleted': 'red',
            }[op]),
            member,
        ))
    click.echo('Done.')


@sync_cmd.command('projects')
def sync_projects_cmd():
    """Synchronizes all project meta info."""
    click.echo('Synchronizing projects')
    for project in sync_projects(get_metaview(sync=True)):
        click.echo('  %s %s' % (
            click.style('updated', fg='cyan'),
            project,
        ))
    click.echo('Done.')
