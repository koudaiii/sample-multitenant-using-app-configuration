"""Exercise lifecycle scripts without creating or deleting Azure resources.

The CLI double checks arguments and models groups, outputs and stored values.
These are orchestration tests, not proof of Azure deployment/RBAC behavior.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUBSCRIPTION = '11111111-1111-1111-1111-111111111111'

MOCK_AZ = '''#!/usr/bin/env python3
import json, os, pathlib, sys
a = sys.argv[1:]
path = pathlib.Path(os.environ['MOCK_CLOUD'])
d = json.loads(path.read_text()) if path.exists() else {'groups': {}, 'calls': [], 'values': {}}
d['calls'].append(a)
def save(): path.write_text(json.dumps(d))
def arg(k): return a[a.index(k)+1]
save()
assert arg('--subscription') == '11111111-1111-1111-1111-111111111111'
if a[:2] == ['account','show']: print(arg('--subscription'))
elif a[:3] == ['ad','signed-in-user','show']: print('22222222-2222-2222-2222-222222222222')
elif a[:2] == ['group','exists']: print(str(arg('--name') in d['groups']).lower())
elif a[:2] == ['group','create']:
    name = arg('--name')
    tags = dict(x.split('=',1) for x in a[a.index('--tags')+1:a.index('-o')])
    d['groups'][name] = {'id': '/subscriptions/'+arg('--subscription')+'/resourceGroups/'+name, 'tags': tags}
    save()
elif a[:2] == ['group','show']: print(json.dumps(d['groups'][arg('--name')]))
elif a[:2] == ['group','delete']:
    if os.environ.get('MOCK_DELETE_FAIL'): sys.exit(1)
    del d['groups'][arg('--name')]; save()
elif a[:3] == ['deployment','group','create']:
    if os.environ.get('MOCK_DEPLOY_FAIL'): sys.exit(1)
    params = dict(x.split('=',1) for x in a[a.index('--parameters')+1:a.index('--query')])
    assert params['readerPrincipalType'] == 'User'
    assert len(params['runId']) == 32
    def v(x): return {'value': x}
    print(json.dumps({'sharedStoreName':v('shared'), 'endpoint':v('https://shared.azconfig.io'),
      'sharedEndpoint':v('https://shared.azconfig.io'),
      'tenantEndpoints':v([{'tenantId':t,'endpoint':'https://'+t+'.azconfig.io'} for t in ['tenant-a','tenant-b']]),
      'tenantStoreNames':v([{'tenantId':t,'name':t} for t in ['tenant-a','tenant-b']])}))
elif a[:3] == ['role','assignment','create']: print('/subscriptions/'+arg('--subscription')+'/roleAssignments/writer')
elif a[:3] == ['role','assignment','delete']: assert arg('--ids').endswith('/writer')
elif a[:3] == ['appconfig','kv','set']:
    label = arg('--label') if '--label' in a else ''
    d['values'][arg('--name')+'|'+arg('--key')+'|'+label] = arg('--value'); save()
elif a[:3] == ['appconfig','snapshot','create']:
    assert '--store-name' not in a
    filters = [json.loads(x) for x in a[a.index('--filters')+1:a.index('-o')]]
    assert all(isinstance(f,dict) and f['key'].startswith('tenant-a/') for f in filters)
    assert arg('--snapshot-name').startswith('tenant-a-')
else: raise AssertionError(a)
'''


@pytest.fixture
def scripts(tmp_path):
    repo = tmp_path / 'repo with spaces'
    repo.mkdir()
    shutil.copytree(ROOT / 'script', repo / 'script')
    shutil.copytree(ROOT / 'src', repo / 'src')
    for sample in (ROOT / 'samples').glob('0[1-4]-*'):
        (repo / 'samples' / sample.name).mkdir(parents=True)
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    (binaries / 'az').write_text(MOCK_AZ)
    (binaries / 'uv').write_text('#!/usr/bin/env bash\nexit 0\n')
    for p in binaries.iterdir():
        p.chmod(0o755)
    python = repo / '.venv/bin/python'
    python.parent.mkdir(parents=True)
    python.write_text('#!/usr/bin/env python3\nimport json,os,sys\nprint(json.dumps({"argv":sys.argv[1:], "env":{k:v for k,v in os.environ.items() if k.startswith("APPCONFIG_")}}))\n')
    python.chmod(0o755)
    env = dict(os.environ, PATH=f'{binaries}:{os.environ["PATH"]}',
               MOCK_CLOUD=str(tmp_path / 'cloud.json'))

    def run(name, *args, **extra):
        # Deliberately execute outside the repository, with spaces in its path.
        return subprocess.run([str(repo / 'script' / name), *args], cwd=tmp_path,
                              env=dict(env, **extra), text=True, capture_output=True)

    def cloud():
        return json.loads(Path(env['MOCK_CLOUD']).read_text())

    return repo, run, cloud


def bootstrap(run, sample='01', azure=False, **extra):
    args = ['--sample', sample]
    if azure:
        args += ['--azure', '--subscription', SUBSCRIPTION]
    result = run('bootstrap', *args, **extra)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_repeated_bootstrap_creates_independent_groups_and_cleanup_is_scoped(scripts):
    repo, run, cloud = scripts
    first = bootstrap(run, azure=True)
    second = bootstrap(run, azure=True)
    assert first != second
    assert len(cloud()['groups']) == 2
    result = run('cleanup', '--run', first)
    assert result.returncode == 0, result.stderr
    assert list(cloud()['groups']) == [f'rg-mtappconfig-01-{second}']
    calls = len(cloud()['calls'])
    assert run('cleanup', '--run', first).returncode == 0
    assert len(cloud()['calls']) == calls
    assert run('server', '--run', first).returncode != 0


def test_cleanup_refuses_a_group_with_different_ownership(scripts):
    repo, run, cloud = scripts
    run_id = bootstrap(run, azure=True)
    path = repo.parent / 'cloud.json'
    d = cloud()
    next(iter(d['groups'].values()))['tags']['mtappconfig-run'] = 'someone-else'
    path.write_text(json.dumps(d))
    result = run('cleanup', '--run', run_id)
    assert result.returncode != 0
    assert 'Ownership check failed' in result.stderr
    assert len(cloud()['groups']) == 1


def test_failed_deployment_leaves_enough_state_for_cleanup(scripts):
    repo, run, cloud = scripts
    result = run('bootstrap', '--azure', '--subscription', SUBSCRIPTION, MOCK_DEPLOY_FAIL='1')
    assert result.returncode != 0
    states = list((repo / '.runs').glob('*/state.json'))
    assert len(states) == 1
    state = json.loads(states[0].read_text())
    assert state['status'] == 'preparing'
    assert state['run_id'] in result.stderr
    assert run('cleanup', '--run', state['run_id']).returncode == 0
    assert not cloud()['groups']


def test_failed_cleanup_can_be_retried(scripts):
    repo, run, cloud = scripts
    run_id = bootstrap(run, azure=True)
    assert run('cleanup', '--run', run_id, MOCK_DELETE_FAIL='1').returncode != 0
    state = json.loads((repo / '.runs' / run_id / 'state.json').read_text())
    assert state['status'] == 'deleting'
    assert run('cleanup', '--run', run_id).returncode == 0
    assert not cloud()['groups']


@pytest.mark.parametrize('sample', ['01', '02', '03', '04'])
def test_azure_seed_layout_and_server_endpoints(scripts, sample):
    repo, run, cloud = scripts
    run_id = bootstrap(run, sample, azure=True)
    values = cloud()['values']
    if sample in ('01', '04'):
        assert values['shared|tenant-a/DatabaseName|'] == 'db-tenant-a'
    elif sample == '02':
        assert values['shared|DatabaseName|tenant-a'] == 'db-tenant-a'
    else:
        assert values['tenant-a|DatabaseName|'] == 'db-tenant-a'
    if sample == '04':
        assert values['shared|tenant-a/LogLevel|'] == 'Warning'
        assert json.loads(values['shared|tenant-b/RolloutSnapshot|'])['snapshot_name'] == 'tenant-b-missing'
        assert len([a for a in cloud()['calls'] if a[:3] == ['appconfig', 'snapshot', 'create']]) == 2
    result = run('server', '--run', run_id, '--port', '5104')
    assert result.returncode == 0, result.stderr
    server = json.loads(result.stdout)
    assert '5104' in server['argv']
    if sample == '03':
        assert json.loads(server['env']['APPCONFIG_ENDPOINTS'])['tenant-b'] == 'https://tenant-b.azconfig.io'
        assert 'APPCONFIG_ENDPOINT' not in server['env']
    else:
        assert server['env'] == {'APPCONFIG_ENDPOINT': 'https://shared.azconfig.io'}
    assert any(a[:3] == ['role', 'assignment', 'delete'] for a in cloud()['calls'])


def test_local_server_ignores_inherited_azure_endpoints(scripts):
    repo, run, cloud = scripts
    run_id = bootstrap(run, '04')
    result = run('server', '--run', run_id, APPCONFIG_ENDPOINT='https://wrong.azconfig.io',
                 APPCONFIG_ENDPOINTS='{}', APPCONFIG_SHARED_ENDPOINT='wrong')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['env'] == {}
    assert run('cleanup', '--run', run_id).returncode == 0
    assert not (repo.parent / 'cloud.json').exists()


def test_scripts_reject_invalid_arguments_and_metadata(scripts):
    repo, run, cloud = scripts
    assert run('bootstrap', '--sample', '05').returncode != 0
    assert run('server', '--run', '../../other').returncode != 0
    assert run('cleanup', '--run').returncode != 0
    run_id = bootstrap(run)
    assert run('server', '--run', run_id, '--port', '0').returncode != 0
    state_path = repo / '.runs' / run_id / 'state.json'
    state = json.loads(state_path.read_text())
    state['resource_group'] = 'production'
    state_path.write_text(json.dumps(state))
    assert run('cleanup', '--run', run_id).returncode != 0
